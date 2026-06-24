"""别名解析查询

查询类型别名和使用声明，支持别名链的递归解析。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..db.graph_db import GraphDB
from ..db.relation_types import RelationType

logger = logging.getLogger(__name__)


@dataclass
class AliasInfo:
    """别名信息"""
    alias_name: str
    target_name: str
    alias_namespace: str
    target_namespace: str
    alias_file: str
    is_typedef: bool


def _parse_extra(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


class AliasQuery:
    """别名解析查询"""

    def __init__(self, db_path: str):
        self.db = GraphDB(db_path)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def get_aliases_of(self, name: str) -> list[AliasInfo]:
        """查询指定类型的所有别名

        沿 type_alias 边反向查找: 谁是 name 的别名?

        Args:
            name: 目标类型名

        Returns:
            别名信息列表
        """
        # 找 name 对应的节点
        target_nodes = self.db.find_node_by_name(name, "class")
        if not target_nodes:
            target_nodes = self.db.find_node_by_name(name, "struct")
        if not target_nodes:
            return []

        results: list[AliasInfo] = []
        seen: set[str] = set()

        for target in target_nodes:
            target_id = target["id"]

            # 找指向此节点的 type_alias 边
            alias_edges = self.db.get_edges_to(target_id, "type_alias")
            for edge in alias_edges:
                alias_node = self.db.get_node_by_id(edge["from_id"])
                if not alias_node:
                    continue

                dedup = alias_node["unique_key"]
                if dedup in seen:
                    continue
                seen.add(dedup)

                extra = _parse_extra(edge.get("extra_info", {}))
                results.append(AliasInfo(
                    alias_name=alias_node["name"],
                    target_name=name,
                    alias_namespace=alias_node.get("namespace", ""),
                    target_namespace=target.get("namespace", ""),
                    alias_file=alias_node.get("file_path", ""),
                    is_typedef=extra.get("is_typedef", False),
                ))

        return results

    def resolve_alias(self, name: str, max_depth: int = 5) -> str:
        """递归解析别名到最终原始类型

        如: OtaServiceProxy → ThreadDrivenProxy<OtaServiceInterface>

        Args:
            name: 别名或类型名
            max_depth: 最大递归深度

        Returns:
            最终原始类型名（如果无法解析则返回原名称）
        """
        current = name
        visited: set[str] = set()

        for _ in range(max_depth):
            if current in visited:
                break  # 环路
            visited.add(current)

            # 查找 current 是否是别名
            nodes = self.db.find_node_by_name(current, "class")
            if not nodes:
                break

            is_alias = False
            for node in nodes:
                # 查从此节点出发的 type_alias 边
                alias_edges = self.db.get_edges_from(node["id"], "type_alias")
                if alias_edges:
                    target = self.db.get_node_by_id(alias_edges[0]["to_id"])
                    if target:
                        current = target["name"]
                        is_alias = True
                        break

            if not is_alias:
                break

        return current

    def get_using_declarations(self, class_name: str) -> list[dict]:
        """查询类中的 using 声明

        Args:
            class_name: 类名

        Returns:
            using 声明列表 [{function_name, base_class, derived_class}]
        """
        # 找类节点
        class_nodes = self.db.find_node_by_name(class_name, "class")
        if not class_nodes:
            class_nodes = self.db.find_node_by_name(class_name, "struct")
        if not class_nodes:
            return []

        results: list[dict] = []
        for class_node in class_nodes:
            class_id = class_node["id"]

            # 找指向此类函数的 using_decl 边
            # using_decl: from=子类::func, to=基类::func
            # 需要找 from 的所属类是 class_name 的边
            func_edges = self.db.get_edges_to(class_id, "belongs_to")
            for fe in func_edges:
                func_id = fe["from_id"]
                using_edges = self.db.get_edges_from(func_id, "using_decl")
                for ue in using_edges:
                    extra = _parse_extra(ue.get("extra_info", {}))
                    results.append({
                        "function_name": extra.get("function_name", ""),
                        "base_class": extra.get("base_class", ""),
                        "derived_class": extra.get("derived_class", ""),
                    })

        return results
