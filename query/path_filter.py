"""路径过滤器

用于多跳遍历时的节点/边过滤条件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PathFilter:
    """路径过滤条件"""

    node_types: list[str] | None = None
    """只保留指定类型的节点 (class / function / struct)"""

    relation_types: list[str] | None = None
    """只沿指定关系类型遍历 (inherits_public / calls_direct / ...)"""

    namespaces: list[str] | None = None
    """只保留指定命名空间下的节点"""

    name_pattern: str | None = None
    """按名称正则模式过滤节点"""

    class_name: str | None = None
    """只保留指定所属类的节点（函数）"""

    function_name: str | None = None
    """只保留指定函数名的节点"""

    def matches_node(self, node: dict) -> bool:
        """检查节点是否满足过滤条件

        Args:
            node: 节点信息 dict (name, type, namespace, file_path, ...)

        Returns:
            True 表示通过过滤
        """
        if self.node_types:
            if node.get("type", "") not in self.node_types:
                return False

        if self.namespaces:
            ns = node.get("namespace", "")
            if not any(ns_prefix in ns for ns_prefix in self.namespaces):
                return False

        if self.name_pattern:
            name = node.get("name", "")
            if not re.search(self.name_pattern, name):
                return False

        if self.class_name:
            # 检查节点所属类（通过 extra_info 或 namespace）
            ns = node.get("namespace", "")
            if self.class_name not in ns:
                return False

        if self.function_name:
            name = node.get("name", "")
            if name != self.function_name:
                return False

        return True

    def matches_edge(self, relation_type: str) -> bool:
        """检查边类型是否满足过滤条件

        Args:
            relation_type: 边的关系类型

        Returns:
            True 表示通过过滤
        """
        if self.relation_types:
            if relation_type not in self.relation_types:
                return False

        return True
