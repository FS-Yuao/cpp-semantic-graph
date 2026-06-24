"""多跳遍历查询

从指定节点出发沿多种关系类型遍历 N 跳，支持路径过滤和组合查询。
支持 BFS（影响面分析）和 DFS（调用链追踪）两种模式。
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field

from ..db.graph_db import GraphDB
from ..db.relation_types import RelationType
from .path_filter import PathFilter

logger = logging.getLogger(__name__)


@dataclass
class Path:
    """从起点到终点的路径"""
    start_key: str              # 起点 unique_key
    end_key: str                # 终点 unique_key
    hop_count: int              # 跳数
    edges: list[dict]           # 路径上的边序列


@dataclass
class TraverseStats:
    """遍历统计信息"""
    total_nodes_visited: int = 0
    total_edges_traversed: int = 0
    max_depth_reached: int = 0
    truncated: bool = False     # 是否因 max_results 截断


@dataclass
class TraverseResult:
    """遍历结果"""
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)
    stats: TraverseStats = field(default_factory=TraverseStats)


def _parse_extra(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


class TraverseQuery:
    """多跳遍历查询"""

    def __init__(self, db_path: str):
        self.db = GraphDB(db_path)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def traverse_graph(
        self,
        start: str | list[str],
        relation_types: list[str] | None = None,
        direction: str = "outgoing",
        depth: int = 3,
        mode: str = "bfs",
        filters: PathFilter | None = None,
        max_results: int = 100,
    ) -> TraverseResult:
        """多跳遍历查询

        Args:
            start: 起始节点名称或名称列表
            relation_types: 遍历的关系类型列表（None 表示所有类型）
            direction: "outgoing" 从 from 到 to / "incoming" 反向
            depth: 最大遍历深度
            mode: "bfs" 逐层展开 / "dfs" 沿路径深入
            filters: 路径过滤条件
            max_results: 最大返回节点数

        Returns:
            TraverseResult 遍历结果
        """
        if isinstance(start, str):
            start = [start]

        result = TraverseResult()

        # 找到起始节点
        start_ids: list[tuple[int, str]] = []  # (node_id, unique_key)
        for name in start:
            nodes = self.db.find_node_by_name(name)
            for n in nodes:
                start_ids.append((n["id"], n["unique_key"]))

        if not start_ids:
            return result

        if mode == "bfs":
            self._bfs_traverse(start_ids, relation_types, direction,
                                depth, filters, max_results, result)
        else:
            self._dfs_traverse(start_ids, relation_types, direction,
                                depth, filters, max_results, result)

        return result

    def _bfs_traverse(
        self,
        start_ids: list[tuple[int, str]],
        relation_types: list[str] | None,
        direction: str,
        max_depth: int,
        filters: PathFilter | None,
        max_results: int,
        result: TraverseResult,
    ):
        """BFS 遍历"""
        visited: set[int] = set()
        # 队列: (node_id, unique_key, depth, path_edges)
        queue: deque[tuple[int, str, int, list[dict]]] = deque()

        # 初始化队列
        for nid, nkey in start_ids:
            queue.append((nid, nkey, 0, []))
            visited.add(nid)

        while queue:
            node_id, node_key, current_depth, path_edges = queue.popleft()

            if current_depth > max_depth:
                continue

            # 获取节点信息
            node = self.db.get_node_by_id(node_id)
            if not node:
                continue

            # 过滤检查
            if filters and not filters.matches_node(node):
                # 起始节点不过滤
                if current_depth > 0:
                    continue

            # 添加节点到结果
            if current_depth > 0:  # 不重复添加起始节点
                result.nodes.append(node)
                if path_edges:
                    result.paths.append(Path(
                        start_key=start_ids[0][1],
                        end_key=node["unique_key"] if "unique_key" in node else "",
                        hop_count=current_depth,
                        edges=path_edges,
                    ))

            result.stats.total_nodes_visited += 1
            result.stats.max_depth_reached = max(
                result.stats.max_depth_reached, current_depth
            )

            if len(result.nodes) >= max_results:
                result.stats.truncated = True
                break

            # 扩展下一层
            if current_depth < max_depth:
                neighbors = self._get_neighbors(
                    node_id, relation_types, direction, filters
                )
                for neighbor_id, edge_info in neighbors:
                    if neighbor_id in visited:
                        continue
                    visited.add(neighbor_id)

                    result.edges.append(edge_info)
                    result.stats.total_edges_traversed += 1

                    neighbor_node = self.db.get_node_by_id(neighbor_id)
                    nkey = neighbor_node["unique_key"] if neighbor_node else ""

                    queue.append((neighbor_id, nkey, current_depth + 1,
                                  path_edges + [edge_info]))

    def _dfs_traverse(
        self,
        start_ids: list[tuple[int, str]],
        relation_types: list[str] | None,
        direction: str,
        max_depth: int,
        filters: PathFilter | None,
        max_results: int,
        result: TraverseResult,
    ):
        """DFS 遍历"""
        visited: set[int] = set()

        def dfs(node_id: int, node_key: str, current_depth: int,
                path_edges: list[dict]):
            if current_depth > max_depth:
                return
            if len(result.nodes) >= max_results:
                result.stats.truncated = True
                return
            if node_id in visited:
                return

            visited.add(node_id)

            node = self.db.get_node_by_id(node_id)
            if not node:
                return

            # 过滤检查
            if filters and not filters.matches_node(node):
                if current_depth > 0:
                    return

            # 添加节点到结果
            if current_depth > 0:
                result.nodes.append(node)
                if path_edges:
                    result.paths.append(Path(
                        start_key=start_ids[0][1],
                        end_key=node.get("unique_key", ""),
                        hop_count=current_depth,
                        edges=path_edges,
                    ))

            result.stats.total_nodes_visited += 1
            result.stats.max_depth_reached = max(
                result.stats.max_depth_reached, current_depth
            )

            # 扩展下一层
            if current_depth < max_depth:
                neighbors = self._get_neighbors(
                    node_id, relation_types, direction, filters
                )
                for neighbor_id, edge_info in neighbors:
                    if neighbor_id in visited:
                        continue

                    result.edges.append(edge_info)
                    result.stats.total_edges_traversed += 1

                    dfs(neighbor_id, "", current_depth + 1,
                        path_edges + [edge_info])

        for nid, nkey in start_ids:
            dfs(nid, nkey, 0, [])

    def _get_neighbors(
        self,
        node_id: int,
        relation_types: list[str] | None,
        direction: str,
        filters: PathFilter | None,
    ) -> list[tuple[int, dict]]:
        """获取节点的邻居

        继承边方向约定: from=子类, to=基类
        - outgoing + inherits → 查子类: 需要查指向自己的边 (incoming)
        - incoming + inherits → 查基类: 需要查自己出发的边 (outgoing)

        为符合用户直觉（"outgoing 从当前类向外找子类"），
        对继承关系自动翻转物理方向。
        """
        neighbors: list[tuple[int, dict]] = []
        inherits_types = {rt.value for rt in RelationType.inherits_types()}
        belongs_types = {"belongs_to"}

        # 收集边：按关系类型分组，可能需要两个方向都查
        edges_out = self.db.get_edges_from(node_id)
        edges_in = self.db.get_edges_to(node_id)

        for edge in edges_out + edges_in:
            rt = edge["relation_type"]

            # 关系类型过滤
            if relation_types and rt not in relation_types:
                continue
            if filters and not filters.matches_edge(rt):
                continue

            # 确定邻居方向
            # edge 在 edges_out 中: from=current, to=neighbor → 物理 outgoing
            # edge 在 edges_in 中: from=neighbor, to=current → 物理 incoming
            is_from_current = (edge["from_id"] == node_id)

            # 逻辑方向判定
            # 继承边: from=子类, to=基类
            #   outgoing (找子类) → 物理 incoming (别人指向我)
            #   incoming (找基类) → 物理 outgoing (我指向别人)
            # belongs_to 边: from=成员, to=所属类
            #   outgoing (找成员) → 物理 incoming (成员指向我)
            #   incoming (找所属类) → 物理 outgoing (我指向所属类)
            # 调用边: from=调用方, to=被调用方
            #   outgoing (找被调用方) → 物理 outgoing (我指向别人)
            #   incoming (找调用方) → 物理 incoming (别人指向我)

            if rt in inherits_types or rt in belongs_types:
                # 继承/belongs_to: 方向翻转
                want_physical_in = (direction == "outgoing")
            else:
                # 调用等: 方向一致
                want_physical_in = (direction == "incoming")

            # 判断此边是否符合想要的物理方向
            if want_physical_in:
                # 要 incoming: 邻居在 from 端
                if not is_from_current:
                    neighbor_id = edge["from_id"]
                else:
                    continue
            else:
                # 要 outgoing: 邻居在 to 端
                if is_from_current:
                    neighbor_id = edge["to_id"]
                else:
                    continue

            edge_info = {
                "relation_type": rt,
                "from_id": edge["from_id"],
                "to_id": edge["to_id"],
                "extra_info": _parse_extra(edge.get("extra_info", {})),
            }

            neighbors.append((neighbor_id, edge_info))

        return neighbors
