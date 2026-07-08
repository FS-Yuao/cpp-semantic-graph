"""手动标记解析

解析文档中的 [[ClassName]] / [[FuncName]] / [[ns::ClassName]] 标记，
在 DB 中查找匹配的代码节点，生成 doc_describes_code / code_refers_to_doc 边。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..db.graph_db import GraphDB
from ..db.relation_types import RelationType

logger = logging.getLogger(__name__)


@dataclass
class Association:
    """代码-文档关联"""
    doc_unique_key: str          # doc_section 的 unique_key
    code_unique_key: str         # 代码节点的 unique_key
    relation_type: str           # doc_describes_code / code_refers_to_doc
    confidence: float            # 1.0 (手动) / 0~1.0 (自动)
    method: str                  # "manual" / "embedding" / "rule"
    link_text: str               # [[...]] 中的文本
    extra_info: dict | None = None


class DocAssociationParser:
    """手动标记解析器"""

    # [[ClassName]] / [[ns::ClassName]] / [[FuncName]]
    LINK_PATTERN = re.compile(r'\[\[([^\]]+)\]\]')

    def __init__(self, db: GraphDB):
        self.db = db

    def parse_manual_links(
        self, doc_unique_key: str, content: str,
    ) -> list[Association]:
        """解析文档切片中的 [[...]] 标记

        Args:
            doc_unique_key: 文档切片的 unique_key
            content: 文档切片内容

        Returns:
            关联列表
        """
        associations: list[Association] = []
        matches = self.LINK_PATTERN.findall(content)

        for link_text in matches:
            # 解析命名空间限定: [[ns::ClassName]] 或 [[ClassName]]
            namespace_hint = ""
            name = link_text.strip()
            if "::" in name:
                parts = name.rsplit("::", 1)
                namespace_hint = parts[0].strip()
                name = parts[1].strip()

            # 在 DB 中查找匹配的代码节点
            code_key = self._find_code_node(name, namespace_hint)
            if not code_key:
                logger.warning("手动标记未匹配: [[%s]] (ns_hint=%s)", link_text, namespace_hint)
                continue

            # 正向边: doc → code
            associations.append(Association(
                doc_unique_key=doc_unique_key,
                code_unique_key=code_key,
                relation_type=RelationType.DOC_DESCRIBES_CODE.value,
                confidence=1.0,
                method="manual",
                link_text=link_text,
                extra_info={"link_text": link_text},
            ))

            # 反向边: code → doc（自动创建）
            associations.append(Association(
                doc_unique_key=doc_unique_key,
                code_unique_key=code_key,
                relation_type=RelationType.CODE_REFERS_TO_DOC.value,
                confidence=1.0,
                method="manual",
                link_text=link_text,
                extra_info={"link_text": link_text},
            ))

        return associations

    def _find_code_node(self, name: str, namespace_hint: str = "") -> str | None:
        """在 DB 中查找匹配的代码节点，返回 unique_key

        查找优先级:
        1. 精确匹配类名
        2. 精确匹配函数名
        3. 带命名空间限定匹配
        """
        # 1. 类名精确匹配
        rows = self.db.find_node_by_name(name, "class", exact=True)
        if namespace_hint:
            rows = [r for r in rows if namespace_hint in (r.get("namespace") or "")]
        if rows:
            return rows[0]["unique_key"]

        # 2. 函数名精确匹配
        rows = self.db.find_node_by_name(name, "function", exact=True)
        if namespace_hint:
            rows = [r for r in rows if namespace_hint in (r.get("namespace") or "")]
        if rows:
            return rows[0]["unique_key"]

        # 3. 结构体
        rows = self.db.find_node_by_name(name, "struct", exact=True)
        if namespace_hint:
            rows = [r for r in rows if namespace_hint in (r.get("namespace") or "")]
        if rows:
            return rows[0]["unique_key"]

        # 4. 模糊匹配（无命名空间限定时）
        if not namespace_hint:
            rows = self.db.find_node_by_name(name, "class", exact=False)
            if rows:
                return rows[0]["unique_key"]

        return None

    def parse_all_docs(self) -> dict:
        """遍历 DB 中所有 doc_section 节点，解析 [[...]] 标记

        Returns:
            统计信息
        """
        stats = {"docs_scanned": 0, "links_found": 0, "associations_created": 0, "unmatched": 0}

        # 查所有 doc_section 节点
        conn = self.db.conn
        rows = conn.execute(
            "SELECT unique_key, content_preview FROM node WHERE type='doc_section'"
        ).fetchall()

        for row in rows:
            doc_key = row["unique_key"]
            # 获取文档内容预览（包含 [[...]] 标记）
            content = row["content_preview"] or ""
            if not content:
                continue

            stats["docs_scanned"] += 1
            matches = self.LINK_PATTERN.findall(content)
            stats["links_found"] += len(matches)

            if not matches:
                continue

            # 解析关联
            associations = self.parse_manual_links(doc_key, content)
            for assoc in associations:
                if assoc.relation_type == RelationType.DOC_DESCRIBES_CODE.value:
                    stats["associations_created"] += 1
                elif assoc.method == "manual" and assoc.confidence < 1.0:
                    stats["unmatched"] += 1

        return stats
