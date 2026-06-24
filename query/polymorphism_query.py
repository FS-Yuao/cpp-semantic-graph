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


def _parse_extra(raw) -> dict:
    """安全解析 extra_info（可能是 JSON 字符串或 dict）"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


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

        # 找属于此类的函数
        edges = self.db.get_edges_to(class_id, "belongs_to")
        for edge in edges:
            func_id = edge["from_id"]
            func_node = self.db.get_node_by_id(func_id)
            if not func_node or func_node["type"] != "function":
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
    ):
        """递归收集 override（通过 overrides 边）"""
        # 查 overrides 边: to_id = base_func_id
        override_edges = self.db.get_edges_to(base_func_id, "overrides")

        for edge in override_edges:
            derived_func_id = edge["from_id"]
            derived_func = self.db.get_node_by_id(derived_func_id)
            if not derived_func:
                continue

            dedup_key = f"{derived_func['name']}@{derived_func.get('namespace', '')}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # 找到所属类
            derived_class = self._get_owning_class(derived_func_id)
            extra = _parse_extra(derived_func.get("extra_info", {}))
            base_extra = _parse_extra(
                self.db.get_node_by_id(base_func_id).get("extra_info", {})
                if self.db.get_node_by_id(base_func_id) else {}
            )

            results.append(OverrideInfo(
                function_name=derived_func["name"],
                class_name=derived_class or "",
                namespace=derived_func.get("namespace", ""),
                file_path=derived_func.get("file_path", ""),
                line_number=derived_func.get("start_line", 0),
                signature=extra.get("signature", derived_func["name"]),
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
        """查找函数节点 ID"""
        # 先找类节点
        class_nodes = self.db.find_node_by_name(class_name, "class")
        if not class_nodes:
            class_nodes = self.db.find_node_by_name(class_name, "struct")
        if not class_nodes:
            return None

        class_id = class_nodes[0]["id"]

        # 找属于此类的同名函数
        edges = self.db.get_edges_to(class_id, "belongs_to")
        for edge in edges:
            func_node = self.db.get_node_by_id(edge["from_id"])
            if func_node and func_node["name"] == func_name:
                return func_node["id"]

        return None

    def _find_overrides_of_func(
        self, func_id: int, func_name: str,
    ) -> list[OverrideInfo]:
        """查找函数的所有直接 override"""
        results: list[OverrideInfo] = []
        override_edges = self.db.get_edges_to(func_id, "overrides")
        for edge in override_edges:
            derived_func = self.db.get_node_by_id(edge["from_id"])
            if not derived_func:
                continue
            derived_class = self._get_owning_class(edge["from_id"])
            results.append(OverrideInfo(
                function_name=derived_func["name"],
                class_name=derived_class or "",
                namespace=derived_func.get("namespace", ""),
                file_path=derived_func.get("file_path", ""),
                line_number=derived_func.get("start_line", 0),
                signature="",
                base_class="",
                base_function_signature="",
            ))
        return results

    def _get_owning_class(self, func_id: int) -> str | None:
        """通过 belongs_to 边查找函数所属的类"""
        edges = self.db.get_edges_from(func_id, "belongs_to")
        if edges:
            class_node = self.db.get_node_by_id(edges[0]["to_id"])
            if class_node:
                return class_node["name"]
        return None

    def _get_ancestor_classes(self, class_name: str) -> list[str]:
        """获取所有祖先类名（递归 up）"""
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
                parent = self.db.get_node_by_id(edge["to_id"])
                if parent and parent["name"] not in visited:
                    visited.add(parent["name"])
                    ancestors.append(parent["name"])
                    queue.append(parent["name"])

        return ancestors

    def _get_all_descendants(self, class_name: str) -> list[dict]:
        """获取所有派生类（递归 down）"""
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
                child = self.db.get_node_by_id(edge["from_id"])
                if child and child["name"] not in visited:
                    visited.add(child["name"])
                    descendants.append({
                        "name": child["name"],
                        "namespace": child.get("namespace", ""),
                        "file_path": child.get("file_path", ""),
                    })
                    queue.append(child["name"])

        return descendants

    def _is_abstract_class(self, class_name: str) -> bool:
        """判断类是否为抽象类（含纯虚函数）"""
        nodes = self.db.find_node_by_name(class_name, "class")
        if not nodes:
            nodes = self.db.find_node_by_name(class_name, "struct")
        if not nodes:
            return False

        class_id = nodes[0]["id"]
        # 检查所属函数中是否有纯虚函数
        edges = self.db.get_edges_to(class_id, "belongs_to")
        for edge in edges:
            func_node = self.db.get_node_by_id(edge["from_id"])
            if not func_node:
                continue
            extra = _parse_extra(func_node.get("extra_info", {}))
            if extra.get("is_pure_virtual", False):
                return True

        return False
