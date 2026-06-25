"""模板实例化提取器

从 AST 中提取白名单中的模板特化，作为独立节点入库，
通过 instantiates 边关联模板定义。

集成方式：由 SemanticExtractor.parse() 调用，传入 config 与路径转换回调，
确保 file_path 与 ast_visitor 走同一套相对路径转换（避免 unique_key 错配）。
"""

from __future__ import annotations

import logging
from clang.cindex import CursorKind

from ..db.relation_types import RelationType
from ..parser.models import NodeInfo, EdgeInfo, NodeType

logger = logging.getLogger(__name__)


class TemplateExtractor:
    """模板实例化提取器

    只提取白名单中模板的特化（如 ARA COM Proxy 模板特化）。
    模板定义本身已由 ast_visitor._extract_classes 作为普通类节点入库，
    这里负责为特化建立独立节点 + instantiates 边。
    """

    def __init__(self, template_whitelist: list[str] | None = None):
        """初始化

        Args:
            template_whitelist: 模板白名单（只有这些模板的特化才入库）
                如 ["ThreadDrivenProxy", "ServiceProxy"]
        """
        self.whitelist = set(template_whitelist or [])

    def extract_template_specializations(
        self, tu_cursor, config, make_file_path,
    ) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """提取白名单中的模板特化

        Args:
            tu_cursor: 翻译单元的根 cursor
            config: ProjectConfig，用于过滤（config.should_extract_node）
            make_file_path: 路径转换回调 (abs_path -> rel_path)，
                与 ast_visitor._make_file_path 同源，保证 file_path 一致

        Returns:
            (nodes, edges) 元组
        """
        nodes: list[NodeInfo] = []
        edges: list[EdgeInfo] = []

        for cursor in tu_cursor.walk_preorder():
            # 只处理类/结构体
            if cursor.kind not in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
                continue
            if not cursor.is_definition():
                continue
            if not cursor.location.file:
                continue
            abs_path = str(cursor.location.file.name)
            if not config.should_extract_node(abs_path):
                continue

            # libclang 对模板特化的 spelling 形如 "ThreadDrivenProxy<...>"
            name = cursor.spelling
            if not name or "<" not in name:
                continue
            # 提取模板定义名（< 之前的部分）
            template_name = name[:name.index("<")]
            if not self._is_whitelisted(template_name):
                continue

            namespace = self._get_namespace(cursor)
            file_path = make_file_path(abs_path)

            # 特化节点（unique_key 含完整特化名，Base<int> 与 Base<float> 不混淆）
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
            # 模板定义节点由 ast_visitor 按其 spelling(不含 <...>) 入库，
            # 故 target_key 用 template_name + 同 namespace/file_path 对齐。
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

    def _is_whitelisted(self, template_name: str) -> bool:
        """检查模板名是否匹配白名单（子串匹配，宽松）"""
        if not template_name or not self.whitelist:
            return False
        for wl_name in self.whitelist:
            if wl_name in template_name:
                return True
        return False

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
