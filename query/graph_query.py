"""
核心检索 API

封装 4 个高频查询接口，实现"替代全局 grep"的基本价值。
基于 GraphDB 底层操作，提供面向业务的语义查询。

用法:
    q = GraphQuery("semantic_graph.db")
    classes = q.search_class("SocUpdate")
    inh = q.get_inheritance("BasePeriUpdate", direction="down")
    funcs = q.search_function("PerformUpgrade")
    symbols = q.get_file_symbols("soc_update.cpp")
"""

import json
import logging
import time
from pathlib import Path

from ..db.graph_db import GraphDB, _hydrate_node
from ..db.relation_types import RelationType
from .query_models import ClassInfo, InheritanceInfo, FunctionInfo, SymbolInfo

logger = logging.getLogger(__name__)


def _extract_class_name(namespace: str) -> str | None:
    """从 namespace 提取所属类名

    hq_ota::SocUpdate → SocUpdate
    hq_ota → None（命名空间本身不是类）
    """
    if not namespace or "::" not in namespace:
        return None
    parts = namespace.rsplit("::", 1)
    # 最后一部分可能是类名（但不是顶层命名空间）
    return parts[-1] if len(parts) > 1 else None


def _row_to_class_info(row: dict) -> ClassInfo:
    """数据库行 → ClassInfo"""
    extra = {}
    if row.get("extra_info"):
        try:
            extra = json.loads(row["extra_info"]) if isinstance(row["extra_info"], str) else row["extra_info"]
        except (json.JSONDecodeError, TypeError):
            pass

    return ClassInfo(
        name=row.get("name", ""),
        namespace=row.get("namespace", ""),
        file_path=row.get("file_path", ""),
        start_line=row.get("start_line", 0) or 0,
        end_line=row.get("end_line", 0) or 0,
        is_abstract=extra.get("is_abstract", False),
        template_params=extra.get("template_params"),
        access=extra.get("access", "public"),
        unique_key=row.get("unique_key", ""),
    )


def _row_to_function_info(row: dict) -> FunctionInfo:
    """数据库行 → FunctionInfo"""
    extra = {}
    if row.get("extra_info"):
        try:
            extra = json.loads(row["extra_info"]) if isinstance(row["extra_info"], str) else row["extra_info"]
        except (json.JSONDecodeError, TypeError):
            pass

    namespace = row.get("namespace", "")
    class_name = _extract_class_name(namespace)

    return FunctionInfo(
        name=row.get("name", ""),
        signature=extra.get("signature", row.get("name", "")),
        namespace=namespace,
        class_name=class_name,
        file_path=row.get("file_path", ""),
        start_line=row.get("start_line", 0) or 0,
        end_line=row.get("end_line", 0) or 0,
        is_virtual=extra.get("is_virtual", False),
        is_override=extra.get("is_override", False),
        is_pure_virtual=extra.get("is_pure_virtual", False),
        is_static=extra.get("is_static", False),
        is_const=extra.get("is_const", False),
        access=extra.get("access", "public"),
        unique_key=row.get("unique_key", ""),
    )


def _row_to_symbol_info(row: dict) -> SymbolInfo:
    """数据库行 → SymbolInfo"""
    extra = {}
    if row.get("extra_info"):
        try:
            extra = json.loads(row["extra_info"]) if isinstance(row["extra_info"], str) else row["extra_info"]
        except (json.JSONDecodeError, TypeError):
            pass

    return SymbolInfo(
        node_type=row.get("type", ""),
        name=row.get("name", ""),
        namespace=row.get("namespace", ""),
        file_path=row.get("file_path", ""),
        start_line=row.get("start_line", 0) or 0,
        end_line=row.get("end_line", 0) or 0,
        extra=extra,
        unique_key=row.get("unique_key", ""),
    )


def _edge_extra(edge: dict) -> dict:
    """数据库边行 → extra_info dict

    edge.extra_info 在 SQLite 中存为 JSON 字符串，需解析后才能取字段。
    直接 isinstance 判断会误判为非 dict（字符串），导致 is_virtual 等字段丢失。
    """
    raw = edge.get("extra_info")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


