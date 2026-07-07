"""文档独立查询

按关键词搜文档、按标签过滤、按类名/函数名查关联文档。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..db.graph_db import GraphDB
from ..db.relation_types import RelationType
from .query_utils import parse_extra as _parse_extra  # P2-3: 统一实现

logger = logging.getLogger(__name__)


@dataclass
class DocSectionInfo:
    """文档切片信息"""
    title: str
    doc_title: str
    file_path: str
    start_line: int
    end_line: int
    content_preview: str
    tags: list[str] = field(default_factory=list)
    word_count: int = 0


@dataclass
class DocWithCode:
    """文档切片 + 关联代码"""
    doc: DocSectionInfo
    related_code: list[dict] = field(default_factory=list)
    # related_code: [{name, type, namespace, file_path, confidence, method}]


class DocQuery:
    """文档查询"""

    def __init__(self, db_path: str):
        self.db = GraphDB(db_path)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def search_documentation(
        self,
        keyword: str,
        tag: str | None = None,
        max_results: int = 20,
        min_confidence: float = 0.0,
    ) -> list[DocWithCode]:
        """按关键词搜文档，返回文档+关联代码

        Args:
            keyword: 搜索文档标题和内容预览
            tag: 按标签过滤
            max_results: 最大返回数
            min_confidence: 关联代码最低置信度（0=不过滤，0.6=过滤低质量embedding关联）

        Returns:
            文档+关联代码列表
        """
        conn = self.db.conn
        # 搜结构化字段而非整个 JSON 字符串，避免命中 JSON key/无关字段（主题A-6）
        # 如 'ota' 不再因 'data'/'content_hash' 等字段名误匹配
        sql = """SELECT * FROM node
                 WHERE type='doc_section'
                 AND (name LIKE ?
                      OR json_extract(extra_info, '$.doc_title') LIKE ?
                      OR json_extract(extra_info, '$.content_preview') LIKE ?)"""
        params = [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]

        if tag:
            # tag 是 JSON 数组，用 json_each 精确匹配元素，避免子串误匹配
            sql += " AND EXISTS (SELECT 1 FROM json_each(json_extract(extra_info, '$.tags')) WHERE value = ?)"
            params.append(tag)

        sql += " ORDER BY start_line LIMIT ?"
        params.append(max_results)

        rows = conn.execute(sql, params).fetchall()

        results: list[DocWithCode] = []
        for row in rows:
            extra = _parse_extra(row["extra_info"])
            doc_info = DocSectionInfo(
                title=row["name"],
                doc_title=extra.get("doc_title", ""),
                file_path=row["file_path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                content_preview=extra.get("content_preview", ""),
                tags=extra.get("tags", []),
                word_count=extra.get("word_count", 0),
            )

            # 查关联代码
            related = self._find_related_code(row["id"], min_confidence=min_confidence)
            results.append(DocWithCode(doc=doc_info, related_code=related))

        return results

    def get_docs_for_class(self, class_name: str, *,
                           min_confidence: float = 0.0) -> list[DocSectionInfo]:
        """获取指定类关联的所有文档切片

        通过 code_refers_to_doc 边反向查找。

        Args:
            class_name: 类名
            min_confidence: 最低置信度（0=不过滤）
        """
        return self._get_docs_for_code(class_name, "class",
                                        min_confidence=min_confidence)

    def get_docs_for_function(self, func_name: str, *,
                               min_confidence: float = 0.0) -> list[DocSectionInfo]:
        """获取指定函数关联的所有文档切片"""
        return self._get_docs_for_code(func_name, "function",
                                        min_confidence=min_confidence)

    def _get_docs_for_code(
        self, name: str, node_type: str, *,
        min_confidence: float = 0.0,
    ) -> list[DocSectionInfo]:
        """通过关联边查找文档"""
        # 找代码节点
        code_nodes = self.db.find_node_by_name(name, node_type, exact=True)
        if not code_nodes:
            return []

        results: list[DocSectionInfo] = []
        seen: set[int] = set()

        for code_node in code_nodes:
            code_id = code_node["id"]

            # 方式1: code_refers_to_doc 边 (from=code, to=doc)
            edges = self.db.get_edges_from(code_id, "code_refers_to_doc")
            for edge in edges:
                # confidence 过滤
                if min_confidence > 0:
                    edge_extra = _parse_extra(edge.get("extra_info", {}))
                    if edge_extra.get("confidence", 0) < min_confidence:
                        continue

                doc_id = edge["to_id"]
                if doc_id in seen:
                    continue
                seen.add(doc_id)

                doc_node = self.db.get_node_by_id(doc_id)
                if doc_node:
                    results.append(self._node_to_doc_info(doc_node))

            # 方式2: doc_describes_code 边 (from=doc, to=code)
            edges2 = self.db.get_edges_to(code_id, "doc_describes_code")
            for edge in edges2:
                # confidence 过滤
                if min_confidence > 0:
                    edge_extra = _parse_extra(edge.get("extra_info", {}))
                    if edge_extra.get("confidence", 0) < min_confidence:
                        continue

                doc_id = edge["from_id"]
                if doc_id in seen:
                    continue
                seen.add(doc_id)

                doc_node = self.db.get_node_by_id(doc_id)
                if doc_node:
                    results.append(self._node_to_doc_info(doc_node))

        return results

    def _find_related_code(self, doc_id: int, *,
                           min_confidence: float = 0.0) -> list[dict]:
        """查找文档关联的代码实体

        Args:
            doc_id: 文档节点 ID
            min_confidence: 最低置信度（0=不过滤）
        """
        results: list[dict] = []
        seen: set[int] = set()

        # doc_describes_code 边 (from=doc, to=code)
        edges = self.db.get_edges_from(doc_id, "doc_describes_code")
        for edge in edges:
            # confidence 过滤
            edge_extra = _parse_extra(edge.get("extra_info", {}))
            if min_confidence > 0 and edge_extra.get("confidence", 0) < min_confidence:
                continue

            code_id = edge["to_id"]
            if code_id in seen:
                continue
            seen.add(code_id)

            code_node = self.db.get_node_by_id(code_id)
            if code_node:
                edge_extra = _parse_extra(edge.get("extra_info", {}))
                results.append({
                    "name": code_node["name"],
                    "type": code_node["type"],
                    "namespace": code_node.get("namespace", ""),
                    "file_path": code_node.get("file_path", ""),
                    "confidence": edge_extra.get("confidence", 0),
                    "method": edge_extra.get("method", ""),
                })

        return results

    @staticmethod
    def _node_to_doc_info(node: dict) -> DocSectionInfo:
        extra = _parse_extra(node.get("extra_info", {}))
        return DocSectionInfo(
            title=node["name"],
            doc_title=extra.get("doc_title", ""),
            file_path=node["file_path"],
            start_line=node["start_line"],
            end_line=node["end_line"],
            content_preview=extra.get("content_preview", ""),
            tags=extra.get("tags", []),
            word_count=extra.get("word_count", 0),
        )
