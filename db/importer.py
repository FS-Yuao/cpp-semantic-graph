"""
JSON → SQLite 入库脚本

将 AST visitor 输出的标准化 JSON 批量写入 SQLite 图谱库。

支持两种使用方式:
1. CLI 命令行:  python -m db.importer <json_dir> -o <db_path>
2. Python API:  from db.importer import Importer; Importer(db_path).import_json_dir(json_dir)

JSON 文件格式（与 ParseResult.to_dict 对齐）:
{
  "source_path": "src/foo.cpp",
  "status": "success",
  "error_message": "",
  "nodes": [
    {"type": "class", "name": "Foo", "namespace": "", "file_path": "...",
     "start_line": 1, "end_line": 10, "extra_info": {}, "unique_key": "class||Foo|..."}
  ],
  "edges": [
    {"relation_type": "inherits_public", "from_unique_key": "...",
     "to_unique_key": "...", "extra_info": {}}
  ],
  "includes": [
    {"source_file": "src/foo.cpp", "included_file": "include/foo.h", "is_system": false}
  ]
}
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Iterator

from .graph_db import GraphDB
from .relation_types import RelationType
from ..parser.models import NodeInfo, EdgeInfo, IncludeDep, ParseResult, NodeType

logger = logging.getLogger(__name__)


class Importer:
    """JSON → SQLite 图谱入库器"""

    def __init__(self, db_path: str):
        """初始化入库器

        Args:
            db_path: SQLite 数据库文件路径，不存在则自动创建
        """
        self.db = GraphDB(db_path)

    def close(self):
        """关闭数据库连接"""
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # 单文件导入
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_node_dict(d: dict) -> NodeInfo:
        """将 JSON dict 转为 NodeInfo"""
        node_type = d.get("type", "class")
        try:
            node_type = NodeType(node_type)
        except ValueError:
            node_type = NodeType.CLASS  # fallback

        return NodeInfo(
            type=node_type,
            name=d.get("name", ""),
            namespace=d.get("namespace", ""),
            file_path=d.get("file_path", ""),
            start_line=d.get("start_line", 0),
            end_line=d.get("end_line", 0),
            extra_info=d.get("extra_info", {}),
            unique_key=d.get("unique_key", ""),
        )

    @staticmethod
    def _parse_edge_dict(d: dict) -> EdgeInfo:
        """将 JSON dict 转为 EdgeInfo"""
        rt_str = d.get("relation_type", "")
        rt = RelationType.from_str(rt_str)
        if rt is None:
            # 未知关系类型 → 用字符串直接存储，import_parse_result 内处理
            # 但 EdgeInfo 需要 RelationType 枚举，用 BELONGS_TO 作为占位
            logger.warning("未知关系类型: %s，存为 belongs_to", rt_str)
            rt = RelationType.BELONGS_TO

        extra = d.get("extra_info", {})
        # 保留原始 relation_type 字符串，用于后续可能的自定义类型
        if rt_str and rt.value != rt_str:
            extra["_original_relation_type"] = rt_str

        return EdgeInfo(
            relation_type=rt,
            from_unique_key=d.get("from_unique_key", ""),
            to_unique_key=d.get("to_unique_key", ""),
            extra_info=extra,
        )

    @staticmethod
    def _parse_include_dict(d: dict) -> IncludeDep:
        """将 JSON dict 转为 IncludeDep"""
        return IncludeDep(
            source_file=d.get("source_file", ""),
            included_file=d.get("included_file", ""),
            is_system=d.get("is_system", False),
        )

    def load_json_file(self, json_path: str | Path) -> ParseResult:
        """从 JSON 文件加载解析结果

        Args:
            json_path: AST visitor 输出的 JSON 文件路径

        Returns:
            ParseResult 实例
        """
        json_path = Path(json_path)
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        nodes = [self._parse_node_dict(n) for n in data.get("nodes", [])]
        edges = [self._parse_edge_dict(e) for e in data.get("edges", [])]
        includes = [self._parse_include_dict(i) for i in data.get("includes", [])]

        return ParseResult(
            source_path=data.get("source_path", str(json_path)),
            status=data.get("status", "success"),
            error_message=data.get("error_message", ""),
            nodes=nodes,
            edges=edges,
            includes=includes,
        )

    def import_json_file(self, json_path: str | Path) -> dict:
        """导入单个 JSON 文件到数据库

        Args:
            json_path: JSON 文件路径

        Returns:
            导入统计信息
        """
        result = self.load_json_file(json_path)
        return self.db.import_parse_result(result)

    # ------------------------------------------------------------------
    # 批量导入
    # ------------------------------------------------------------------

    def _find_json_files(self, directory: str | Path) -> list[Path]:
        """扫描目录下的所有 JSON 文件"""
        directory = Path(directory)
        if not directory.exists():
            logger.error("目录不存在: %s", directory)
            return []
        return sorted(directory.rglob("*.json"))

    def import_json_dir(self, directory: str | Path, *,
                        verbose: bool = False) -> dict:
        """批量导入目录下所有 JSON 文件

        Args:
            directory: 包含 JSON 文件的目录
            verbose: 是否输出每个文件的进度

        Returns:
            汇总统计信息
        """
        json_files = self._find_json_files(directory)
        if not json_files:
            logger.warning("未找到 JSON 文件: %s", directory)
            return {
                "files_processed": 0,
                "files_failed": 0,
                "nodes_new": 0,
                "nodes_updated": 0,
                "edges_new": 0,
                "edges_skipped": 0,
                "includes_new": 0,
            }

        logger.info("发现 %d 个 JSON 文件", len(json_files))
        total_stats = {
            "files_processed": 0,
            "files_failed": 0,
            "nodes_new": 0,
            "nodes_updated": 0,
            "edges_new": 0,
            "edges_skipped": 0,
            "includes_new": 0,
        }

        start_time = time.time()

        for i, json_path in enumerate(json_files, 1):
            try:
                stats = self.import_json_file(json_path)
                total_stats["files_processed"] += 1
                total_stats["nodes_new"] += stats["nodes_new"]
                total_stats["nodes_updated"] += stats["nodes_updated"]
                total_stats["edges_new"] += stats["edges_new"]
                total_stats["edges_skipped"] += stats["edges_skipped"]
                total_stats["includes_new"] += stats["includes_new"]

                if verbose:
                    print(f"  [{i}/{len(json_files)}] {json_path.name}: "
                          f"+{stats['nodes_new']} nodes, "
                          f"+{stats['edges_new']} edges, "
                          f"+{stats['includes_new']} includes")
            except Exception as e:
                total_stats["files_failed"] += 1
                logger.error("导入失败 %s: %s", json_path, e)

            # 每 100 个文件输出一次进度
            if i % 100 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                logger.info("进度: %d/%d (%.1f 文件/秒)", i, len(json_files), rate)

        elapsed = time.time() - start_time
        total_stats["elapsed_seconds"] = round(elapsed, 2)

        logger.info("批量导入完成: %d 文件, %.2f 秒", total_stats["files_processed"], elapsed)
        return total_stats

    # ------------------------------------------------------------------
    # ParseResult 批量导入（直接从内存，不经 JSON）
    # ------------------------------------------------------------------

    def import_results(self, results: list[ParseResult]) -> dict:
        """直接导入 ParseResult 列表

        适用于从 parser 直接入库，无需中间 JSON 文件。

        Args:
            results: ParseResult 列表

        Returns:
            汇总统计信息
        """
        return self.db.import_results(results)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def get_db_stats(self) -> dict:
        """获取数据库统计信息"""
        return self.db.get_stats()

    def print_summary(self, stats: dict):
        """打印导入统计摘要"""
        print("\n" + "=" * 60)
        print("导入统计摘要")
        print("=" * 60)
        print(f"  处理文件数:  {stats.get('files_processed', 0)}")
        print(f"  失败文件数:  {stats.get('files_failed', 0)}")
        print(f"  新增节点:    {stats.get('nodes_new', 0)}")
        print(f"  更新节点:    {stats.get('nodes_updated', 0)}")
        print(f"  新增边:      {stats.get('edges_new', 0)}")
        print(f"  跳过边:      {stats.get('edges_skipped', 0)}")
        print(f"  新增 include: {stats.get('includes_new', 0)}")
        if "elapsed_seconds" in stats:
            print(f"  耗时:        {stats['elapsed_seconds']:.2f} 秒")
        print("=" * 60)

        # 数据库统计
        db_stats = self.get_db_stats()
        print("\n数据库统计:")
        print(f"  节点总数:    {db_stats['node_count']}")
        print(f"  边总数:      {db_stats['edge_count']}")
        print(f"  include 总数: {db_stats['include_count']}")

        if db_stats["node_type_distribution"]:
            print("\n  节点类型分布:")
            for t, c in db_stats["node_type_distribution"].items():
                print(f"    {t}: {c}")

        if db_stats["edge_type_distribution"]:
            print("\n  边类型分布:")
            for t, c in db_stats["edge_type_distribution"].items():
                print(f"    {t}: {c}")


# ======================================================================
# CLI 入口
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="将 AST visitor 输出的 JSON 批量导入 SQLite 图谱库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 导入单个 JSON 文件
  python -m db.importer output/soc_update.json -o graph.db

  # 批量导入目录下所有 JSON
  python -m db.importer output/ -o graph.db --verbose

  # 查看已有数据库统计
  python -m db.importer --stats -o graph.db
        """,
    )
    parser.add_argument(
        "json_path",
        nargs="?",
        help="JSON 文件或目录路径",
    )
    parser.add_argument(
        "-o", "--output",
        default="semantic_graph.db",
        help="SQLite 数据库输出路径 (默认: semantic_graph.db)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="输出每个文件的导入详情",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="只显示数据库统计信息，不导入",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认: INFO)",
    )

    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    with Importer(args.output) as importer:
        if args.stats:
            # 只显示统计
            db_stats = importer.get_db_stats()
            print(json.dumps(db_stats, indent=2, ensure_ascii=False))
            return

        if not args.json_path:
            parser.error("请提供 JSON 文件或目录路径（或使用 --stats 查看统计）")

        json_path = Path(args.json_path)
        if not json_path.exists():
            print(f"错误: 路径不存在: {json_path}", file=sys.stderr)
            sys.exit(1)

        if json_path.is_file():
            # 单文件导入
            print(f"导入文件: {json_path}")
            stats = importer.import_json_file(json_path)
            importer.print_summary(stats)
        elif json_path.is_dir():
            # 批量导入
            print(f"扫描目录: {json_path}")
            stats = importer.import_json_dir(json_path, verbose=args.verbose)
            importer.print_summary(stats)
        else:
            print(f"错误: 不是文件或目录: {json_path}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
