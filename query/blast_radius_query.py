"""爆炸半径（blast radius）查询

输入"改动符号/文件"，输出"受影响文件清单 + 分层调用链"，用于改动前评估影响面。

核心思路：编排已有查询能力（CallQuery.get_call_chain 递归调用链 +
PolymorphismQuery 虚函数 override 展开 + GraphQuery 文件符号/继承），
聚合到文件维度去重并按跳数分层，形成"需 review 的最小文件集"产品形态。

与现有工具边界：
- cpp_get_callers：一跳直接调用方（快，局部）
- cpp_traverse_graph：通用多关系遍历（灵活但平铺输出）
- cpp_blast_radius（本模块）：专注"改动影响面"，递归 + 虚分派展开 + 文件聚合 + 分层
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from ..db.graph_db import GraphDB
from .call_query import CallQuery, CallChainNode
from .graph_query import GraphQuery
from .polymorphism_query import PolymorphismQuery

logger = logging.getLogger(__name__)


@dataclass
class BlastNode:
    """爆炸半径中的受影响节点（去重后，保留最短跳数）"""
    function_name: str
    class_name: str | None
    namespace: str
    file_path: str
    depth: int                       # 距最近起点的跳数（1=直接受影响）
    call_type: str | None            # 到达此节点的调用类型 / 标注
    origin_symbols: list[str] = field(default_factory=list)  # 经由哪个起点到达


@dataclass
class BlastResult:
    """爆炸半径查询结果"""
    origin_functions: list[dict] = field(default_factory=list)   # 起点函数
    origin_classes: list[dict] = field(default_factory=list)     # 起点类
    expanded_overrides: list[dict] = field(default_factory=list)  # 展开的 override
    expanded_subclasses: list[dict] = field(default_factory=list)  # 展开的子类
    affected_nodes: list[BlastNode] = field(default_factory=list)  # 受影响节点（按 depth 排序）
    affected_files: dict[str, list[BlastNode]] = field(default_factory=dict)  # 文件 → 节点
    max_depth_reached: int = 0
    truncated: bool = False


class BlastRadiusQuery:
    """爆炸半径查询编排器"""

    # depth 上限（比 traverse 的 6 略紧，防虚分派展开指数膨胀）
    MAX_DEPTH = 5
    # 受影响节点软上限（超限截断并提示）
    MAX_NODES = 500

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db = GraphDB(db_path)
        self.gq = GraphQuery(db_path)
        # CallQuery 的 expand_virtual 由 compute() 按 include_overrides 重建
        self.pq = PolymorphismQuery(db_path)

    def close(self):
        for q in (self.gq, self.pq):
            try:
                q.close()
            except Exception:
                pass
        try:
            self.db.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def compute(
        self,
        symbols: list[str] | None = None,
        files: list[str] | None = None,
        depth: int = 3,
        include_overrides: bool = True,
        include_subclasses: bool = True,
        direction: str = "up",
    ) -> BlastResult:
        """计算爆炸半径

        Args:
            symbols: 起点符号名列表（函数名/类名）
            files: 起点文件路径列表（部分匹配，展开为文件内符号）
            depth: 最大递归跳数 [1, 5]
            include_overrides: 是否展开虚函数 override（多态调度方受影响）
            include_subclasses: 是否展开类的直接子类
            direction: "up"=谁受影响（被谁调用，默认）; "down"=依赖什么（调用了谁）

        Returns:
            BlastResult
        """
        depth = max(1, min(depth, self.MAX_DEPTH))
        if direction not in ("up", "down"):
            direction = "up"

        result = BlastResult()

        # --- 1. 收集起点（函数 + 类）---
        func_origins: list[dict] = []   # {name, class_name, is_virtual, file_path}
        class_origins: list[dict] = []  # {name, namespace, file_path}
        self._collect_origins(symbols, files, func_origins, class_origins)
        result.origin_functions = func_origins
        result.origin_classes = class_origins

        if not func_origins and not class_origins:
            return result

        # --- 2. 展开虚函数 override + 类子类/成员函数 ---
        # 受影响节点表：key=(name, class_name, file_path) → BlastNode（保留最小 depth）
        node_map: dict[tuple, BlastNode] = {}
        # 额外 call_chain 起点（override、类成员函数）
        chain_seeds: list[tuple[str, str | None, bool]] = []  # (func_name, class_name, is_virtual)

        for fo in func_origins:
            chain_seeds.append((fo["name"], fo["class_name"], fo["is_virtual"]))
            # 虚函数：override 本身受影响（需同步修改签名）+ 作为额外起点追 callers
            if fo["is_virtual"] and include_overrides and fo["class_name"]:
                overrides = self.pq.get_all_overrides(fo["name"], fo["class_name"])
                for o in overrides:
                    result.expanded_overrides.append({
                        "function_name": o.function_name,
                        "class_name": o.class_name,
                        "namespace": o.namespace,
                        "file_path": o.file_path,
                        "base_class": o.base_class,
                    })
                    # override 本身作为 depth=1 受影响节点
                    self._add_node(node_map, o.function_name, o.class_name,
                                   o.namespace, o.file_path, 1, "override(需同步)")
                    # override 也作为起点追其 callers
                    chain_seeds.append((o.function_name, o.class_name, False))

        for co in class_origins:
            # 类的成员函数作为起点
            methods = self._get_class_methods(co["name"])
            for m in methods:
                chain_seeds.append((m["name"], co["name"], bool(m["is_virtual"])))
            # 直接子类（受影响类）
            if include_subclasses:
                children = self.gq.get_inheritance(co["name"], direction="down", depth=1)
                for info in children:
                    child = info.child
                    result.expanded_subclasses.append({
                        "name": child.name,
                        "namespace": child.namespace,
                        "file_path": child.file_path,
                        "parent": co["name"],
                    })
                    # 子类成员函数也作为起点（子类受影响，其方法可能被调用）
                    child_methods = self._get_class_methods(child.name)
                    for m in child_methods:
                        chain_seeds.append((m["name"], child.name, bool(m["is_virtual"])))

        # --- 3. 递归调用链 ---
        cq = CallQuery(self.db_path, expand_virtual=include_overrides)
        try:
            for func_name, class_name, _is_virt in chain_seeds:
                chain = cq.get_call_chain(
                    func_name, class_name=class_name,
                    direction=direction, depth=depth,
                )
                for cn in chain:
                    self._add_node(node_map, cn.function_name, cn.class_name,
                                   cn.namespace, cn.file_path, cn.depth, cn.call_type)
        finally:
            cq.close()

        # --- 4. 排序 + 截断 + 文件聚合 ---
        # get_call_chain(depth=N) 的既有语义是"递归 N 层"，实际产生到 depth=N+1 的节点
        # （_walk_call_chain 用 current_depth > max_depth 判停，append 用 current_depth+1）。
        # blast_radius 承诺"depth=N = 最多 N 跳"，在此收敛：过滤超 depth 的节点。
        nodes = [n for n in node_map.values() if n.depth <= depth]
        nodes.sort(key=lambda n: (n.depth, n.file_path, n.function_name))
        if len(nodes) > self.MAX_NODES:
            nodes = nodes[:self.MAX_NODES]
            result.truncated = True
        result.affected_nodes = nodes
        result.max_depth_reached = max((n.depth for n in nodes), default=0)

        file_map: dict[str, list[BlastNode]] = defaultdict(list)
        for n in nodes:
            file_map[n.file_path].append(n)
        result.affected_files = dict(sorted(file_map.items()))

        return result

    # ------------------------------------------------------------------
    # 起点收集
    # ------------------------------------------------------------------

    def _collect_origins(
        self,
        symbols: list[str] | None,
        files: list[str] | None,
        func_origins: list[dict],
        class_origins: list[dict],
    ) -> None:
        """从 symbols/files 收集起点符号"""
        seen_func: set[tuple] = set()
        seen_class: set[str] = set()

        # 符号名 → 函数/类
        for sym in symbols or []:
            sym = sym.strip()
            if not sym:
                continue
            # 尝试函数（支持 "Class::func" 形式拆分）
            cls_part: str | None = None
            func_part = sym
            if "::" in sym:
                parts = sym.rsplit("::", 1)
                cls_part, func_part = parts[0], parts[1]
            for fi in self.gq.search_function(func_part, class_name=cls_part):
                key = (fi.name, fi.class_name, fi.file_path)
                if key in seen_func:
                    continue
                seen_func.add(key)
                func_origins.append({
                    "name": fi.name,
                    "class_name": fi.class_name,
                    "namespace": fi.namespace,
                    "file_path": fi.file_path,
                    "is_virtual": fi.is_virtual,
                })
            # 尝试类
            for ci in self.gq.search_class(sym, exact=False):
                if ci.name in seen_class:
                    continue
                seen_class.add(ci.name)
                class_origins.append({
                    "name": ci.name,
                    "namespace": ci.namespace,
                    "file_path": ci.file_path,
                })

        # 文件 → 文件内符号
        for fp in files or []:
            fp = fp.strip()
            if not fp:
                continue
            for si in self.gq.get_file_symbols(fp):
                if si.node_type == "function":
                    cls_name = self._namespace_to_class(si.namespace)
                    key = (si.name, cls_name, si.file_path)
                    if key in seen_func:
                        continue
                    seen_func.add(key)
                    is_virt = bool(si.extra.get("is_virtual")) if si.extra else False
                    func_origins.append({
                        "name": si.name,
                        "class_name": cls_name,
                        "namespace": si.namespace,
                        "file_path": si.file_path,
                        "is_virtual": is_virt,
                    })
                elif si.node_type in ("class", "struct"):
                    if si.name in seen_class:
                        continue
                    seen_class.add(si.name)
                    class_origins.append({
                        "name": si.name,
                        "namespace": si.namespace,
                        "file_path": si.file_path,
                    })

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _get_class_methods(self, class_name: str) -> list[dict]:
        """查询类的成员函数（通过 node.parent_class 精确匹配）

        parent_class 列存简单类名（如 'BasePeriUpdate'），与 namespace 末段一致。
        """
        rows = self.db.conn.execute(
            "SELECT name, namespace, file_path, is_virtual, is_pure_virtual "
            "FROM node WHERE type='function' AND parent_class = ?",
            (class_name,),
        ).fetchall()
        return [
            {"name": r[0], "namespace": r[1] or "", "file_path": r[2] or "",
             "is_virtual": bool(r[3])}
            for r in rows
        ]

    @staticmethod
    def _namespace_to_class(namespace: str) -> str | None:
        """namespace '::' 分隔路径，末段=所属类名"""
        if not namespace:
            return None
        parts = [p for p in namespace.split("::") if p]
        return parts[-1] if parts else None

    @staticmethod
    def _add_node(
        node_map: dict[tuple, BlastNode],
        name: str,
        class_name: str | None,
        namespace: str,
        file_path: str,
        depth: int,
        call_type: str | None,
    ) -> None:
        """加入受影响节点，同节点取最小 depth（最短路径=最直接影响）"""
        key = (name, class_name, file_path)
        existing = node_map.get(key)
        if existing is None:
            node_map[key] = BlastNode(
                function_name=name, class_name=class_name, namespace=namespace,
                file_path=file_path, depth=depth, call_type=call_type,
            )
        else:
            if depth < existing.depth:
                existing.depth = depth
            # 合并 call_type 标注
            if call_type and call_type not in (existing.call_type or ""):
                existing.call_type = f"{existing.call_type}/{call_type}" if existing.call_type else call_type
