"""多态体系查询

支持虚函数体系精准映射：查询类的所有虚函数、虚函数的所有重写实现、
接口类的所有实现子类。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..db.graph_db import GraphDB
from ..db.relation_types import RelationType
from .query_utils import parse_extra as _parse_extra  # P2-3: 统一实现

logger = logging.getLogger(__name__)


@dataclass
class VirtualFuncInfo:
    """虚函数信息"""
    function_name: str
    signature: str
    namespace: str
    class_name: str            # 首次声明的类
    file_path: str
    start_line: int
    is_pure_virtual: bool
    is_overridden: bool        # 是否有子类 override
    override_count: int        # override 的数量
    override_classes: list[str] = field(default_factory=list)  # override 的子类列表


@dataclass
class OverrideInfo:
    """虚函数重写信息"""
    function_name: str
    class_name: str
    namespace: str
    file_path: str
    line_number: int
    signature: str
    base_class: str              # 被重写的基类
    base_function_signature: str  # 基类虚函数签名


class PolymorphismQuery:
    """多态体系查询"""

    def __init__(self, db_path: str):
        self.db = GraphDB(db_path)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # 虚函数清单
    # ------------------------------------------------------------------

    def get_virtual_functions(
        self,
        class_name: str,
        include_inherited: bool = True,
    ) -> list[VirtualFuncInfo]:
        """查询类的所有虚函数

        Args:
            class_name: 类名
            include_inherited: 是否包含从基类继承的虚函数

        Returns:
            虚函数信息列表
        """
        result: list[VirtualFuncInfo] = []
        seen_names: set[str] = set()  # 避免重复

        # 1. 当前类的虚函数
        self._collect_virtual_functions(class_name, result, seen_names)

        # 2. 递归收集基类的虚函数
        if include_inherited:
            base_classes = self._get_ancestor_classes(class_name)
            for base in base_classes:
                self._collect_virtual_functions(base, result, seen_names)

        return result

    def _collect_virtual_functions(
        self,
        class_name: str,
        result: list[VirtualFuncInfo],
        seen_names: set[str],
    ):
        """收集指定类的虚函数（不含继承的）"""
        # 找到类节点
        class_nodes = self.db.find_node_by_name(class_name, "class")
        if not class_nodes:
            class_nodes = self.db.find_node_by_name(class_name, "struct")
        if not class_nodes:
            return

        class_id = class_nodes[0]["id"]

        # N+1 消除：get_edges_to 已 JOIN from 节点，用 from_type 过滤非 function
        edges = self.db.get_edges_to(class_id, "belongs_to")
        func_ids = [e["from_id"] for e in edges if e.get("from_type") == "function"]
        if not func_ids:
            return

        # N+1 消除：批量获取所有函数节点（1 条 SQL 替代 N 条）
        nodes_map = self.db.get_nodes_by_ids(func_ids)
        for func_id in func_ids:
            func_node = nodes_map.get(func_id)
            if not func_node:
                continue

            extra = _parse_extra(func_node.get("extra_info", {}))
            is_virtual = extra.get("is_virtual", False)
            if not is_virtual:
                continue

            func_name = func_node["name"]
            # 跳过重复（声明和定义的同一函数）
            dedup_key = f"{func_name}@{class_name}"
            if dedup_key in seen_names:
                continue
            seen_names.add(dedup_key)

            # 检查是否有 override
            overrides = self._find_overrides_of_func(func_id, func_name)
            is_pure = extra.get("is_pure_virtual", False)
            signature = extra.get("signature", func_name)

            result.append(VirtualFuncInfo(
                function_name=func_name,
                signature=signature,
                namespace=func_node.get("namespace", ""),
                class_name=class_name,
                file_path=func_node.get("file_path", ""),
                start_line=func_node.get("start_line", 0),
                is_pure_virtual=is_pure,
                is_overridden=len(overrides) > 0,
                override_count=len(overrides),
                override_classes=[o.class_name for o in overrides],
            ))

    # ------------------------------------------------------------------
    # 虚函数的所有重写
    # ------------------------------------------------------------------

    def get_all_overrides(
        self,
        func_name: str,
        class_name: str,
    ) -> list[OverrideInfo]:
        """查询虚函数的所有重写实现（递归所有子类）

        Args:
            func_name: 虚函数名
            class_name: 首次声明该虚函数的基类名

        Returns:
            重写信息列表
        """
        # 找基类虚函数节点
        base_func_id = self._find_function_node(func_name, class_name)
        if base_func_id is None:
            logger.debug("未找到基类虚函数: %s::%s", class_name, func_name)
            return []

        # 找 override 边: to_id = base_func_id
        results: list[OverrideInfo] = []
        seen: set[str] = set()

        # 直接查 overrides 边
        self._collect_overrides_recursive(
            base_func_id, func_name, class_name, results, seen
        )

        return results

    def _collect_overrides_recursive(
        self,
        base_func_id: int,
        func_name: str,
        base_class: str,
        results: list[OverrideInfo],
        seen: set[str],
        depth: int = 0,
    ):
        """递归收集 override（通过 overrides 边）"""
        # 深度上限防深层 override 链栈溢出（主题F）
        if depth > 20:
            logger.warning("override 递归达深度上限 20，可能截断: %s::%s", base_class, func_name)
            return

        # N+1 消除：base_func_node 只查一次（原为每条边查 2 次）
        base_func_node = self.db.get_node_by_id(base_func_id)
        base_extra = _parse_extra(
            base_func_node.get("extra_info", {}) if base_func_node else {}
        )

        # N+1 消除：owning_class 缓存
        _class_cache: dict[int, str | None] = {}

        # 查 overrides 边: to_id = base_func_id
        # get_edges_to 已 JOIN from 节点（from_name/from_namespace/from_file_path）
        override_edges = self.db.get_edges_to(base_func_id, "overrides")

        for edge in override_edges:
            derived_func_id = edge["from_id"]
            # N+1 消除：用 JOINed 数据做快速过滤
            derived_name = edge.get("from_name", "")
            derived_namespace = edge.get("from_namespace", "")
            derived_file = edge.get("from_file_path", "")
            if not derived_name:
                continue

            dedup_key = f"{derived_name}@{derived_namespace}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # 仍需 start_line / extra_info，查一次完整节点
            derived_func = self.db.get_node_by_id(derived_func_id)
            if not derived_func:
                continue

            # 找到所属类（带缓存）
            if derived_func_id not in _class_cache:
                _class_cache[derived_func_id] = self._get_owning_class(derived_func_id)
            derived_class = _class_cache[derived_func_id]

            extra = _parse_extra(derived_func.get("extra_info", {}))

            results.append(OverrideInfo(
                function_name=derived_name,
                class_name=derived_class or "",
                namespace=derived_namespace,
                file_path=derived_file,
                line_number=derived_func.get("start_line", 0),
                signature=extra.get("signature", derived_name),
                base_class=base_class,
                base_function_signature=base_extra.get("signature", func_name),
            ))

            # 递归: 派生类的 override 也可能被更深层的子类 override
            self._collect_overrides_recursive(
                derived_func_id, func_name, derived_class or base_class,
                results, seen,
            )

    # ------------------------------------------------------------------
    # 接口实现查询
    # ------------------------------------------------------------------

    def get_all_implementations(self, interface_class: str) -> list[dict]:
        """查询接口类的所有实现子类（递归所有派生）

        只返回非抽象的叶子类。

        Args:
            interface_class: 接口/抽象类名

        Returns:
            实现类信息列表 [{name, namespace, file_path}]
        """
        # 找所有子类（递归 down）
        all_descendants = self._get_all_descendants(interface_class)
        if not all_descendants:
            return []

        # 过滤掉抽象类
        implementations = []
        for desc in all_descendants:
            if not self._is_abstract_class(desc["name"]):
                implementations.append(desc)

        return implementations

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _find_function_node(
        self, func_name: str, class_name: str,
    ) -> int | None:
        """查找函数节点 ID

        N+1 消除：get_edges_to 已 JOIN from 节点（from_name），
        直接用 from_name 匹配，无需 get_node_by_id。
        """
        # 先找类节点
        class_nodes = self.db.find_node_by_name(class_name, "class")
        if not class_nodes:
            class_nodes = self.db.find_node_by_name(class_name, "struct")
        if not class_nodes:
            return None

        class_id = class_nodes[0]["id"]

        # 找属于此类的同名函数（用 JOINed from_name 匹配）
        edges = self.db.get_edges_to(class_id, "belongs_to")
        for edge in edges:
            if edge.get("from_name") == func_name:
                return edge["from_id"]

        return None

    def _find_overrides_of_func(
        self, func_id: int, func_name: str,
    ) -> list[OverrideInfo]:
        """查找函数的所有直接 override"""
        results: list[OverrideInfo] = []
        # N+1 消除：get_edges_to 已 JOIN from 节点
        override_edges = self.db.get_edges_to(func_id, "overrides")
        for edge in override_edges:
            derived_name = edge.get("from_name", "")
            derived_namespace = edge.get("from_namespace", "")
            derived_file = edge.get("from_file_path", "")
            if not derived_name:
                continue
            # 需 start_line，查一次完整节点
            derived_func = self.db.get_node_by_id(edge["from_id"])
            if not derived_func:
                continue
            derived_class = self._get_owning_class(edge["from_id"])
            results.append(OverrideInfo(
                function_name=derived_name,
                class_name=derived_class or "",
                namespace=derived_namespace,
                file_path=derived_file,
                line_number=derived_func.get("start_line", 0),
                signature="",
                base_class="",
                base_function_signature="",
            ))
        return results

    def _get_owning_class(self, func_id: int) -> str | None:
        """通过 belongs_to 边查找函数所属的类

        N+1 消除：get_edges_from 已 JOIN to 节点，直接取 to_name，
        无需二次 get_node_by_id 查询。
        """
        edges = self.db.get_edges_from(func_id, "belongs_to")
        if edges:
            return edges[0].get("to_name")
        # fallback：belongs_to 边缺失时，从函数 namespace 末段推断类名
        node = self.db.get_node_by_id(func_id)
        if node:
            ns = node.get("namespace", "") or ""
            if "::" in ns:
                return ns.rsplit("::", 1)[-1]
        return None

    def _get_ancestor_classes(self, class_name: str) -> list[str]:
        """获取所有祖先类名（递归 up）

        N+1 消除：get_edges_from 已 JOIN to 节点（to_name），
        直接用 to_name，无需 get_node_by_id。
        """
        ancestors: list[str] = []
        visited: set[str] = {class_name}
        queue = [class_name]

        while queue:
            current = queue.pop(0)
            nodes = self.db.find_node_by_name(current, "class")
            if not nodes:
                nodes = self.db.find_node_by_name(current, "struct")
            if not nodes:
                continue

            current_id = nodes[0]["id"]
            # 找继承边: from=current(子类), to=parent(基类)
            edges = self.db.get_edges_from(current_id)
            rel_types = {rt.value for rt in RelationType.inherits_types()}
            for edge in edges:
                if edge["relation_type"] not in rel_types:
                    continue
                parent_name = edge.get("to_name", "")
                if parent_name and parent_name not in visited:
                    visited.add(parent_name)
                    ancestors.append(parent_name)
                    queue.append(parent_name)

        return ancestors

    def _get_all_descendants(self, class_name: str) -> list[dict]:
        """获取所有派生类（递归 down）

        N+1 消除：get_edges_to 已 JOIN from 节点（from_name/from_namespace/from_file_path），
        直接用 JOINed 数据，无需 get_node_by_id。
        """
        descendants: list[dict] = []
        visited: set[str] = {class_name}
        queue = [class_name]

        while queue:
            current = queue.pop(0)
            nodes = self.db.find_node_by_name(current, "class")
            if not nodes:
                nodes = self.db.find_node_by_name(current, "struct")
            if not nodes:
                continue

            current_id = nodes[0]["id"]
            # 找子类: inherits 边 from=子类, to=current
            edges = self.db.get_edges_to(current_id)
            rel_types = {rt.value for rt in RelationType.inherits_types()}
            for edge in edges:
                if edge["relation_type"] not in rel_types:
                    continue
                child_name = edge.get("from_name", "")
                if child_name and child_name not in visited:
                    visited.add(child_name)
                    descendants.append({
                        "name": child_name,
                        "namespace": edge.get("from_namespace", ""),
                        "file_path": edge.get("from_file_path", ""),
                    })
                    queue.append(child_name)

        return descendants

    def _is_abstract_class(self, class_name: str) -> bool:
        """判断类是否为抽象类（含纯虚函数）

        N+1 消除：用单条 JOIN SQL 替代 N 次 get_node_by_id。
        """
        nodes = self.db.find_node_by_name(class_name, "class")
        if not nodes:
            nodes = self.db.find_node_by_name(class_name, "struct")
        if not nodes:
            return False

        class_id = nodes[0]["id"]
        # 单条 SQL：查找此类是否有纯虚函数
        row = self.db.conn.execute(
            """SELECT 1 FROM edge e
               JOIN node n ON e.from_id = n.id
               WHERE e.to_id = ? AND e.relation_type = 'belongs_to'
               AND n.is_pure_virtual = 1
               LIMIT 1""",
            (class_id,)
        ).fetchone()
        return row is not None
