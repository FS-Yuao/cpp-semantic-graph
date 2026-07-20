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

    def detect_doc_changes(self, base_ref: str = "HEAD~1") -> list[str]:
        """检测 docs 目录下的 .md 文件变更

        通过 git diff --name-only 检测，只返回在 docs_dir 范围内的 .md 文件。

        Args:
            base_ref: git diff 基准 ref（默认 HEAD~1）

        Returns:
            变更的 .md 文件绝对路径列表
        """
        repo_root = self._ensure_repo_root()
        if not repo_root:
            logger.warning("无法确定 git 仓库根，文档变更检测失败")
            return []

        docs_dir = self.config.docs_dir
        if not docs_dir:
            logger.info("项目配置中未设置 docs_dir，跳过文档变更检测")
            return []

        # 解析 docs_dir 为绝对路径
        docs_path = Path(docs_dir)
        if not docs_path.is_absolute():
            # 相对于项目配置文件所在目录解析
            config_dir = Path(self.config.config_path).parent if hasattr(self.config, 'config_path') and self.config.config_path else repo_root
            docs_path = (config_dir / docs_dir).resolve()

        if not docs_path.exists():
            logger.warning("文档目录不存在: %s", docs_path)
            return []

        # git diff 只看 docs 目录下的 .md 文件
        docs_git_rel = str(docs_path.relative_to(repo_root)) if docs_path.is_relative_to(repo_root) else ""
        if not docs_git_rel:
            # docs_path 不在仓库内，用全量扫描判断
            return self._detect_doc_changes_by_scan(docs_path, base_ref)

        env = {**os.environ, "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1"}
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_root), "diff", "--name-only", base_ref,
                 "--", f"{docs_git_rel}/*.md", f"{docs_git_rel}/**/*.md"],
                capture_output=True, text=True, env=env, timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error("git diff 文档变更检测失败: %s", e)
            return []

        if result.returncode != 0:
            logger.warning("git diff 文档变更检测返回非零: %s", result.stderr.strip())
            return []

        changed_docs: list[str] = []
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            abs_path = str(repo_root / line.strip())
            if abs_path.lower().endswith(".md") and Path(abs_path).exists():
                changed_docs.append(abs_path)

        return changed_docs

    def _detect_doc_changes_by_scan(self, docs_path: Path, base_ref: str) -> list[str]:
        """当 docs_path 不在仓库内时，通过文件修改时间判断变更

        返回最近 24 小时内修改的 .md 文件。
        """
        import time
        cutoff = time.time() - 86400  # 24 小时
        changed: list[str] = []
        for md_file in docs_path.rglob("*.md"):
            if md_file.stat().st_mtime >= cutoff:
                changed.append(str(md_file))
        return changed

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

        优先用构造时传入的 repo_root；否则从 compile_commands 所在目录
        向上查找有效的 .git 仓库根。

        有效性校验：.git 须含 HEAD（标准仓库/bare 仓库/repo 工具的符号链接
        均含 HEAD），以此排除损坏的空 .git 目录（如顶层 adc4.0/.git），
        避免误判为仓库根导致 git diff 失败、增量检测返回空。

        注：compile_commands 通常位于仓库根（ap-aa/compile_commands.json），
        故从其所在目录开始查；此前用 parent.parent 会跳过仓库根本身。
        """
        if self.repo_root and self.repo_root.exists():
            return self.repo_root

        candidate = Path(self.config.compile_commands).parent
        for _ in range(8):
            git_dir = candidate / ".git"
            if git_dir.exists():
                # .git 可能是目录(标准/bare 仓库)、符号链接(repo 工具子模块)、
                # 或 file(gitdir 指针)。三者都含 HEAD；空 .git 目录无 HEAD，跳过。
                if (git_dir / "HEAD").exists() or git_dir.is_file():
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
