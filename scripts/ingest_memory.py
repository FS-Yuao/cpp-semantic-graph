#!/usr/bin/env python3
"""将 CodeBuddy 自动记忆（.workbuddy/memory/）纳入知识图谱

更新两个数据库：
1. doc_graph.db — 通过 parser.py 全量重建（扫描 docs/ + extra_doc_dirs）
2. semantic_graph_full.db — 通过 DocIngester.ingest_from_config() upsert
   + AssociationIngester 重建文档关联边

用法:
  python3 ingest_memory.py [--db-main DB_PATH] [--db-docgraph DB_PATH] [--config CONFIG_PATH]

  --db-main      semantic_graph_full.db 路径（默认: 项目根/semantic_graph_full.db）
  --db-docgraph doc_graph.db 路径（默认: doc_graph/doc_graph.db）
  --config       cpp_semantic_graph.yaml 路径（默认: 自动查找）
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# 确保能 import cpp_semantic_graph 包
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # cpp_semantic_graph/
sys.path.insert(0, str(PROJECT_ROOT.parent))  # 插入父目录，让 from cpp_semantic_graph.xxx 可用

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def rebuild_doc_graph(doc_graph_db: str, config_path: str | None):
    """重建 doc_graph.db（全量，扫描 docs/ + extra_doc_dirs）"""
    import subprocess

    docs_root = "<PROJECT_SRC>/docs"
    config = config_path or str(PROJECT_ROOT / "config" / "doc_config.yaml")
    parser_script = str(PROJECT_ROOT / "doc_graph" / "parser.py")

    logger.info("=== 重建 doc_graph.db ===")
    logger.info("docs_root: %s", docs_root)
    logger.info("db: %s", doc_graph_db)

    cmd = [sys.executable, parser_script, docs_root, "--db", doc_graph_db, "--config", config]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        logger.error("doc_graph 重建失败:\n%s", result.stderr)
    else:
        logger.info("doc_graph 重建完成")


def update_main_db(main_db: str, config_path: str | None):
    """更新 semantic_graph_full.db（增量 upsert + 重建关联边）"""
    from cpp_semantic_graph.parser.doc_ingester import DocIngester
    from cpp_semantic_graph.parser.association_ingester import AssociationIngester
    from cpp_semantic_graph.parser.config import ProjectConfig

    project_config_path = config_path or str(PROJECT_ROOT / "cpp_semantic_graph.yaml")

    logger.info("=== 更新 semantic_graph_full.db ===")

    # 1. 文档入库（含 extra_doc_dirs）
    ingester = DocIngester(main_db, config_path=None, project_config_path=project_config_path)
    stats = ingester.ingest_from_config(verbose=True)
    ingester.close()
    logger.info("文档入库: %d 文件, %d 新切片, %d 更新切片",
                stats.get("files_processed", 0),
                stats.get("sections_created", 0),
                stats.get("sections_updated", 0))

    # 2. 重建文档关联边
    proj_config = ProjectConfig.from_yaml(project_config_path)
    assoc = AssociationIngester(main_db, project_config=proj_config)
    aschip_stats = assoc.ingest_content_scan_associations()
    assoc.close()
    logger.info("关联重建完成")


def main():
    parser = argparse.ArgumentParser(description="将 CodeBuddy 记忆纳入知识图谱")
    parser.add_argument("--db-main", default=None,
                        help="semantic_graph_full.db 路径（默认自动查找）")
    parser.add_argument("--db-docgraph", default=None,
                        help="doc_graph.db 路径（默认 doc_graph/doc_graph.db）")
    parser.add_argument("--config", default=None,
                        help="cpp_semantic_graph.yaml 路径")
    parser.add_argument("--skip-doc-graph", action="store_true",
                        help="跳过 doc_graph.db 重建")
    parser.add_argument("--skip-main-db", action="store_true",
                        help="跳过 semantic_graph_full.db 更新")
    args = parser.parse_args()

    main_db = args.db_main or str(PROJECT_ROOT / "semantic_graph_full.db")
    doc_graph_db = args.db_docgraph or str(PROJECT_ROOT / "doc_graph" / "doc_graph.db")

    t0 = time.time()

    if not args.skip_doc_graph:
        rebuild_doc_graph(doc_graph_db, args.config)

    if not args.skip_main_db:
        update_main_db(main_db, args.config)

    elapsed = time.time() - t0
    logger.info("=== 完成，耗时 %.1fs ===", elapsed)


if __name__ == "__main__":
    main()
