"""增量更新编排器

基于 include 依赖图的增量解析：文件变更时自动确定影响范围，
仅重新解析受影响的翻译单元，无需全量重跑。

流程:
  1. ChangeDetector：检测文件变更（git diff 或手动指定）
  2. ImpactAnalyzer：分析影响范围（.h → 递归 includer）
  3. 删除旧数据：删出边（不删共享节点）+ include_dep + parse_status
  4. 重新解析受影响 TU（复用 SemanticExtractor）
  5. upsert 新数据（importer 已支持节点更新/边去重）
  6. 清理残留节点（文件中已删除的函数/类）
  7. 重建文档关联（content_scan，可选 embedding）

删除策略核心：只删出边（from_id 在该文件的边），保留入边；
节点用 upsert 更新，不删除（除非文件物理删除或节点从源码消失）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .parser.config import ProjectConfig
from .parser.compile_db import CompileDB
from .parser.ast_visitor import SemanticExtractor
from .parser.change_detector import ChangeDetector, FileChangeSet, FileChange
from .parser.impact_analyzer import ImpactAnalyzer, ImpactEntry, ImpactReport
from .parser.models import ParseResult
from .db.graph_db import GraphDB

logger = logging.getLogger(__name__)


@dataclass
class IncrementalReport:
    """增量更新报告"""
    files_changed: int = 0
    tus_affected: int = 0
    tus_reparsed: int = 0
    tus_failed: int = 0
    failed_files: list[str] = field(default_factory=list)
    nodes_new: int = 0
    nodes_updated: int = 0
    edges_deleted: int = 0
    edges_new: int = 0
    edges_skipped: int = 0
    includes_deleted: int = 0
    includes_new: int = 0
    nodes_removed: int = 0
    associations_rebuilt: bool = False
    elapsed_seconds: float = 0.0
    db_node_count: int = 0
    db_edge_count: int = 0
    # dry-run 诊断信息
    impact_chain: dict[str, list[str]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


class IncrementalUpdater:
    """增量更新主编排器"""

    def __init__(self, config_path: str, db_path: str,
                 repo_root: str | None = None):
        """初始化

        Args:
            config_path: cpp_semantic_graph.yaml 路径
            db_path: 图谱数据库路径
            repo_root: git 仓库根（None 时从配置推断）
        """
        self.config = ProjectConfig.from_yaml(config_path)
        self.config_path = config_path
        self.db_path = db_path
        self.repo_root = repo_root
        self.compile_db = CompileDB(self.config.compile_commands)

    def run(self, *,
            base_ref: str | None = "HEAD~1",
            files: list[str] | None = None,
            rebuild_associations: bool = True,
            rebuild_embeddings: bool = False,
            dry_run: bool = False) -> IncrementalReport:
        """执行增量更新

        Args:
            base_ref: git diff 基准 ref（None 且 files 为空时默认 HEAD~1）
            files: 手动指定文件列表（覆盖 base_ref）
            rebuild_associations: 是否重建文档关联边
            rebuild_embeddings: 是否重建 embedding 关联（慢）
            dry_run: 只检测+分析，不执行删除/解析

        Returns:
            IncrementalReport
        """
        t0 = time.time()
        report = IncrementalReport()

        # --- 1. 检测变更 ---
        detector = ChangeDetector(self.repo_root, self.config)
        if files:
            changes = detector.detect_from_files(files)
        else:
            changes = detector.detect_from_git(base_ref or "HEAD~1")
        report.files_changed = len(changes.all_changed)

        if changes.is_empty:
            logger.info("无文件变更")
            report.elapsed_seconds = time.time() - t0
            return report

        logger.info("变更文件: %d (M=%d, A=%d, D=%d)",
                    len(changes.all_changed),
                    len(changes.modified),
                    len(changes.added),
                    len(changes.deleted))

        # --- 2. 影响分析 ---
        analyzer = ImpactAnalyzer(self.db_path, self.config, self.compile_db)
        impact = analyzer.analyze(changes)
        report.tus_affected = len(impact.affected_tus)
        report.impact_chain = impact.impact_chain
        report.skipped = impact.skipped

        logger.info("受影响 TU: %d, 删除文件: %d",
                    len(impact.affected_tus), len(impact.deleted_db_paths))

        if impact.skipped:
            logger.warning("跳过 %d 个文件: %s",
                           len(impact.skipped), impact.skipped[:5])

        if dry_run:
            report.elapsed_seconds = time.time() - t0
            return report

        if not impact.affected_tus and not impact.deleted_db_paths:
            logger.info("无需重新解析的 TU")
            report.elapsed_seconds = time.time() - t0
            return report

        # --- 3. 删除旧数据 ---
        db = GraphDB(self.db_path)
        del_stats = self._delete_stale_data(changes, impact, db)
        report.edges_deleted = del_stats["edges_deleted"]
        report.includes_deleted = del_stats["includes_deleted"]

        # --- 4. 重新解析 ---
        results = self._reparse_tus(impact.affected_tus)
        for r in results:
            if r.status == "failed":
                report.tus_failed += 1
                report.failed_files.append(r.source_path)
            else:
                report.tus_reparsed += 1

        # --- 5. 导入结果（upsert）---
        import_stats = self._import_results(results, db)
        report.nodes_new = import_stats["nodes_new"]
        report.nodes_updated = import_stats["nodes_updated"]
        report.edges_new = import_stats["edges_new"]
        report.edges_skipped = import_stats["edges_skipped"]
        report.includes_new = import_stats["includes_new"]

        # --- 6. 清理残留节点 ---
        report.nodes_removed = self._cleanup_removed_nodes(
            changes.reparse_files, results, db)

        # --- 7. 重建文档关联 ---
        if rebuild_associations:
            self._rebuild_associations(rebuild_embeddings)
            report.associations_rebuilt = True

        # --- 8. 统计 ---
        db_stats = db.get_stats()
        report.db_node_count = db_stats["node_count"]
        report.db_edge_count = db_stats["edge_count"]
        db.close()

        report.elapsed_seconds = time.time() - t0
        return report

    # ------------------------------------------------------------------
    # 删除旧数据
    # ------------------------------------------------------------------

    def _delete_stale_data(self, changes: FileChangeSet,
                           impact: ImpactReport, db: GraphDB) -> dict:
        """删除旧数据

        策略:
        A. 变更文件的出边（from_id 在该文件的边）
        B. 受影响 TU 的 .cpp 出边 + include_dep + parse_status
        C. 物理删除的文件（节点 + CASCADE 边 + includes + status）

        顺序: A → B → C（C 删除节点后无法再查其出边，但 C 的文件已物理删除，
              include_dep 中关于它的记录也要清，放在最后）
        """
        edges_deleted = 0
        includes_deleted = 0

        # A. 变更文件的出边
        for fc in changes.reparse_files:
            n = db.delete_edges_from_file(fc.db_rel_path)
            edges_deleted += n

        # B. 受影响 TU 的 .cpp 出边 + include_dep + parse_status
        for entry in impact.affected_tus:
            n = db.delete_edges_from_file(entry.tu_db_rel)
            edges_deleted += n
            stats = db.delete_tu_data(entry.tu_db_rel, entry.tu_abs_path)
            includes_deleted += stats["includes_deleted"]

        # C. 物理删除的文件
        for db_rel in impact.deleted_db_paths:
            stats = db.delete_file_completely(db_rel)
            edges_deleted += stats.get("edges_cascaded", 0)
            includes_deleted += stats.get("includes_deleted", 0)

        return {"edges_deleted": edges_deleted,
                "includes_deleted": includes_deleted}

    # ------------------------------------------------------------------
    # 重新解析
    # ------------------------------------------------------------------

    def _reparse_tus(self, affected_tus: list[ImpactEntry]) -> list[ParseResult]:
        """重新解析受影响 TU

        串行模式（max_workers=1）直接用主进程 extractor；
        并行模式复用 pipeline 的 worker 模式。
        """
        entries = []
        for entry_info in affected_tus:
            entry = self.compile_db.get_entry_for_file(entry_info.tu_abs_path)
            if entry:
                entries.append(entry)
            else:
                logger.warning("找不到 compile_commands 条目: %s",
                               entry_info.tu_abs_path)

        if not entries:
            return []

        if self.config.max_workers <= 1:
            extractor = SemanticExtractor(self.config)
            return [extractor.parse(e) for e in entries]

        return self._parse_all_parallel(entries)

    def _parse_all_parallel(self, entries: list) -> list[ParseResult]:
        """并行解析（复用 pipeline.py 的 worker 模式）"""
        from concurrent.futures import ProcessPoolExecutor, as_completed
        from .pipeline import _worker_init, _worker_parse

        results: list[ParseResult] = []
        with ProcessPoolExecutor(
            max_workers=self.config.max_workers,
            initializer=_worker_init,
            initargs=(self.config_path,),
        ) as pool:
            future_map = {
                pool.submit(_worker_parse, e.file, e.args): e for e in entries
            }
            for fut in as_completed(future_map):
                entry = future_map[fut]
                try:
                    results.append(fut.result())
                except Exception as exc:
                    logger.error("解析异常 %s: %s", entry.file, exc)
                    results.append(ParseResult(
                        source_path=entry.file, status="failed",
                        error_message=str(exc),
                    ))
        return results

    # ------------------------------------------------------------------
    # 导入结果
    # ------------------------------------------------------------------

    def _import_results(self, results: list[ParseResult],
                        db: GraphDB) -> dict:
        """导入解析结果（upsert）

        复用 db.import_results()：节点已存在则 UPDATE，不存在则 INSERT；
        边 UNIQUE 约束跳过重复。
        """
        successful = [r for r in results if r.status != "failed"]
        return db.import_results(successful)

    # ------------------------------------------------------------------
    # 清理残留节点
    # ------------------------------------------------------------------

    def _cleanup_removed_nodes(self, reparse_files: list[FileChange],
                               results: list[ParseResult],
                               db: GraphDB) -> int:
        """清理已从源码删除的节点

        对每个变更文件 F:
        1. 收集所有 ParseResult 中 file_path == F.db_rel_path 的 unique_key
        2. 删除 F 中不在该集合里的节点（CASCADE 删关联边）

        必须在所有 TU 导入完成后执行，确保 retained_keys 完整。
        """
        total_removed = 0

        # 按 file_path 分组收集 unique_keys（所有受影响 TU 的并集）
        file_to_keys: dict[str, set[str]] = {}
        for result in results:
            for node in result.nodes:
                file_to_keys.setdefault(node.file_path, set()).add(node.unique_key)

        for fc in reparse_files:
            retained = file_to_keys.get(fc.db_rel_path, set())
            removed = db.delete_removed_nodes(fc.db_rel_path, retained)
            total_removed += removed
            if removed:
                logger.info("清理 %s: 删除 %d 个残留节点",
                            fc.db_rel_path, removed)

        return total_removed

    # ------------------------------------------------------------------
    # 重建文档关联
    # ------------------------------------------------------------------

    def _rebuild_associations(self, rebuild_embeddings: bool) -> dict:
        """重建文档关联边

        content_scan：用 insert_edge（skip duplicates），已被删除节点的关联边
        由 CASCADE 删除，重新运行 content_scan 即可重建。
        """
        from .parser.association_ingester import AssociationIngester

        ingester = AssociationIngester(self.db_path, self.config)
        stats = ingester.ingest_content_scan_associations()

        if rebuild_embeddings:
            emb_stats = ingester.ingest_embedding_associations()
            stats.update(emb_stats)

        ingester.close()
        return stats
