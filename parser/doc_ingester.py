"""文档节点入库

扫描文档目录，切片后批量写入 SQLite 图谱库的 node 表。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import sys
import time
from pathlib import Path

import yaml

from ..db.graph_db import GraphDB
from ..parser.doc_parser import DocParser, DocSection
from ..parser.models import NodeInfo, NodeType

logger = logging.getLogger(__name__)


class DocIngester:
    """文档切片入库器"""

    def __init__(self, db_path: str, config_path: str | None = None,
                 project_config_path: str | None = None):
        """初始化

        Args:
            db_path: 数据库路径
            config_path: 文档配置 YAML（doc_config.yaml），若为 None 则从项目配置读取
            project_config_path: 项目主配置 YAML（cpp_semantic_graph.yaml），
                用于读取 docs_dir 和 docs_config
        """
        self.db = GraphDB(db_path)
        self.project_config_path = project_config_path

        # 从项目配置读取 docs_dir 和 docs_config
        self._project_config = self._load_yaml(project_config_path)
        if config_path is None:
            config_path = self._resolve_config_path(
                self._project_config.get("docs_config")
            )

        self.config = self._load_yaml(config_path)
        self.parser = DocParser(
            min_section_words=self.config.get("section_split", {}).get(
                "min_word_count", 20
            )
        )
        self.tag_rules = self.config.get("tag_rules", [])
        self.exclude_patterns = self.config.get("exclude_patterns", [])

    def _resolve_config_path(self, rel_path: str | None) -> str | None:
        """将相对于项目配置的路径解析为绝对路径"""
        if not rel_path or not self.project_config_path:
            return None
        base = Path(self.project_config_path).parent
        resolved = (base / rel_path).resolve()
        return str(resolved) if resolved.exists() else None

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def _load_yaml(path: str | None) -> dict:
        """加载 YAML 配置文件"""
        if path and Path(path).exists():
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def get_docs_dir(self) -> str | None:
        """从项目配置获取文档根目录（解析相对路径为绝对路径）"""
        docs_dir = self._project_config.get("docs_dir")
        if not docs_dir:
            return None
        if Path(docs_dir).is_absolute():
            return docs_dir
        # 相对于项目配置文件所在目录解析
        if self.project_config_path:
            base = Path(self.project_config_path).parent
            return str((base / docs_dir).resolve())
        return docs_dir

    def ingest_from_config(self, *, verbose: bool = False) -> dict:
        """从项目配置读取 docs_dir 并入库（无需手动传路径）

        Returns:
            统计信息
        """
        docs_dir = self.get_docs_dir()
        if not docs_dir:
            logger.error("项目配置中未设置 docs_dir")
            return {"files_processed": 0, "sections_created": 0}
        logger.info("文档目录: %s", docs_dir)
        return self.ingest_dir(docs_dir, verbose=verbose)

    def ingest_dir(self, doc_root: str, *, verbose: bool = False) -> dict:
        """扫描文档目录，切片入库

        Args:
            doc_root: 文档根目录
            verbose: 是否输出逐文件进度

        Returns:
            统计信息
        """
        doc_root = Path(doc_root)
        if not doc_root.exists():
            logger.error("文档目录不存在: %s", doc_root)
            return {"files_processed": 0, "sections_created": 0}

        stats = {
            "files_processed": 0,
            "files_skipped": 0,
            "sections_created": 0,
            "sections_updated": 0,
        }

        md_files = sorted(doc_root.rglob("*.md"))
        logger.info("发现 %d 个 .md 文件", len(md_files))

        t0 = time.time()

        for i, md_file in enumerate(md_files, 1):
            # 排除检查
            rel_path = str(md_file.relative_to(doc_root))
            if self._should_exclude(rel_path):
                stats["files_skipped"] += 1
                continue

            # 切片
            sections = self.parser.parse_file(md_file)
            if not sections:
                stats["files_skipped"] += 1
                continue

            # 用相对路径替换绝对路径（unique_key 和 file_path）
            for sec in sections:
                sec.file_path = rel_path
                sec.unique_key = f"doc_section|{rel_path}|{sec.start_line}"

            # 打标签
            for sec in sections:
                sec.tags = self._apply_tags(rel_path)

            # 入库
            for sec in sections:
                node = self._section_to_node(sec, str(md_file))
                existing = self.db.get_node_by_key(node.unique_key)
                if existing:
                    # 检查内容是否变化（existing 已 hydrate，extra_info 为 dict/None）
                    old_extra = existing.get("extra_info") or {}
                    if old_extra.get("content_hash") != sec.content_hash:
                        self.db.upsert_node(node)
                        stats["sections_updated"] += 1
                    # 内容没变，跳过
                else:
                    self.db.upsert_node(node)
                    stats["sections_created"] += 1

            stats["files_processed"] += 1

            if verbose:
                print(f"  [{i}/{len(md_files)}] {md_file.name}: "
                      f"{len(sections)} sections")

        self.db.conn.commit()
        elapsed = time.time() - t0
        stats["elapsed_seconds"] = round(elapsed, 2)

        logger.info("文档入库完成: %d 文件, %d 切片, %.2f 秒",
                     stats["files_processed"], stats["sections_created"], elapsed)
        return stats

    def _should_exclude(self, rel_path: str) -> bool:
        """检查路径是否应排除"""
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return True
        return False

    def _apply_tags(self, rel_path: str) -> list[str]:
        """按目录规则打标签"""
        tags: list[str] = []
        for rule in self.tag_rules:
            pattern = rule.get("path_pattern", "")
            if fnmatch.fnmatch(rel_path, pattern):
                tags.extend(rule.get("tags", []))

        # 去重保序
        seen: set[str] = set()
        unique: list[str] = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique

    @staticmethod
    def _section_to_node(sec: DocSection, abs_path: str) -> NodeInfo:
        """将 DocSection 转为 NodeInfo"""
        extra = {
            "doc_title": sec.doc_title,
            "section_level": sec.section_level,
            "heading": sec.title,
            "content_preview": sec.content_preview,
            "content_hash": sec.content_hash,
            "tags": sec.tags,
            "word_count": sec.word_count,
        }

        return NodeInfo(
            type=NodeType("doc_section"),
            name=sec.title,
            namespace="",
            file_path=sec.file_path,
            start_line=sec.start_line,
            end_line=sec.end_line,
            extra_info=extra,
            unique_key=sec.unique_key,
        )


def main():
    parser = argparse.ArgumentParser(
        description="文档切片入库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("doc_dir", help="文档根目录")
    parser.add_argument("-o", "--output", default="semantic_graph.db",
                        help="数据库路径")
    parser.add_argument("--config", default=None,
                        help="文档解析配置 YAML")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="逐文件进度输出")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    with DocIngester(args.output, args.config) as ing:
        stats = ing.ingest_dir(args.doc_dir, verbose=args.verbose)
        print(f"\n文件处理: {stats['files_processed']}")
        print(f"切片新建: {stats['sections_created']}")
        print(f"切片更新: {stats['sections_updated']}")
        print(f"耗时: {stats.get('elapsed_seconds', 0):.2f}s")


if __name__ == "__main__":
    main()
