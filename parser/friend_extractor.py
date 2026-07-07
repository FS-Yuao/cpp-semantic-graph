"""友元关系提取器

提取:
- friend class Foo       → friend_of 边 (Foo → 本类)
- friend void func()     → friend_of 边 (func → 本类)

集成方式：由 SemanticExtractor.parse() 调用，传入 config 与路径转换回调，
确保 file_path 与 ast_visitor 走同一套相对路径转换（避免 unique_key 错配）。

节点策略：friend 实体若是项目源码范围内的类/函数，作为节点入库（让 friend_of
边的 from 端可解析）；若来自外部库则边入库时丢弃，但 host（本类）节点一定在库。
"""

from __future__ import annotations

import logging
from clang.cindex import CursorKind

from ..db.relation_types import RelationType
from ..parser.models import NodeInfo, EdgeInfo, NodeType, make_func_sig_suffix
from .cursor_utils import get_namespace, get_parent_class_name  # P2-3: 统一 cursor 工具

logger = logging.getLogger(__name__)


class FriendExtractor:
    """友元关系提取器"""

    def extract_friends(
        self, tu_cursor, config, make_file_path,
    ) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """提取友元关系

        Args:
            tu_cursor: 翻译单元的根 cursor
            config: ProjectConfig，用于过滤
            make_file_path: 路径转换回调（与 ast_viewer._make_file_path 同源）

        Returns:
            (nodes, edges) 元组。nodes 仅含项目源码范围内的 friend 实体节点，
            用于让 friend_of 边的 from 端可解析。
        """
        nodes: list[NodeInfo] = []
        edges: list[EdgeInfo] = []

        for cursor in tu_cursor.walk_preorder():
            if cursor.kind != CursorKind.FRIEND_DECL:
                continue
            if not cursor.location.file:
                continue
            abs_path = str(cursor.location.file.name)
            if not config.should_extract_node(abs_path):
                continue

            # 解析友元声明的目标（class / function）
            friend_name = ""
            friend_type = ""
            friend_cursor = None
            for child in cursor.get_children():
                if child.kind in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
                    friend_name = child.spelling
                    friend_type = "class"
                    friend_cursor = child
                    break
                elif child.kind in (CursorKind.CXX_METHOD, CursorKind.FUNCTION_DECL):
                    friend_name = child.spelling
                    friend_type = "function"
                    friend_cursor = child
                    break

            if not friend_name:
                friend_name = cursor.spelling or ""
            if not friend_name:
                continue

            # 声明友元的宿主类（"本类"）
            parent_class = self._get_parent_class_name(cursor)
            if not parent_class:
                continue

            namespace = self._get_namespace(cursor)
            file_path = make_file_path(abs_path)

            # friend 节点 key（friend_type 决定节点类型）
            if friend_type == "class":
                friend_key = f"{NodeType.CLASS.value}|{namespace}|{friend_name}|{file_path}"
                # 若 friend 实体 cursor 存在且是定义，作为节点入库，
                # 让 friend_of 边的 from 端可解析。
                if friend_cursor and friend_cursor.is_definition():
                    nodes.append(NodeInfo(
                        type=NodeType.CLASS,
                        name=friend_name,
                        namespace=namespace,
                        file_path=file_path,
                        start_line=friend_cursor.extent.start.line,
                        end_line=friend_cursor.extent.end.line,
                        extra_info={"is_friend": True},
                        unique_key=friend_key,
                    ))
            else:
                # friend function key 需含参数签名（与 ast_visitor._make_function_key 对齐），
                # 否则重载友元函数的 friend_of 边 from 端解析失配。
                friend_params = []
                friend_is_const = False
                if friend_cursor is not None:
                    friend_params = [
                        (a.type.spelling if a.type else a.spelling)
                        for a in friend_cursor.get_arguments()
                        if a.kind == CursorKind.PARM_DECL
                    ]
                    if friend_cursor.kind == CursorKind.CXX_METHOD:
                        friend_is_const = friend_cursor.is_const_method()
                sig_suffix = make_func_sig_suffix(friend_params, friend_is_const)
                friend_key = (
                    f"{NodeType.FUNCTION.value}|{namespace}|{friend_name}|{file_path}{sig_suffix}"
                )

            # host（本类）节点 key —— 本类由 ast_viewer._extract_classes 入库
            host_key = f"{NodeType.CLASS.value}|{namespace}|{parent_class}|{file_path}"

            # friend_of 边: friend → 本类
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
        """提取命名空间（P2-3：统一实现见 cursor_utils.get_namespace）"""
        return get_namespace(cursor)

    @staticmethod
    def _get_parent_class_name(cursor) -> str | None:
        """获取友元声明的宿主类名（P2-3：统一实现见 cursor_utils.get_parent_class_name）"""
        return get_parent_class_name(cursor)
