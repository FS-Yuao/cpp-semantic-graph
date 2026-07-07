"""类型别名 / using 声明提取器

提取:
- using Alias = Base<T>   → type_alias 边 (Alias → Base<T>)
- typedef Base<T> Alias   → type_alias 边 (Alias → Base<T>)
- using Base::func        → using_decl 边 (子类::func → Base::func)

集成方式：由 SemanticExtractor.parse() 调用，传入 config 与路径转换回调，
确保 file_path 与 ast_visitor 走同一套相对路径转换（避免 unique_key 错配）。

已知限制：target 类型可能来自外部库（如 std::xxx、ara::com::Proxy），
其 namespace/file_path 无法从别名自身获取，导致 target_key 指向不存在的节点、
type_alias 边被入库阶段丢弃。此时别名节点本身仍会入库（含 target_type 元信息），
查询时可通过 extra_info.target_type 回溯目标类型。
"""

from __future__ import annotations

import logging
from clang.cindex import CursorKind

from ..db.relation_types import RelationType
from ..parser.models import NodeInfo, EdgeInfo, NodeType
from .cursor_utils import get_namespace, get_parent_class_name  # P2-3: 统一 cursor 工具

logger = logging.getLogger(__name__)


class AliasExtractor:
    """类型别名和 using 声明提取器"""

    def extract_type_aliases(
        self, tu_cursor, config, make_file_path,
    ) -> tuple[list[NodeInfo], list[EdgeInfo]]:
        """提取类型别名关系

        Args:
            tu_cursor: 翻译单元的根 cursor
            config: ProjectConfig，用于过滤
            make_file_path: 路径转换回调（与 ast_viewer._make_file_path 同源）

        Returns:
            (nodes, edges) 元组
        """
        nodes: list[NodeInfo] = []
        edges: list[EdgeInfo] = []

        for cursor in tu_cursor.walk_preorder():
            if cursor.kind == CursorKind.TYPE_ALIAS_DECL:
                # using Alias = TargetType;
                self._process_type_alias(cursor, config, make_file_path, nodes, edges)
            elif cursor.kind == CursorKind.TYPEDEF_DECL:
                # typedef TargetType Alias;
                self._process_typedef(cursor, config, make_file_path, nodes, edges)
            elif cursor.kind == CursorKind.USING_DECLARATION:
                # using Base::func;
                self._process_using_declaration(cursor, config, make_file_path, edges)

        return nodes, edges

    def _process_type_alias(
        self, cursor, config, make_file_path,
        nodes: list[NodeInfo], edges: list[EdgeInfo],
    ):
        """处理 using Alias = TargetType;"""
        if not cursor.location.file:
            return
        abs_path = str(cursor.location.file.name)
        if not config.should_extract_node(abs_path):
            return

        alias_name = cursor.spelling
        target_type = ""
        if cursor.underlying_typedef_type:
            target_type = cursor.underlying_typedef_type.spelling

        if not alias_name or not target_type:
            return

        namespace = self._get_namespace(cursor)
        file_path = make_file_path(abs_path)

        # 别名节点（type=CLASS，便于与类型查询统一）
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

        # type_alias 边: alias → target。
        # target 可能不在 DB（外部类型），target_key 用弱对齐命中则建边；
        # 命中不到则由 graph_db 按 target_type 末尾类名回溯（见 _resolve_hint="type_alias"）。
        # 始终保留 target_type 元信息，即便边最终丢弃，别名节点仍可查。
        target_key = self._build_target_key(target_type, namespace, file_path)
        target_simple = self._simple_class_name(target_type)
        edges.append(EdgeInfo(
            relation_type=RelationType.TYPE_ALIAS,
            from_unique_key=alias_key,
            to_unique_key=target_key,
            extra_info={
                "alias_name": alias_name,
                "target_type": target_type,
                "target_simple_name": target_simple,
                "_needs_resolution": True,
                "_resolve_hint": "type_alias",
            },
        ))

    def _process_typedef(
        self, cursor, config, make_file_path,
        nodes: list[NodeInfo], edges: list[EdgeInfo],
    ):
        """处理 typedef TargetType Alias;"""
        if not cursor.location.file:
            return
        abs_path = str(cursor.location.file.name)
        if not config.should_extract_node(abs_path):
            return

        alias_name = cursor.spelling
        target_type = ""
        if cursor.underlying_typedef_type:
            target_type = cursor.underlying_typedef_type.spelling

        if not alias_name or not target_type:
            return

        namespace = self._get_namespace(cursor)
        file_path = make_file_path(abs_path)

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

        target_key = self._build_target_key(target_type, namespace, file_path)
        target_simple = self._simple_class_name(target_type)
        edges.append(EdgeInfo(
            relation_type=RelationType.TYPE_ALIAS,
            from_unique_key=alias_key,
            to_unique_key=target_key,
            extra_info={
                "alias_name": alias_name,
                "target_type": target_type,
                "target_simple_name": target_simple,
                "is_typedef": True,
                "_needs_resolution": True,
                "_resolve_hint": "type_alias",
            },
        ))

    def _process_using_declaration(
        self, cursor, config, make_file_path,
        edges: list[EdgeInfo],
    ):
        """处理 using Base::func;"""
        if not cursor.location.file:
            return
        abs_path = str(cursor.location.file.name)
        if not config.should_extract_node(abs_path):
            return

        # using 声明引用的函数
        referenced = cursor.referenced
        if not referenced:
            return

        func_name = referenced.spelling
        if not func_name:
            return

        base_class = self._get_parent_class_name(referenced)
        derived_class = self._get_parent_class_name(cursor)

        if not base_class or not derived_class:
            return

        namespace = self._get_namespace(cursor)
        file_path = make_file_path(abs_path)

        # using_decl 边: 子类::func → 基类::func
        # 注：using 声明语义是导入基类的「所有」同名重载，不对应单一参数签名，
        # 故此处 key 为 name 级（无 params 后缀）。该边通过 _needs_resolution 的
        # using_decl hint 在 graph_db 按 base_class+func_name 解析（name 级匹配）。
        # 若基类同名函数有多个重载，当前解析取首个（LIMIT 1）——using_decl 边罕见
        # （本项目 0 条），name 级足够；如需精确到每个重载需展开为多条边（后续）。
        from_key = (
            f"{NodeType.FUNCTION.value}|{namespace}::{derived_class}"
            f"|{func_name}|{file_path}"
        )
        to_key = (
            f"{NodeType.FUNCTION.value}|{namespace}::{base_class}"
            f"|{func_name}|{file_path}"
        )

        edges.append(EdgeInfo(
            relation_type=RelationType.USING_DECL,
            from_unique_key=from_key,
            to_unique_key=to_key,
            extra_info={
                "function_name": func_name,
                "base_class": base_class,
                "derived_class": derived_class,
                "_needs_resolution": True,
                "_resolve_hint": "using_decl",
            },
        ))

    @staticmethod
    def _build_target_key(target_type: str, namespace: str, file_path: str) -> str:
        """根据目标类型拼写构造 target_key

        target_type 形如 "Base<T>"、ara::com::Proxy<...>、std::vector<int> 等。
        其 namespace/file_path 无法精确获取，这里用别名自身的 namespace/file_path
        作为弱对齐——命中则建边，不命中则由 graph_db 按 target_simple_name 回溯。
        """
        return f"{NodeType.CLASS.value}|{namespace}|{target_type}|{file_path}"

    @staticmethod
    def _simple_class_name(target_type: str) -> str:
        """从目标类型拼写提取末尾的"简单类名"，供按名回溯

        例:
          "::amsr::socal::methods::MethodParameters<std::uint8_t>" → "MethodParameters"
          "ara::core::StringView" → "StringView"
          "std::vector<int>" → "vector"
          "Base<T>::Inner" → "Inner"
        策略: 去掉模板参数 <...>、去掉命名空间 :: 前缀、取最后的标识符。
        """
        if not target_type:
            return ""
        # 去掉模板参数
        s = target_type.split("<")[0]
        # 去掉命名空间前缀，取末段
        s = s.rstrip(":").split("::")[-1]
        # 去掉可能的指针/引用修饰
        s = s.strip("&* ")
        return s

    @staticmethod
    def _get_namespace(cursor) -> str:
        """提取命名空间（P2-3：统一实现见 cursor_utils.get_namespace）"""
        return get_namespace(cursor)

    @staticmethod
    def _get_parent_class_name(cursor) -> str | None:
        """获取父类名（P2-3：统一实现见 cursor_utils.get_parent_class_name）"""
        return get_parent_class_name(cursor)
