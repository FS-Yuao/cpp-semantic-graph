"""融合查询

查代码时带出文档，查文档时定位代码，支持多跳跨域遍历。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..db.graph_db import GraphDB
from ..db.relation_types import RelationType
from .graph_query import GraphQuery, ClassInfo, FunctionInfo
from .doc_query import DocQuery, DocSectionInfo, DocWithCode
from .query_utils import parse_extra as _parse_extra  # P2-3: 统一实现

logger = logging.getLogger(__name__)


@dataclass
class ClassWithDocs:
    """类信息 + 关联文档"""
    class_info: ClassInfo
    related_docs: list[dict] = field(default_factory=list)
    # related_docs: [{title, doc_path, content_preview, confidence, method}]


@dataclass
class FunctionWithDocs:
    """函数信息 + 关联文档"""
    func_info: FunctionInfo
    related_docs: list[dict] = field(default_factory=list)


class FusionQuery:
    """融合查询"""

    def __init__(self, db_path: str):
        self._gq = GraphQuery(db_path)
        self._dq = DocQuery(db_path)
        self.db = GraphDB(db_path)

    def close(self):
        self._gq.close()
        self._dq.close()
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def search_class_with_docs(self, name: str, *,
                                min_confidence: float = 0.0) -> list[ClassWithDocs]:
        """查类时自动带出关联文档

        Args:
            name: 类名
            min_confidence: 关联最低置信度（0=不过滤，0.6=过滤低质量embedding关联）

        Returns:
            类信息 + 关联文档列表
        """
        classes = self._gq.search_class(name, exact=False)
        results: list[ClassWithDocs] = []

        for cls in classes:
            docs = self._dq.get_docs_for_class(cls.name,
                                                min_confidence=min_confidence)
            doc_list = [
                {
                    "title": d.title,
                    "doc_path": d.file_path,
                    "content_preview": d.content_preview,
                    "confidence": 0,  # 手动关联时 confidence 在边上
                    "method": "association",
                }
                for d in docs
            ]

            # 也搜索标题包含类名的文档（规则关联补充）
            if not doc_list:
                doc_search = self._dq.search_documentation(cls.name, max_results=3)
                doc_list = [
                    {
                        "title": d.doc.title,
                        "doc_path": d.doc.file_path,
                        "content_preview": d.doc.content_preview,
                        "confidence": 0.6,
                        "method": "keyword_match",
                    }
                    for d in doc_search
                    if cls.name in d.doc.title or cls.name in d.doc.content_preview
                ]

            results.append(ClassWithDocs(
                class_info=cls,
                related_docs=doc_list,
            ))

        return results

    def search_function_with_docs(self, name: str, *,
                                   min_confidence: float = 0.0) -> list[FunctionWithDocs]:
        """查函数时自动带出关联文档

        Args:
            name: 函数名
            min_confidence: 关联最低置信度（0=不过滤，0.6=过滤低质量embedding关联）
        """
        funcs = self._gq.search_function(name)
        results: list[FunctionWithDocs] = []

        for func in funcs:
            docs = self._dq.get_docs_for_function(func.name,
                                                   min_confidence=min_confidence)
            doc_list = [
                {
                    "title": d.title,
                    "doc_path": d.file_path,
                    "content_preview": d.content_preview,
                    "confidence": 0,
                    "method": "association",
                }
                for d in docs
            ]

            results.append(FunctionWithDocs(
                func_info=func,
                related_docs=doc_list,
            ))

        return results
