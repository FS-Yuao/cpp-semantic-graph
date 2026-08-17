"""函数调用关系查询

支持 callers / callees / call_chain 查询，以及虚分派展开：
- calls_direct: 直接函数调用
- calls_virtual: 虚函数调用（展开为所有可能的目标函数）
- calls_callback: 回调/函数对象调用
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..db.graph_db import GraphDB
from ..db.relation_types import RelationType

logger = logging.getLogger(__name__)


@dataclass
class CallInfo:
    """调用关系信息"""
    caller_name: str           # 调用方函数名
    caller_class: str | None   # 调用方所属类
    caller_namespace: str
    caller_file: str
    caller_line: int
    callee_name: str           # 被调用方函数名
    callee_class: str | None   # 被调用方所属类
    callee_namespace: str
    callee_file: str
    call_type: str             # calls_direct / calls_virtual / calls_callback
    is_virtual_dispatch: bool  # 是否为虚分派


@dataclass
class CallChainNode:
    """调用链节点"""
    function_name: str
    class_name: str | None
    namespace: str
    file_path: str
    depth: int
    call_type: str | None = None  # 到达此节点的调用类型


class CallQuery:
    """调用关系查询"""

    def __init__(self, db_path: str, expand_virtual: bool = True):
        """初始化

        Args:
            db_path: 数据库路径
            expand_virtual: 是否展开虚分派（将 calls_virtual 边
                展开为所有子类的 override 实现）
        """
        self.db = GraphDB(db_path)
        self.expand_virtual = expand_virtual

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # 谁调用了这个函数
    # ------------------------------------------------------------------

    def get_callers(
        self,
        func_name: str,
        class_name: str | None = None,
        call_type: str | None = None,
        namespace: str | None = None,
    ) -> list[CallInfo]:
        """查询谁调用了指定函数

        Args:
            func_name: 被调用方函数名
            class_name: 限定被调用方所属类
            call_type: 限定调用类型 (direct / virtual / callback)
            namespace: 限定被调用方 namespace（精确匹配，消歧同名重载）
                成员函数形如 "update::FileHandler"，自由函数形如 "update"

        Returns:
            调用方信息列表
        """
        # 找到目标函数节点
        target_ids = self._find_function_ids(
            func_name, class_name, namespace=namespace)
        if not target_ids:
            return []

        results: list[CallInfo] = []
        seen: set[str] = set()
        # N+1 消除：owning_class 查询缓存
        _class_cache: dict[int, str | None] = {}

        call_types = self._resolve_call_types(call_type)

        for target_id in target_ids:
            # 每个目标节点只查一次（原为每条边查一次）
            callee_node = self.db.get_node_by_id(target_id)
            if not callee_node:
                continue
            callee_class = self._get_owning_class_cached(target_id, _class_cache)

            for ct in call_types:
                edges = self.db.get_edges_to(target_id, ct)
                for edge in edges:
                    # N+1 消除：用 JOINed 边数据（from_name/from_namespace/from_file_path）
                    caller_name = edge.get("from_name", "")
                    caller_namespace = edge.get("from_namespace", "")
                    caller_file = edge.get("from_file_path", "")
                    if not caller_name:
                        continue

                    call_line = edge.get("call_line", 0)

                    dedup = f"{caller_name}@{caller_file}@{call_line}"
                    if dedup in seen:
                        continue
                    seen.add(dedup)

                    caller_class = self._get_owning_class_cached(
                        edge["from_id"], _class_cache)

                    results.append(CallInfo(
                        caller_name=caller_name,
                        caller_class=caller_class,
                        caller_namespace=caller_namespace,
                        caller_file=caller_file,
                        caller_line=call_line,
                        callee_name=func_name,
                        callee_class=callee_class,
                        callee_namespace=callee_node.get("namespace", ""),
                        callee_file=callee_node.get("file_path", ""),
                        call_type=edge["relation_type"],
                        is_virtual_dispatch=edge["relation_type"] == "calls_virtual",
                    ))

        # 虚分派展开: 如果 expand_virtual，把虚调用也视为对子类 override 的调用
        if self.expand_virtual:
            self._expand_virtual_callers(func_name, class_name, results, seen)

        return results

    # ------------------------------------------------------------------
    # 这个函数调用了谁
    # ------------------------------------------------------------------

    def get_callees(
        self,
        func_name: str,
        class_name: str | None = None,
        call_type: str | None = None,
        namespace: str | None = None,
    ) -> list[CallInfo]:
        """查询指定函数调用了谁

        Args:
            func_name: 调用方函数名
            class_name: 限定调用方所属类
            call_type: 限定调用类型
            namespace: 限定调用方 namespace（精确匹配，消歧同名重载）

        Returns:
            被调用方信息列表
        """
        # 找到调用方函数节点
        source_ids = self._find_function_ids(
            func_name, class_name, namespace=namespace)
        if not source_ids:
            return []

        results: list[CallInfo] = []
        seen: set[str] = set()
        # N+1 消除：owning_class 查询缓存
        _class_cache: dict[int, str | None] = {}

        call_types = self._resolve_call_types(call_type)

        for source_id in source_ids:
            # 每个源节点只查一次（原为每条边查一次）
            caller_node = self.db.get_node_by_id(source_id)
            if not caller_node:
                continue
            caller_class = self._get_owning_class_cached(source_id, _class_cache)

            for ct in call_types:
                edges = self.db.get_edges_from(source_id, ct)
                for edge in edges:
                    # N+1 消除：用 JOINed 边数据（to_name/to_namespace/to_file_path）
                    callee_name = edge.get("to_name", "")
                    callee_namespace = edge.get("to_namespace", "")
                    callee_file = edge.get("to_file_path", "")
                    if not callee_name:
                        continue

                    call_line = edge.get("call_line", 0)

                    dedup = f"{callee_name}@{callee_file}@{call_line}"
                    if dedup in seen:
                        continue
                    seen.add(dedup)

                    callee_class = self._get_owning_class_cached(
                        edge["to_id"], _class_cache)

                    results.append(CallInfo(
                        caller_name=func_name,
                        caller_class=caller_class,
                        caller_namespace=caller_node.get("namespace", ""),
                        caller_file=caller_node.get("file_path", ""),
                        caller_line=call_line,
                        callee_name=callee_name,
                        callee_class=callee_class,
                        callee_namespace=callee_namespace,
                        callee_file=callee_file,
                        call_type=edge["relation_type"],
                        is_virtual_dispatch=edge["relation_type"] == "calls_virtual",
                    ))

        # 虚分派展开
        if self.expand_virtual:
            self._expand_virtual_callees(func_name, class_name, results, seen)

        return results

    # ------------------------------------------------------------------
    # 调用链递归查询
    # ------------------------------------------------------------------

    def get_call_chain(
        self,
        func_name: str,
        class_name: str | None = None,
        direction: str = "down",
        depth: int = 3,
    ) -> list[CallChainNode]:
        """递归查询调用链

        Args:
            func_name: 起始函数名
            class_name: 限定所属类
            direction: "down" 查调用了谁, "up" 查被谁调用
            depth: 最大递归深度

        Returns:
            调用链节点列表
        """
        result: list[CallChainNode] = []
        visited: set[str] = set()

        self._walk_call_chain(
            func_name, class_name, direction, 0, depth,
            visited, result,
        )

        return result

    def _walk_call_chain(
        self,
        func_name: str,
        class_name: str | None,
        direction: str,
        current_depth: int,
        max_depth: int,
        visited: set[str],
        result: list[CallChainNode],
    ):
        """BFS 递归遍历调用链"""
        if current_depth > max_depth:
            return

        dedup_key = f"{func_name}@{class_name}"
        if dedup_key in visited:
            return
        visited.add(dedup_key)

        if direction == "down":
            calls = self.get_callees(func_name, class_name)
            for call in calls:
                result.append(CallChainNode(
                    function_name=call.callee_name,
                    class_name=call.callee_class,
                    namespace=call.callee_namespace,
                    file_path=call.callee_file,
                    depth=current_depth + 1,
                    call_type=call.call_type,
                ))
                # 递归
                self._walk_call_chain(
                    call.callee_name, call.callee_class,
                    direction, current_depth + 1, max_depth,
                    visited, result,
                )
        else:  # up
            callers = self.get_callers(func_name, class_name)
            for call in callers:
                result.append(CallChainNode(
                    function_name=call.caller_name,
                    class_name=call.caller_class,
                    namespace=call.caller_namespace,
                    file_path=call.caller_file,
                    depth=current_depth + 1,
                    call_type=call.call_type,
                ))
                # 递归
                self._walk_call_chain(
                    call.caller_name, call.caller_class,
                    direction, current_depth + 1, max_depth,
                    visited, result,
                )

    # ------------------------------------------------------------------
    # 虚分派展开
    # ------------------------------------------------------------------

    def expand_virtual_dispatch(
        self,
        func_name: str,
        class_name: str,
    ) -> list[CallInfo]:
        """展开虚函数调用的所有可能目标

        例如: DeviceAdapter::PerformUpdate 的虚调用展开为:
        - FirmwareUpdate::PerformUpdate
        - SensorUpdate::PerformUpdate
        - SwitchUpdate::PerformUpdate
        - McuUpdate::PerformUpdate

        Args:
            func_name: 虚函数名
            class_name: 声明该虚函数的基类名

        Returns:
            所有可能的调用目标
        """
        from .polymorphism_query import PolymorphismQuery

        results: list[CallInfo] = []
        with PolymorphismQuery(str(self.db.db_path)) as pq:
            overrides = pq.get_all_overrides(func_name, class_name)

        for o in overrides:
            results.append(CallInfo(
                caller_name="",
                caller_class=None,
                caller_namespace="",
                caller_file="",
                caller_line=0,
                callee_name=o.function_name,
                callee_class=o.class_name,
                callee_namespace=o.namespace,
                callee_file=o.file_path,
                call_type="calls_virtual_expanded",
                is_virtual_dispatch=True,
            ))

        return results

    def _expand_virtual_callers(
        self,
        func_name: str,
        class_name: str | None,
        results: list[CallInfo],
        seen: set[str],
    ):
        """虚分派展开: callers 侧"""
        # 找基类虚函数的所有 override，检查它们的 callers
        from .polymorphism_query import PolymorphismQuery

        try:
            with PolymorphismQuery(str(self.db.db_path)) as pq:
                overrides = pq.get_all_overrides(func_name, class_name or "")
        except Exception:
            return

        # N+1 消除：owning_class 缓存
        _class_cache: dict[int, str | None] = {}

        for o in overrides:
            if o.class_name == class_name:
                continue  # 跳过自身
            override_ids = self._find_function_ids(o.function_name, o.class_name)
            for oid in override_ids:
                for ct in [RelationType.CALLS_DIRECT.value, RelationType.CALLS_VIRTUAL.value]:
                    edges = self.db.get_edges_to(oid, ct)
                    for edge in edges:
                        # N+1 消除：用 JOINed 边数据
                        caller_name = edge.get("from_name", "")
                        caller_namespace = edge.get("from_namespace", "")
                        caller_file = edge.get("from_file_path", "")
                        if not caller_name:
                            continue
                        call_line = edge.get("call_line", 0)
                        dedup = f"{caller_name}@{caller_file}@{call_line}"
                        if dedup in seen:
                            continue
                        seen.add(dedup)

                        results.append(CallInfo(
                            caller_name=caller_name,
                            caller_class=self._get_owning_class_cached(
                                edge["from_id"], _class_cache),
                            caller_namespace=caller_namespace,
                            caller_file=caller_file,
                            caller_line=call_line,
                            callee_name=o.function_name,
                            callee_class=o.class_name,
                            callee_namespace=o.namespace,
                            callee_file=o.file_path,
                            call_type="calls_virtual_expanded",
                            is_virtual_dispatch=True,
                        ))

    def _expand_virtual_callees(
        self,
        func_name: str,
        class_name: str | None,
        results: list[CallInfo],
        seen: set[str],
    ):
        """虚分派展开: callees 侧"""
        # 对结果中的虚调用，展开为所有 override
        virtual_calls = [r for r in results if r.is_virtual_dispatch]
        for vc in virtual_calls:
            expanded = self.expand_virtual_dispatch(
                vc.callee_name, vc.callee_class or ""
            )
            for exp in expanded:
                dedup = f"{exp.callee_name}@{exp.callee_class}@{exp.callee_file}"
                if dedup not in seen:
                    seen.add(dedup)
                    results.append(exp)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _find_function_ids(
        self, func_name: str, class_name: str | None = None,
        namespace: str | None = None,
    ) -> list[int]:
        """查找函数节点 ID 列表

        优先返回 .cpp 定义节点（有调用边），.h 声明节点排后。
        通过 namespace LIKE 匹配类名，同时覆盖声明和定义节点。
        namespace 参数为精确匹配（消歧同名重载），优先于 class_name 生效。
        """
        all_funcs = self.db.find_node_by_name(func_name, "function")
        matched = all_funcs
        if namespace:
            # 精确匹配 namespace（如 "update::FileHandler"），排除同名重载
            matched = [n for n in matched
                       if (n.get("namespace", "") or "") == namespace]
        if class_name:
            # 按 namespace 匹配类名 + 函数名查找
            # namespace 是 '::' 分隔路径（末段=所属类名），按段精确匹配避免子串误匹配（主题A-5）
            # 'Update' 不再误匹配 'FirmwareUpdate'
            matched = [
                n for n in matched
                if class_name in ((n.get("namespace", "") or "").split("::"))
            ]

        # 定义优先（.cpp/.cc 文件中的节点通常有调用边）
        definition_ids = [n["id"] for n in matched
                          if n.get("file_path", "").endswith((".cpp", ".cc"))]
        declaration_ids = [n["id"] for n in matched
                           if not n.get("file_path", "").endswith((".cpp", ".cc"))]
        return definition_ids + declaration_ids

    def _get_owning_class(self, func_id: int) -> str | None:
        """通过 belongs_to 边查找函数所属的类

        N+1 消除：get_edges_from 已 JOIN to 节点，直接取 to_name，
        无需二次 get_node_by_id 查询。
        """
        edges = self.db.get_edges_from(func_id, "belongs_to")
        if edges:
            return edges[0].get("to_name")
        return None

    def _get_owning_class_cached(
        self, func_id: int, cache: dict[int, str | None],
    ) -> str | None:
        """带缓存的 _get_owning_class（同一 func_id 只查一次）"""
        if func_id not in cache:
            cache[func_id] = self._get_owning_class(func_id)
        return cache[func_id]

    @staticmethod
    def _resolve_call_types(call_type: str | None) -> list[str]:
        """将调用类型过滤条件转换为 relation_type 值列表"""
        if call_type is None:
            return [rt.value for rt in RelationType.call_types()]
        mapping = {
            "direct": RelationType.CALLS_DIRECT.value,
            "virtual": RelationType.CALLS_VIRTUAL.value,
            "callback": RelationType.CALLS_CALLBACK.value,
        }
        ct = mapping.get(call_type)
        return [ct] if ct else [rt.value for rt in RelationType.call_types()]
