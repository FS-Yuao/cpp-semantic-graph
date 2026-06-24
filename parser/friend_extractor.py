"""友元关系提取器

提取:
- friend class Foo       → friend_of 边 (Foo → 本类)
- friend void func()     → friend_of 边 (func → 本类)
"""

from __future__ import annotations

import logging
from clang.cindex import CursorKind

from ..db.relation_types import RelationType
from ..parser.models import NodeInfo, EdgeInfo, NodeType

logger = logging.getLogger(__name__)


class FriendExtractor:
    """友元关系提取器"""

    def extract_friends(
        self, tu_cursor, should_include_fn,
    ) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """提取友元关系

        Returns:
            (nodes, edges) 元组
        """
        nodes: list[NodeInfo] = []
        edges: list[EdgeInfo] = []

        for cursor in tu_cursor.walk_preorder():
            if cursor.kind != CursorKind.FRIEND_DECL:
                continue
            if not cursor.location.file or not should_include_fn(cursor):
                continue

            # 获取友元声明的内容
            friend_name = ""
            friend_type = ""

            # friend class Foo
            for child in cursor.get_children():
                if child.kind in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
                    friend_name = child.spelling
                    friend_type = "class"
                    break
                elif child.kind == CursorKind.CXX_METHOD:
                    friend_name = child.spelling
                    friend_type = "function"
                    break
                elif child.kind == CursorKind.FUNCTION_DECL:
                    friend_name = child.spelling
                    friend_type = "function"
                    break

            if not friend_name:
                # 尝试从 displayname 获取
                friend_name = cursor.spelling or ""

            if not friend_name:
                continue

            # 找到声明友元的类（即 "本类"）
            parent_class = self._get_parent_class_name(cursor)
            if not parent_class:
                continue

            namespace = self._get_namespace(cursor)
            file_path = str(cursor.location.file.name)

            # friend_of 边: friend → 本类
            # friend 可能不在 DB 中（如外部类），边入库时可能跳过
            if friend_type == "class":
                friend_key = f"{NodeType.CLASS.value}|{namespace}|{friend_name}|{file_path}"
            else:
                friend_key = f"{NodeType.FUNCTION.value}|{namespace}|{friend_name}|{file_path}"

            host_key = f"{NodeType.CLASS.value}|{namespace}|{parent_class}|{file_path}"

            edges.append(EdgeInfo(
                relation_type=RelationType.FRIEND_OF,
                from_unique_key=friend_key,
                to_unique_key=host_key,
                extra_info={
                    "friend_name": friend_name,
                    "friend_type": friend_type,
                    "host_class": parent_class,
                },
            ))

        return nodes, edges

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
        """获取友元声明的宿主类名"""
        parent = cursor.semantic_parent
        if parent and parent.kind in (CursorKind.CLASS_DECL,
                                       CursorKind.STRUCT_DECL):
            return parent.spelling
        return None