class GraphQuery:
    """核心检索 API"""

    def __init__(self, db_path: str):
        self.db = GraphDB(db_path)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # 1. search_class — 按类名搜索
    # ------------------------------------------------------------------

    def search_class(self, name: str, exact: bool = False) -> list[ClassInfo]:
        """按类名搜索，返回类信息与文件位置

        Args:
            name: 类名
            exact: True=精确匹配, False=模糊匹配（LIKE %name%）

        Returns:
            ClassInfo 列表
        """
        rows = self.db.find_node_by_name(name, node_type="class", exact=exact)

        # struct 也算类
        if not rows:
            rows = self.db.find_node_by_name(name, node_type="struct", exact=exact)

        # 如果 class 和 struct 都没精确匹配，模糊搜索时合并两者
        if not exact and not rows:
            class_rows = self.db.find_node_by_name(name, node_type="class", exact=False)
            struct_rows = self.db.find_node_by_name(name, node_type="struct", exact=False)
            seen = set()
            for r in class_rows + struct_rows:
                if r["unique_key"] not in seen:
                    rows.append(r)
                    seen.add(r["unique_key"])

        return [_row_to_class_info(r) for r in rows]

    # ------------------------------------------------------------------
    # 2. get_inheritance — 查询继承关系
    # ------------------------------------------------------------------

    def _find_class_node_ids(self, class_name: str) -> list[tuple[int, dict]]:
        """按类名查找所有匹配的节点 (id, row)"""
        rows = self.db.find_node_by_name(class_name, node_type="class", exact=True)
        if not rows:
            rows = self.db.find_node_by_name(class_name, node_type="struct", exact=True)
        if not rows:
            # 模糊搜索 fallback
            rows = self.db.find_node_by_name(class_name, node_type="class", exact=False)
        return [(r["id"], r) for r in rows]

    def get_inheritance(self, class_name: str, direction: str = "down",
                        depth: int = 1) -> list[InheritanceInfo]:
        """查询类的继承关系

        Args:
            class_name: 类名
            direction: "up"=查父类, "down"=查子类
            depth: 递归深度（1=直接, -1=全部）

        Returns:
            InheritanceInfo 列表
        """
        node_pairs = self._find_class_node_ids(class_name)
        if not node_pairs:
            return []

        # 继承关系类型
        inherit_types = [rt.value for rt in RelationType.inherits_types()]

        results: list[InheritanceInfo] = []
        visited_edges: set[int] = set()

        # BFS 遍历
        # 当前层：要查询的节点 id 列表
        current_ids = [nid for nid, _ in node_pairs]
        # 起始节点 id → 起始行（用于构造 InheritanceInfo 的 child/parent）
        id_to_row: dict[int, dict] = {nid: row for nid, row in node_pairs}

        remaining = depth if depth > 0 else 999  # -1 → 足够深

        while current_ids and remaining > 0:
            next_ids = []

            for nid in current_ids:
                if direction == "down":
                    # 查子类：当前节点是 parent (to_id)，找 from_id
                    edges = self.db.get_edges_to(nid)
                    for e in edges:
                        if e["relation_type"] not in inherit_types:
                            continue
                        if e["id"] in visited_edges:
                            continue
                        visited_edges.add(e["id"])

                        child_row = self.db.get_node_by_id(e["from_id"])
                        parent_row = self.db.get_node_by_id(e["to_id"])
                        if child_row and parent_row:
                            results.append(InheritanceInfo(
                                parent=_row_to_class_info(parent_row),
                                child=_row_to_class_info(child_row),
                                access=e["relation_type"].replace("inherits_", ""),
                                is_virtual=_edge_extra(e).get("is_virtual", False),
                            ))
                            next_ids.append(e["from_id"])

                else:  # direction == "up"
                    # 查父类：当前节点是 child (from_id)，找 to_id
                    edges = self.db.get_edges_from(nid)
                    for e in edges:
                        if e["relation_type"] not in inherit_types:
                            continue
                        if e["id"] in visited_edges:
                            continue
                        visited_edges.add(e["id"])

                        parent_row = self.db.get_node_by_id(e["to_id"])
                        child_row = self.db.get_node_by_id(e["from_id"])
                        if parent_row and child_row:
                            results.append(InheritanceInfo(
                                parent=_row_to_class_info(parent_row),
                                child=_row_to_class_info(child_row),
                                access=e["relation_type"].replace("inherits_", ""),
                                is_virtual=_edge_extra(e).get("is_virtual", False),
                            ))
                            next_ids.append(e["to_id"])

            current_ids = next_ids
            remaining -= 1

        return results

    # ------------------------------------------------------------------
    # 3. search_function — 按函数名搜索
    # ------------------------------------------------------------------

    def search_function(self, name: str, class_name: str = None) -> list[FunctionInfo]:
        """按函数名搜索，返回签名、所属类、文件位置

        Args:
            name: 函数名
            class_name: 可选，限定所属类

        Returns:
            FunctionInfo 列表
        """
        rows = self.db.find_node_by_name(name, node_type="function", exact=True)
        if not rows:
            # 模糊搜索 fallback
            rows = self.db.find_node_by_name(name, node_type="function", exact=False)

        results = [_row_to_function_info(r) for r in rows]

        # 按所属类过滤
        if class_name:
            results = [f for f in results
                       if f.class_name == class_name
                       or class_name in (f.namespace or "")]

        return results

    # ------------------------------------------------------------------
    # 4. get_file_symbols — 按文件路径查询符号
    # ------------------------------------------------------------------

    def get_file_symbols(self, file_path: str) -> list[SymbolInfo]:
        """按文件路径查询文件内所有类与函数

        Args:
            file_path: 文件路径（支持部分匹配）

        Returns:
            SymbolInfo 列表，按 start_line 排序
        """
        rows = self.db.conn.execute(
            "SELECT * FROM node WHERE file_path LIKE ? ORDER BY start_line",
            (f"%{file_path}%",)
        ).fetchall()

        return [_row_to_symbol_info(_hydrate_node(r)) for r in rows]

    # ------------------------------------------------------------------
    # 辅助：按 unique_key 精确查节点
    # ------------------------------------------------------------------

    def get_class_by_key(self, unique_key: str) -> ClassInfo | None:
        """按 unique_key 精确查询类"""
        row = self.db.get_node_by_key(unique_key)
        if not row:
            return None
        return _row_to_class_info(row)

    def get_function_by_key(self, unique_key: str) -> FunctionInfo | None:
        """按 unique_key 精确查询函数"""
        row = self.db.get_node_by_key(unique_key)
        if not row:
            return None
        return _row_to_function_info(row)
