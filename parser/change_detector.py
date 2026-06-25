"""文件变更检测

支持两种检测方式:
  1. git diff（默认）：通过 git diff --name-status 获取变更文件
  2. 手动指定：--files 参数直接给文件列表

路径转换链:
  git_path (相对仓库根) → abs_path = repo_root / git_path
  abs_path → db_rel_path = config.make_relative_path(abs_path)

注意: 项目用 Google repo 工具管理，每个子模块是独立 git 仓库。
hq_ota_service 所在的 ap-aa 是独立 git 仓库，需从 source_paths 向上查找 .git。
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import ProjectConfig

logger = logging.getLogger(__name__)


@dataclass
class FileChange:
    """单个文件变更"""
    abs_path: str          # 绝对路径（compile_commands.file 格式）
    db_rel_path: str       # DB 相对路径（node.file_path / include_dep.source_file 格式）
    git_path: str          # git diff 返回的相对路径
    status: str            # "M" / "A" / "D" / "R"（重命名）
    is_source: bool        # .cpp/.cxx/.cc/.c
    is_header: bool        # .h/.hpp/.hxx


@dataclass
class FileChangeSet:
    """文件变更集合"""
    modified: list[FileChange] = field(default_factory=list)  # status M
    added: list[FileChange] = field(default_factory=list)     # status A
    deleted: list[FileChange] = field(default_factory=list)  # status D

    @property
    def all_changed(self) -> list[FileChange]:
        """所有变更文件"""
        return self.modified + self.added + self.deleted

    @property
    def reparse_files(self) -> list[FileChange]:
        """需要重新解析的文件（modified + added，含 .h 和 .cpp）"""
        return self.modified + self.added

    @property
    def is_empty(self) -> bool:
        return not self.all_changed


class ChangeDetector:
    """文件变更检测"""

    def __init__(self, repo_root: str | None, project_config: ProjectConfig):
        """初始化

        Args:
            repo_root: git 仓库根目录（None 时从配置推断）
            project_config: 项目配置
        """
        self.repo_root = Path(repo_root) if repo_root else None
        self.config = project_config

    def detect_from_git(self, base_ref: str = "HEAD~1") -> FileChangeSet:
        """通过 git diff 检测变更文件

        Args:
            base_ref: git diff 基准 ref（默认 HEAD~1）

        Returns:
            FileChangeSet
        """
        repo_root = self._ensure_repo_root()
        if not repo_root:
            logger.warning("无法确定 git 仓库根，git diff 检测失败")
            return FileChangeSet()

        raw = self._run_git_diff(str(repo_root), base_ref)
        changes: list[FileChange] = []
        for status_letter, git_path in raw:
            fc = self._convert_git_path(git_path, status_letter, repo_root)
            if fc:
                changes.append(fc)
        return self._partition(changes)

    def detect_from_files(self, files: list[str]) -> FileChangeSet:
        """手动指定文件列表（不依赖 git）

        Args:
            files: 文件路径列表（绝对或相对路径均可）

        Returns:
            FileChangeSet（全部标记为 M）
        """
        changes: list[FileChange] = []
        for f in files:
            fc = self._convert_abs_path(f, "M")
            if fc:
                changes.append(fc)
        return self._partition(changes)

    # ------------------------------------------------------------------

    def _ensure_repo_root(self) -> Path | None:
        """确定 git 仓库根目录

        优先用构造时传入的 repo_root；否则从 source_paths 向上查找 .git。
        """
        if self.repo_root and self.repo_root.exists():
            return self.repo_root

        # 从 source_paths 的实际路径向上找 .git
        for sp in self.config.source_paths:
            # source_paths 形如 "hq_ota_service/src"，需结合 compile_commands 所在路径
            # compile_commands 是绝对路径，其父目录通常是项目根
            cc_dir = Path(self.config.compile_commands).parent.parent
            candidate = cc_dir
            for _ in range(6):
                if (candidate / ".git").exists():
                    return candidate
                if candidate.parent == candidate:
                    break
                candidate = candidate.parent
        return None

    def _run_git_diff(self, repo_root: str, base_ref: str) -> list[tuple[str, str]]:
        """执行 git diff，返回 (status, path) 列表

        git diff --name-status 输出格式:
          M\tapp/hq_ota_service/src/soc_update.cpp
          A\tapp/hq_ota_service/include/new_header.h
          D\tapp/hq_ota_service/src/old_file.cpp
          R100\told.cpp\tnew.cpp   （重命名）

        需设置 GIT_DISCOVERY_ACROSS_FILESYSTEM=1（跨挂载点）。
        """
        env = {**os.environ, "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1"}
        try:
            result = subprocess.run(
                ["git", "-C", repo_root, "diff", "--name-status", base_ref],
                capture_output=True, text=True, env=env, timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error("git diff 执行失败: %s", e)
            return []

        if result.returncode != 0:
            logger.warning("git diff 返回非零: %s", result.stderr.strip())
            return []

        entries: list[tuple[str, str]] = []
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status_letter = parts[0][0]  # R100 → R, M → M
            if status_letter == "R" and len(parts) >= 3:
                # 重命名格式: R100\told_path\tnew_path
                # 拆成"删除旧路径 + 新增新路径"，避免旧路径节点/边残留
                old_path = parts[1]
                new_path = parts[2]
                entries.append(("D", old_path))   # 删除旧路径
                entries.append(("A", new_path))   # 新增新路径
            else:
                git_path = parts[-1]
                entries.append((status_letter, git_path))
        return entries

    def _convert_git_path(self, git_path: str, status: str,
                          repo_root: Path) -> FileChange | None:
        """git diff 路径 → FileChange"""
        abs_path = str(repo_root / git_path)
        return self._convert_abs_path(abs_path, status, git_path)

    def _convert_abs_path(self, path: str, status: str,
                          git_path: str = "") -> FileChange | None:
        """绝对路径 → FileChange"""
        abs_path = str(Path(path).resolve()) if not Path(path).is_absolute() else path

        # 只处理项目源码和生成代码
        if not (self.config.is_project_source(abs_path)
                or self.config.is_generated(abs_path)):
            return None

        db_rel = self.config.make_relative_path(abs_path)
        if not db_rel:
            return None

        ext = abs_path.lower()
        return FileChange(
            abs_path=abs_path,
            db_rel_path=db_rel,
            git_path=git_path or abs_path,
            status=status,
            is_source=ext.endswith((".cpp", ".cxx", ".cc", ".c")),
            is_header=ext.endswith((".h", ".hpp", ".hxx")),
        )

    @staticmethod
    def _partition(changes: list[FileChange]) -> FileChangeSet:
        """按 status 分组"""
        cs = FileChangeSet()
        for fc in changes:
            if fc.status == "D":
                cs.deleted.append(fc)
            elif fc.status == "A":
                cs.added.append(fc)
            else:  # M / R
                cs.modified.append(fc)
        return cs
