"""端到端流程编排

串联 AST visitor + 入库 + 查询 + 正确性验证，提供一键全量解析入口。

流程:
  1. 读取 compile_commands.json，筛选目标翻译单元
  2. 并行解析每个翻译单元（AST visitor）→ ParseResult
  3. 批量入库（importer）
  4. 运行正确性验证（accuracy_validator，可选）
  5. 输出统计报告（节点/边/include 分布 + 性能 + 验证结果）

用法:
  from cpp_semantic_graph.pipeline import FullParsePipeline
  pipeline = FullParsePipeline(config_path)
  report = pipeline.run(db_path, filter_path="hq_ota_service")
"""

import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .parser.config import ProjectConfig
from .parser.compile_db import CompileDB
from .parser.ast_visitor import SemanticExtractor
from .parser.models import ParseResult
from .db.importer import Importer

logger = logging.getLogger(__name__)


@dataclass
class ParseReport:
    """全量解析统计报告"""
    # 翻译单元
    tu_total: int = 0
    tu_success: int = 0
    tu_failed: int = 0
    failed_files: list[str] = field(default_factory=list)
    # 入库
    nodes_new: int = 0
    nodes_updated: int = 0
    edges_new: int = 0
    edges_skipped: int = 0
    includes_new: int = 0
    # 数据库现状
    db_node_count: int = 0
    db_edge_count: int = 0
    db_include_count: int = 0
    node_type_dist: dict = field(default_factory=dict)
    edge_type_dist: dict = field(default_factory=dict)
    # 性能
    parse_seconds: float = 0.0
    import_seconds: float = 0.0
    total_seconds: float = 0.0
    # 文档融合
    doc_sections_new: int = 0
    doc_associations_new: int = 0
    associations_rebuilt: bool = False
    # 验证
    validation: dict | None = None

    @property
    def failure_rate(self) -> float:
        return self.tu_failed / self.tu_total if self.tu_total else 0.0

    def to_dict(self) -> dict:
        return {
            "tu_total": self.tu_total, "tu_success": self.tu_success,
            "tu_failed": self.tu_failed, "failure_rate": f"{self.failure_rate:.1%}",
            "failed_files": self.failed_files,
            "nodes_new": self.nodes_new, "edges_new": self.edges_new,
            "includes_new": self.includes_new,
            "db_node_count": self.db_node_count,
            "db_edge_count": self.db_edge_count,
            "db_include_count": self.db_include_count,
            "node_type_dist": self.node_type_dist,
            "edge_type_dist": self.edge_type_dist,
            "parse_seconds": round(self.parse_seconds, 2),
            "import_seconds": round(self.import_seconds, 2),
            "total_seconds": round(self.total_seconds, 2),
            "doc_sections_new": self.doc_sections_new,
            "doc_associations_new": self.doc_associations_new,
            "associations_rebuilt": self.associations_rebuilt,
            "validation": self.validation,
        }


# ----------------------------------------------------------------------
# 多进程 worker：每个进程独立持有 extractor（libclang index 不可跨进程序列化）
# ----------------------------------------------------------------------

_WORKER_CONFIG: ProjectConfig | None = None
_WORKER_EXTRACTOR: SemanticExtractor | None = None


def _worker_init(config_path: str):
    """进程池初始化：加载配置并创建 extractor"""
    global _WORKER_CONFIG, _WORKER_EXTRACTOR
    _WORKER_CONFIG = ProjectConfig.from_yaml(config_path)
    _WORKER_EXTRACTOR = SemanticExtractor(_WORKER_CONFIG)


def _worker_parse(entry_file: str, entry_args: list[str], directory: str = ""):
    """单个翻译单元解析（在 worker 进程内执行）"""
    from .parser.compile_db import CompileCommand
    # directory 是编译工作目录，相对 -I 路径相对于它解析（主题D：原硬编码 "" 导致 -I 解析失败、图谱不完整）
    entry = CompileCommand(file=entry_file, directory=directory, args=entry_args)
    result = _WORKER_EXTRACTOR.parse(entry)
    # ParseResult 含 NodeInfo/EdgeInfo（dataclass），可 pickle 跨进程返回
    return result


