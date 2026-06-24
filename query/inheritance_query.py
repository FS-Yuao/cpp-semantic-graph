"""多级继承链递归查询

支持递归查询所有祖先/子类、钻石继承检测。
构建于 GraphDB 和 GraphQuery 之上，提供更丰富的继承分析能力。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..db.graph_db import GraphDB
from ..db.relation_types import RelationType

logger = logging.getLogger(__name__)


@dataclass
class InheritanceNode:
    """继承链中的节点"""
    name: str
    namespace: str
    file_path: str
    access: str = "public"       # 继承权限
    depth: int = 0               # 距起点的深度
    is_diamond: bool = False     # 是否通过多条路径到达（钻石继承）


@dataclass
class DiamondInfo:
    """钻石继承信息"""
    base_class: str
    derived_class: str
    paths: list[list[str]]       # 从基类到派生类的所有路径
    intermediate_classes: list[str]  # 中间经过的类


class InheritanceQuery:
    """多级继承链递归查询"""

    def __init__(self, db_path: str):
        self.db = GraphDB(db_path)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # 核心: 递归继承链
    # ------------------------------------------------------------------

    def get_full_inheritance_chain(
        self,
        class_name: str,
        direction: str = "down",
        max_depth: int = -1,
    ) -> list[InheritanceNode]:
        """递归查询完整继承链

        Args:
            class_name: 起始类名
            direction: "up" 查所有祖先, "down" 查所有子类
            max_depth: 最大递归深度, -1 表示无限

        Returns:
            继承节点列表（按深度排序）
        """
        visited: dict[str, int] = {}  # name → depth
        result: list[InheritanceNode] = []
        self._walk_inheritance(class_name, direction, 0, max_depth,
                               visited, result)

        # 检测钻石继承：同一节点被多条路径到达
        seen: dict[str, int] = {}
        for node in result:
            count = seen.get(node.name, 0) + 1
            seen[node.name] = count
            if count > 1:
                node.is_diamond = True

        return result

    def _walk_inheritance(
        self,
        class_name: str,
        direction: str,
        current_depth: int,
        max_depth: int,
        visited: dict[str, int],
        result: list[InheritanceNode],
    ):
        """BFS 递归遍历继承链"""
        if max_depth >= 0 and current_depth > max_depth:
            return
        if class_name in visited:
            # 已访问但可能是更浅的路径
            if visited[class_name] <= current_depth:
                return
        visited[class_name] = current_depth

        # 找到当前类节点
        class_nodes = self.db.find_node_by_name(class_name, "class")
        if not class_nodes:
            class_nodes = self.db.find_node_by_name(class_name, "struct")
        if not class_nodes:
            return

        class_id = class_nodes[0]["id"]

        # 查继承边
        if direction == "down":
            # 找子类: inherits 边 from=子类, to=当前类
            edges = self.db.get_edges_to(class_id)
            rel_types = {rt.value for rt in RelationType.inherits_types()}
            for edge in edges:
                if edge["relation_type"] not in rel_types:
                    continue
                child_id = edge["from_id"]
                child_node = self.db.get_node_by_id(child_id)
                if not child_node:
                    continue

                # 提取继承权限
                access = "public"
                extra = edge.get("extra_info", {})
                if isinstance(extra, str):
                    import json
                    try:
                        extra = json.loads(extra)
                    except (json.JSONDecodeError, TypeError):
                        extra = {}
                if isinstance(extra, dict):
                    rt = edge["relation_type"]
                    if "protected" in rt:
                        access = "protected"
                    elif "private" in rt:
                        access = "private"

                result.append(InheritanceNode(
                    name=child_node["name"],
                    namespace=child_node.get("namespace", ""),
                    file_path=child_node.get("file_path", ""),
                    access=access,
                    depth=current_depth + 1,
                ))

                # 递归
                self._walk_inheritance(
                    child_node["name"], direction,
                    current_depth + 1, max_depth,
                    visited, result,
                )
        else:  # direction == "up"
            # 找父类: inherits 边 from=当前类, to=父类
            edges = self.db.get_edges_from(class_id)
            rel_types = {rt.value for rt in RelationType.inherits_types()}
            for edge in edges:
                if edge["relation_type"] not in rel_types:
                    continue
                parent_id = edge["to_id"]
                parent_node = self.db.get_node_by_id(parent_id)
                if not parent_node:
                    continue

                access = "public"
                rt = edge["relation_type"]
                if "protected" in rt:
                    access = "protected"
                elif "private" in rt:
                    access = "private"

                result.append(InheritanceNode(
                    name=parent_node["name"],
                    namespace=parent_node.get("namespace", ""),
                    file_path=parent_node.get("file_path", ""),
                    access=access,
                    depth=current_depth + 1,
                ))

                # 递归
                self._walk_inheritance(
                    parent_node["name"], direction,
                    current_depth + 1, max_depth,
                    visited, result,
                )

    # ------------------------------------------------------------------
    # 钻石继承检测
    # ------------------------------------------------------------------

    def get_diamond_inheritance(self, class_name: str) -> list[DiamondInfo]:
        """检测指定类中的钻石继承结构

        从指定类向下遍历所有子类，如果某子类通过多条路径
        继承同一基类，返回钻石信息。

        Returns:
            钻石继承信息列表（空列表表示无钻石继承）
        """
        chain_down = self.get_full_inheritance_chain(class_name, "down")
        if not chain_down:
            return []

        # 收集每个子类到基类的所有路径
        diamonds: list[DiamondInfo] = []

        # 找 is_diamond 的节点
        diamond_names = {n.name for n in chain_down if n.is_diamond}
        if not diamond_names:
            return []

        # 对每个钻石节点，找所有路径
        for dname in diamond_names:
            paths = self._find_inheritance_paths(class_name, dname)
            if len(paths) > 1:
                intermediates = set()
                for path in paths:
                    intermediates.update(path[1:-1])  # 去掉起点和终点
                diamonds.append(DiamondInfo(
                    base_class=class_name,
                    derived_class=dname,
                    paths=paths,
                    intermediate_classes=sorted(intermediates),
                ))

        return diamonds

    def _find_inheritance_paths(
        self,
        from_class: str,
        to_class: str,
        max_depth: int = 10,
    ) -> list[list[str]]:
        """找两个类之间的所有继承路径（DFS）"""
        paths: list[list[str]] = []

        def dfs(current: str, path: list[str], depth: int):
            if depth > max_depth:
                return
            if current == to_class:
                paths.append(path.copy())
                return
            # 防环
            if current in path[1:]:
                return

            # 找 current 的子类
            nodes = self.db.find_node_by_name(current, "class")
            if not nodes:
                nodes = self.db.find_node_by_name(current, "struct")
            if not nodes:
                return

            current_id = nodes[0]["id"]
            edges = self.db.get_edges_to(current_id)
            rel_types = {rt.value for rt in RelationType.inherits_types()}
            for edge in edges:
                if edge["relation_type"] not in rel_types:
                    continue
                child_node = self.db.get_node_by_id(edge["from_id"])
                if child_node:
                    dfs(child_node["name"], path + [child_node["name"]],
                        depth + 1)

        dfs(from_class, [from_class], 0)
        return paths
