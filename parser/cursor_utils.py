"""
libclang cursor 工具函数（P2-3：消除多处重复定义）

_get_namespace / _get_parent_class_name 原在 ast_visitor / alias_extractor /
friend_extractor 各复制一份（逻辑完全一致），统一到此。
纯函数，仅依赖 clang.cindex，无项目内部依赖。
"""

from clang.cindex import CursorKind


def get_namespace(cursor) -> str:
    """获取 cursor 的完整命名空间路径（不含类名），如 "ota_manager::detail"。

    只收集 NAMESPACE 层级，类/结构体不计入（与原各处实现一致）。
    """
    parts = []
    parent = cursor.semantic_parent
    while parent:
        if parent.kind == CursorKind.NAMESPACE:
            parts.append(parent.spelling)
        parent = parent.semantic_parent
    return "::".join(reversed(parts)) if parts else ""


def get_parent_class_name(cursor) -> str | None:
    """获取 cursor 所属类/结构体名（父级为 CLASS_DECL/STRUCT_DECL 时），否则 None。"""
    parent = cursor.semantic_parent
    if parent and parent.kind in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
        return parent.spelling
    return None
