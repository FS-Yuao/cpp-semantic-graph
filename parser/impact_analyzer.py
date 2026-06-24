"""影响范围分析

基于 include_dep 表分析文件变更的影响范围:
  - .cpp 变更 → 受影响 TU = [该 .cpp]
  - .h 变更   → 受影响 TU = 递归查所有直接+间接 includer
  - 删除文件  → 先查 includer 再标记删除

复用 IncludeQuery.get_all_includers() 做 .h 的递归 includer 查找。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import ProjectConfig
from .compile_db import CompileDB
from .change_detector import FileChangeSet
from ..query.include_query import IncludeQuery

logger = logging.getLogger(__name__)


@dataclass
class ImpactEntry:
    """一个受影响的翻译单元"""
    tu_abs_path: str          # 绝对路径（compile_commands.file 格式）
    tu_db_rel: str            # DB 相对路径
    reason: str               # "direct .cpp change" / "includes changed header: X"
    trigger_files: list[str] = field(default_factory=list)  # 触发文件 (db_rel_path)


@dataclass
class ImpactReport:
    """影响范围分析报告"""
    affected_tus: list[ImpactEntry] = field(default_factory=list)  # 需重新解析的 TU
    deleted_db_paths: list[str] = field(default_factory=list)       # 物理删除文件 (db_rel)
    new_tu_paths: list[str] = field(default_factory=list)           # 新增 TU (abs)
    impact_chain: dict[str, list[str]] = field(default_factory=dict)  # file → [tu_db_rel]
    skipped: list[str] = field(default_factory=list)                # 跳过的文件


class ImpactAnalyzer:
    """影响范围分析"""

    def __init__(self, db_path: str, project_config: ProjectConfig,
                 compile_db: CompileDB):
        self.db_path = db_path
        self.config = project_config
        self.compile_db = compile_db

    def analyze(self, changes: FileChangeSet) -> ImpactReport:
        """分析文件变更的影响范围

        Args:
            changes: 文件变更集合

        Returns:
            ImpactReport
        """
        affected: dict[str, ImpactEntry] = {}  # tu_db_rel → ImpactEntry
        deleted_paths: list[str] = []
        new_tus: list[str] = []
        skipped: list[str] = []
        impact_chain: dict[str, list[str]] = {}

        # --- 处理修改/新增文件 ---
        for fc in changes.reparse_files:
            if fc.is_source:
                # .cpp 变更：TU = 该 .cpp 本身
                entry = self.compile_db.get_entry_for_file(fc.abs_path)
                if entry:
                    tu_rel = self.config.make_relative_path(entry.file)
                    affected.setdefault(tu_rel, ImpactEntry(
                        tu_abs_path=entry.file,
                        tu_db_rel=tu_rel,
                        reason="direct .cpp change",
                        trigger_files=[fc.db_rel_path],
                    ))
                    if fc.status == "A":
                        new_tus.append(entry.file)
                else:
                    skipped.append(f"{fc.abs_path} (no compile_commands entry)")

            elif fc.is_header:
                # .h 变更：递归查所有 includer
                includers = self._find_affected_tus_for_header(fc.db_rel_path)
                impact_chain[fc.db_rel_path] = includers
                if not includers:
                    logger.warning("头文件 %s 未找到 includer（可能 include_dep 表不完整）",
                                   fc.db_rel_path)
                for tu_rel in includers:
                    tu_abs = self._resolve_tu_abs_path(tu_rel)
                    if not tu_abs:
                        continue
                    existing = affected.get(tu_rel)
                    if existing:
                        existing.trigger_files.append(fc.db_rel_path)
                    else:
                        affected[tu_rel] = ImpactEntry(
                            tu_abs_path=tu_abs,
                            tu_db_rel=tu_rel,
                            reason=f"includes changed header: {fc.db_rel_path}",
                            trigger_files=[fc.db_rel_path],
                        )

        # --- 处理删除文件 ---
        for fc in changes.deleted:
            if fc.is_header:
                # 删除前先查 includer（include_dep 记录还在）
                includers = self._find_affected_tus_for_header(fc.db_rel_path)
                impact_chain[fc.db_rel_path] = includers
                for tu_rel in includers:
                    tu_abs = self._resolve_tu_abs_path(tu_rel)
                    if tu_abs and tu_rel not in affected:
                        affected[tu_rel] = ImpactEntry(
                            tu_abs_path=tu_abs,
                            tu_db_rel=tu_rel,
                            reason=f"includes deleted header: {fc.db_rel_path}",
                            trigger_files=[fc.db_rel_path],
                        )
            deleted_paths.append(fc.db_rel_path)

        return ImpactReport(
            affected_tus=list(affected.values()),
            deleted_db_paths=deleted_paths,
            new_tu_paths=new_tus,
            impact_chain=impact_chain,
            skipped=skipped,
        )

    def _find_affected_tus_for_header(self, header_db_rel: str) -> list[str]:
        """递归查找所有 include 该头文件的 TU

        复用 IncludeQuery.get_all_includers()。
        传入文件名即可（include_dep.included_file 存的是文件名）。

        Args:
            header_db_rel: 头文件 DB 相对路径（如 'base_peri_update.h'）

        Returns:
            受影响 TU 的 DB 相对路径列表
        """
        header_name = Path(header_db_rel).name
        with IncludeQuery(self.db_path) as q:
            return q.get_all_includers(header_name)

    def _resolve_tu_abs_path(self, tu_db_rel: str) -> str | None:
        """DB 相对路径 → 绝对路径（通过 compile_commands 模糊匹配）

        tu_db_rel 如 'peri_update/soc/soc_update.cpp'，compile_commands 中
        file 以此结尾。get_entry_for_file 已支持后缀匹配。
        """
        entry = self.compile_db.get_entry_for_file(tu_db_rel)
        return entry.file if entry else None