class FullParsePipeline:
    """端到端全量解析流程"""

    def __init__(self, config_path: str):
        self.config = ProjectConfig.from_yaml(config_path)
        self.config_path = config_path

    def run(self, db_path: str, *,
            filter_path: str | None = None,
            include_generated: bool = False,
            run_validation: bool = False,
            baseline_path: str | None = None,
            reset_db: bool = True) -> ParseReport:
        """执行全量解析流程

        Args:
            db_path: 输出数据库路径
            filter_path: 只解析路径含此串的翻译单元（如 "hq_ota_service"）
            include_generated: 是否包含生成代码（src-gen）
            run_validation: 是否运行正确性验证
            baseline_path: ground truth 路径（run_validation=True 时需要）
            reset_db: 是否清空已有数据库重建
        """
        t0 = time.time()
        report = ParseReport()

        # 1. 加载 compile_commands，筛选翻译单元
        cdb = CompileDB(self.config.compile_commands, config=self.config)
        entries = cdb.get_entries(
            filter_path=filter_path,
            include_generated=include_generated,
        )
        report.tu_total = len(entries)
        logger.info("待解析翻译单元: %d (filter=%s)", report.tu_total, filter_path)

        # 2. 并行解析
        t_parse = time.time()
        results = self._parse_all(entries)
        report.parse_seconds = time.time() - t_parse

        # 统计成功/失败
        for r in results:
            if r.status == "failed":
                report.tu_failed += 1
                report.failed_files.append(r.source_path)
            else:
                report.tu_success += 1
        logger.info("解析完成: 成功 %d / 失败 %d (%.1f%%)",
                    report.tu_success, report.tu_failed, report.failure_rate * 100)

        # 3. 入库
        if reset_db and Path(db_path).exists():
            Path(db_path).unlink()
        t_import = time.time()
        with Importer(db_path) as importer:
            stats = importer.import_results(results)
            db_stats = importer.get_db_stats()
        report.import_seconds = time.time() - t_import
        report.nodes_new = stats["nodes_new"]
        report.nodes_updated = stats["nodes_updated"]
        report.edges_new = stats["edges_new"]
        report.edges_skipped = stats["edges_skipped"]
        report.includes_new = stats["includes_new"]
        report.db_node_count = db_stats["node_count"]
        report.db_edge_count = db_stats["edge_count"]
        report.db_include_count = db_stats["include_count"]
        report.node_type_dist = db_stats["node_type_distribution"]
        report.edge_type_dist = db_stats["edge_type_distribution"]

        # 3.5 文档融合：解析文档切片 + 重建文档-代码关联边
        # 全量解析必须包含文档，否则 cpp_search_docs 完全失效
        try:
            from .parser.doc_ingester import DocIngester
            from .parser.association_ingester import AssociationIngester

            doc_ingester = DocIngester(
                db_path, config_path=None,
                project_config_path=self.config_path,
            )
            doc_stats = doc_ingester.ingest_from_config(verbose=False)
            doc_ingester.close()
            report.doc_sections_new = doc_stats.get("sections_created", 0)

            assoc_ingester = AssociationIngester(db_path, self.config)
            assoc_stats = assoc_ingester.ingest_content_scan_associations()
            # P0-4 修复：接入 manual_links 配置关联（原为死代码，配置了却不生效）
            config_stats = assoc_ingester.ingest_config_associations(self.config.docs_config)
            assoc_stats.update(config_stats)
            assoc_ingester.close()
            report.doc_associations_new = assoc_stats.get("edges_created", 0)
            report.associations_rebuilt = True
            logger.info("文档融合完成: +{} 切片, +{} 关联边".format(
                report.doc_sections_new, report.doc_associations_new))
        except Exception as e:
            logger.warning("文档融合失败（非致命，cpp_search_docs 将不可用）: %s", e)
            report.associations_rebuilt = False

        # 4. 正确性验证（可选）
        if run_validation:
            report.validation = self._run_validation(
                db_path, baseline_path, filter_path)

        report.total_seconds = time.time() - t0
        return report

    # ------------------------------------------------------------------

    def _parse_all(self, entries: list) -> list[ParseResult]:
        """并行解析所有翻译单元"""
        max_workers = self.config.max_workers or 1

        if max_workers <= 1:
            # 串行：直接用主进程的 extractor
            extractor = SemanticExtractor(self.config)
            return [extractor.parse(e) for e in entries]

        # 并行：每进程独立 extractor
        results: list[ParseResult] = []
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_worker_init,
            initargs=(self.config_path,),
        ) as pool:
            future_to_entry = {
                pool.submit(_worker_parse, e.file, e.args, e.directory): e for e in entries
            }
            for i, fut in enumerate(as_completed(future_to_entry), 1):
                entry = future_to_entry[fut]
                try:
                    results.append(fut.result())
                except Exception as exc:
                    logger.error("解析异常 %s: %s", entry.file, exc)
                    results.append(ParseResult(
                        source_path=entry.file, status="failed",
                        error_message=str(exc),
                    ))
                if i % 20 == 0:
                    logger.info("解析进度: %d/%d", i, len(entries))
        return results

    def _run_validation(self, db_path: str, baseline_path: str | None,
                        filter_path: str | None) -> dict:
        """运行正确性验证，返回汇总"""
        if not baseline_path:
            logger.warning("未提供 baseline，跳过正确性验证")
            return {"skipped": "no baseline"}
        try:
            from .validation.clangd_baseline import ClangdBaseline
            from .validation.accuracy_validator import AccuracyValidator
        except ImportError as e:
            logger.warning("验证模块不可用: %s", e)
            return {"skipped": str(e)}

        baseline = ClangdBaseline.load(baseline_path)
        with AccuracyValidator(db_path, baseline) as v:
            dim_results = v.run_all()
        return {
            "dimensions": [
                {
                    "name": r.name,
                    "precision": f"{r.precision:.1%}",
                    "recall": f"{r.recall:.1%}",
                    "pass": r.pass_,
                }
                for r in dim_results
            ],
            "all_pass": all(r.pass_ for r in dim_results),
        }
