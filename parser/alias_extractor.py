"""类型别名 / using 声明提取器

提取:
- using Alias = Base<T>   → type_alias 边 (Alias → Base<T>)
- typedef Base<T> Alias   → type_alias 边 (Alias → Base<T>)
- using Base::func        → using_decl 边 (子类::func → Base::func)
"""

from __future__ import annotations

import logging
from clang.cindex import CursorKind

from ..db.relation_types import RelationType
from ..parser.models import NodeInfo, EdgeInfo, NodeType

logger = logging.getLogger(__name__)


class AliasExtractor:
    """类型别名和 using 声明提取器"""

    def extract_type_aliases(
        self, tu_cursor, should_include_fn,
    ) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """提取类型别名关系

        Returns:
            (nodes, edges) 元组
        """
        nodes: list[NodeInfo] = []
        edges: list[EdgeInfo] = []

        for cursor in tu_cursor.walk_preorder():
            if cursor.kind == CursorKind.TYPE_ALIAS_DECL:
                # using Alias = TargetType;
                self._process_type_alias(cursor, should_include_fn, nodes, edges)

            elif cursor.kind == CursorKind.TYPEDEF_DECL:
                # typedef TargetType Alias;
                self._process_typedef(cursor, should_include_fn, nodes, edges)

            elif cursor.kind == CursorKind.USING_DECLARATION:
                # using Base::func;
                self._process_using_declaration(cursor, should_include_fn, edges)

        return nodes, edges

    def _process_type_alias(
        self, cursor, should_include_fn,
        nodes: list[NodeInfo], edges: list[EdgeInfo],
    ):
        """处理 using Alias = TargetType;"""
        if not cursor.location.file or not should_include_fn(cursor):
            return

        alias_name = cursor.spelling
        # 获取目标类型
        target_type = ""
        if cursor.underlying_typedef_type:
            target_type = cursor.underlying_typedef_type.spelling

        if not alias_name or not target_type:
            return

        namespace = self._get_namespace(cursor)
        file_path = str(cursor.location.file.name)

        # 别名节点
        alias_key = f"{NodeType.CLASS.value}|{namespace}|{alias_name}|{file_path}"
        nodes.append(NodeInfo(
            type=NodeType.CLASS,
            name=alias_name,
            namespace=namespace,
            file_path=file_path,
            start_line=cursor.extent.start.line,
            end_line=cursor.extent.end.line,
            extra_info={
                "is_type_alias": True,
                "target_type": target_type,
            },
            unique_key=alias_key,
        ))

        # type_alias 边: alias → target
        # 注意: target 可能不在 DB 中（是外部类型），此时边会在入库时跳过
        target_key = f"{NodeType.CLASS.value}|{namespace}|{target_type}|{file_path}"
        edges.append(EdgeInfo(
            relation_type=RelationType.TYPE_ALIAS,
            from_unique_key=alias_key,
            to_unique_key=target_key,
            extra_info={
                "alias_name": alias_name,
                "target_type": target_type,
            },
        ))

    def _process_typedef(
        self, cursor, should_include_fn,
        nodes: list[NodeInfo], edges: list[EdgeInfo],
    ):
        """处理 typedef TargetType Alias;"""
        if not cursor.location.file or not should_include_fn(cursor):
            return

        alias_name = cursor.spelling
        target_type = ""
        if cursor.underlying_typedef_type:
            target_type = cursor.underlying_typedef_type.spelling

        if not alias_name or not target_type:
            return

        namespace = self._get_namespace(cursor)
        file_path = str(cursor.location.file.name)

        alias_key = f"{NodeType.CLASS.value}|{namespace}|{alias_name}|{file_path}"
        nodes.append(NodeInfo(
            type=NodeType.CLASS,
            name=alias_name,
            namespace=namespace,
            file_path=file_path,
            start_line=cursor.extent.start.line,
            end_line=cursor.extent.end.line,
            extra_info={
                "is_type_alias": True,
                "target_type": target_type,
                "is_typedef": True,
            },
            unique_key=alias_key,
        ))

        target_key = f"{NodeType.CLASS.value}|{namespace}|{target_type}|{file_path}"
        edges.append(EdgeInfo(
            relation_type=RelationType.TYPE_ALIAS,
            from_unique_key=alias_key,
            to_unique_key=target_key,
            extra_info={
                "alias_name": alias_name,
                "target_type": target_type,
            },
        ))

    def _process_using_declaration(
        self, cursor, should_include_fn,
        edges: list[EdgeInfo],
    ):
        """处理 using Base::func;"""
        if not cursor.location.file or not should_include_fn(cursor):
            return

        # using 声明引用的名称
        referenced = cursor.referenced
        if not referenced:
            return

        func_name = referenced.spelling
        if not func_name:
            return

        # 找到基类名
        base_class = self._get_parent_class_name(referenced)
        derived_class = self._get_parent_class_name(cursor)

        if not base_class or not derived_class:
            return

        namespace = self._get_namespace(cursor)
        file_path = str(cursor.location.file.name)

        # using_decl 边: 子类::func → 基类::func
        from_key = f"{NodeType.FUNCTION.value}|{namespace}::{derived_class}|{func_name}|{file_path}"
        to_key = f"{NodeType.FUNCTION.value}|{namespace}::{base_class}|{func_name}|{file_path}"

        edges.append(EdgeInfo(
            relation_type=RelationType.USING_DECL,
            from_unique_key=from_key,
            to_unique_key=to_key,
            extra_info={
                "function_name": func_name,
                "base_class": base_class,
                "derived_class": derived_class,
            },
        ))

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

    @staticmethod
    def _get_parent_class_name(cursor) -> str | None:
        """获取父类名"""
        parent = cursor.semantic_parent
        if parent and parent.kind in (CursorKind.CLASS_DECL,
                                       CursorKind.STRUCT_DECL):
            return parent.spelling
        return None
