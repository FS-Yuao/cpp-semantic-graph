"""模板实例化提取器

从 AST 中提取白名单中的模板特化，作为独立节点入库，
通过 instantiates 边关联模板定义。
"""

from __future__ import annotations

import logging
from clang.cindex import CursorKind

from ..db.relation_types import RelationType
from ..parser.models import NodeInfo, EdgeInfo, NodeType

logger = logging.getLogger(__name__)


class TemplateExtractor:
    """模板实例化提取器"""

    def __init__(self, template_whitelist: list[str] | None = None):
        """初始化

        Args:
            template_whitelist: 模板白名单（只有这些模板的特化才入库）
                如 ["ThreadDrivenProxy", "ServiceProxy"]
        """
        self.whitelist = set(template_whitelist or [])

    def extract_template_specializations(
        self, tu_cursor, should_include_fn,
    ) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """提取白名单中的模板特化

        Args:
            tu_cursor: 翻译单元的根 cursor
            should_include_fn: 判断文件是否应包含的回调

        Returns:
            (nodes, edges) 元组
        """
        nodes: list[NodeInfo] = []
        edges: list[EdgeInfo] = []

        for cursor in tu_cursor.walk_preorder():
            # 只处理类/结构体模板特化
            if cursor.kind not in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
                continue
            if not cursor.is_definition():
                continue
            if not should_include_fn(cursor):
                continue

            # 检查是否为模板特化
            # libclang: 模板特化的 cursor.spelling 包含模板参数
            # 如 "OtaServiceInterfaceProxy" 而非 "ThreadDrivenProxy"
            name = cursor.spelling
            if not name or not self._is_whitelisted(name):
                continue

            # 提取模板定义名（去掉模板参数部分）
            template_name = self._extract_template_name(name)
            if not template_name:
                continue

            namespace = self._get_namespace(cursor)
            file_path = str(cursor.location.file.name) if cursor.location.file else ""

            # 特化节点
            spec_key = f"{NodeType.CLASS.value}|{namespace}|{name}|{file_path}"
            nodes.append(NodeInfo(
                type=NodeType.CLASS,
                name=name,
                namespace=namespace,
                file_path=file_path,
                start_line=cursor.extent.start.line,
                end_line=cursor.extent.end.line,
                extra_info={
                    "is_template_specialization": True,
                    "template_name": template_name,
                },
                unique_key=spec_key,
            ))

            # instantiates 边: 特化 → 模板定义
            template_key = f"{NodeType.CLASS.value}|{namespace}|{template_name}|{file_path}"
            edges.append(EdgeInfo(
                relation_type=RelationType.INSTANTIATES,
                from_unique_key=spec_key,
                to_unique_key=template_key,
                extra_info={
                    "specialization_name": name,
                    "template_name": template_name,
                },
            ))

        return nodes, edges

    def _is_whitelisted(self, name: str) -> bool:
        """检查类名是否匹配白名单"""
        if not self.whitelist:
            return False
        for wl_name in self.whitelist:
            if wl_name in name:
                return True
        return False

    @staticmethod
    def _extract_template_name(name: str) -> str | None:
        """从特化名提取模板定义名

        如 "OtaServiceInterfaceProxy" → 无法提取（不是模板特化）
        如 "ThreadDrivenProxy<OtaServiceInterface>" → "ThreadDrivenProxy"
        """
        if "<" in name:
            return name[:name.index("<")]
        return None

    @staticmethod
    def _get_namespace(cursor) -> str:
        """提取命名空间"""
        parts = []
        parent = cursor.semantic_parent
        while parent:
            if parent.kind == CursorKind.NAMESPACE:
                parts.append(parent.spelling)
            parent = parent.semantic_parent
        return "::".join(reversed(parts)) if parts else ""
