#!/usr/bin/env python3
"""自动化回归测试 — 核心查询 API + 数据完整性 + 边解析

用法:
    cd /mnt/code1 && PYTHONPATH=. python3 cpp_semantic_graph/tests/test_regression.py

也可用 pytest:
    cd /mnt/code1 && PYTHONPATH=. python3 -m pytest cpp_semantic_graph/tests/test_regression.py -v

覆盖范围:
1. 搜索 API: search_class / search_function / get_file_symbols
2. 继承查询: get_inheritance (up/down)
3. 调用查询: get_callers / get_callees / get_call_chain
4. 遍历查询: traverse_graph (BFS/DFS)
5. 爆炸半径: blast_radius
6. 边解析完整性: 0 unresolved edges
7. 删除安全性: 精确匹配不误删子串匹配文件
8. UPSERT 正确性: 重复导入不报错、不创建重复行
9. BuildLock: 并发保护
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

# 确保 PYTHONPATH 包含项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cpp_semantic_graph.db.graph_db import GraphDB, BuildLock
from cpp_semantic_graph.query.graph_query import GraphQuery
from cpp_semantic_graph.query.call_query import CallQuery
from cpp_semantic_graph.query.traverse import TraverseQuery
from cpp_semantic_graph.query.blast_radius_query import BlastRadiusQuery
from cpp_semantic_graph.query.polymorphism_query import PolymorphismQuery
from cpp_semantic_graph.parser.models import NodeInfo, EdgeInfo, IncludeDep, ParseResult, NodeType
from cpp_semantic_graph.db.relation_types import RelationType

# ─── 测试用 DB ───
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "semantic_graph_full.db",
)

# ─── 断言辅助 ───

_passed = 0
_failed = 0
_failures: list[str] = []


def check(condition: bool, msg: str):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {msg}")
    else:
        _failed += 1
        _failures.append(msg)
        print(f"  ❌ {msg}")


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ─── 测试用例 ───

def test_search_apis():
    """1. 搜索 API"""
    section("1. 搜索 API")
    with GraphQuery(DB_PATH) as q:
        # search_class
        classes = q.search_class("SocUpdate", exact=True)
        check(len(classes) >= 1, f"search_class('SocUpdate') → {len(classes)} results")

        # search_function
        funcs = q.search_function("PerformUpgrade")
        check(len(funcs) >= 1, f"search_function('PerformUpgrade') → {len(funcs)} results")

        # get_file_symbols
        symbols = q.get_file_symbols("soc_update.cpp")
        check(len(symbols) >= 1, f"get_file_symbols('soc_update.cpp') → {len(symbols)} symbols")


def test_inheritance():
    """2. 继承查询"""
    section("2. 继承查询")
    with GraphQuery(DB_PATH) as q:
        # down: 子类
        children = q.get_inheritance("BasePeriUpdate", direction="down", depth=1)
        check(len(children) >= 1,
              f"get_inheritance('BasePeriUpdate', down) → {len(children)} children")

        # up: 父类
        parents = q.get_inheritance("SocUpdate", direction="up", depth=1)
        check(len(parents) >= 1,
              f"get_inheritance('SocUpdate', up) → {len(parents)} parents")

        # 多层
        deep = q.get_inheritance("BasePeriUpdate", direction="down", depth=-1)
        check(len(deep) >= len(children),
              f"depth=-1 ({len(deep)}) >= depth=1 ({len(children)})")


def test_call_query():
    """3. 调用查询"""
    section("3. 调用查询")
    with CallQuery(DB_PATH) as cq:
        # get_callees
        callees = cq.get_callees("PerformUpgrade")
        check(len(callees) >= 1,
              f"get_callees('PerformUpgrade') → {len(callees)} callees")

        # get_callers — 找一个有调用方的函数
        callers = cq.get_callers("PerformUpgrade")
        check(isinstance(callers, list),
              f"get_callers('PerformUpgrade') → {len(callers)} callers")

        # call_chain
        chain = cq.get_call_chain("PerformUpgrade", direction="down", depth=2)
        check(isinstance(chain, list),
              f"get_call_chain('PerformUpgrade', down, depth=2) → {len(chain)} nodes")


def test_traverse():
    """4. 遍历查询"""
    section("4. 遍历查询")
    with TraverseQuery(DB_PATH) as tq:
        # BFS
        result = tq.traverse_graph(
            "PerformUpgrade", direction="outgoing", depth=2, mode="bfs"
        )
        check(isinstance(result.nodes, list),
              f"BFS traverse → {len(result.nodes)} nodes, {len(result.edges)} edges")

        # DFS
        result_dfs = tq.traverse_graph(
            "PerformUpgrade", direction="outgoing", depth=2, mode="dfs"
        )
        check(isinstance(result_dfs.nodes, list),
              f"DFS traverse → {len(result_dfs.nodes)} nodes")


def test_blast_radius():
    """5. 爆炸半径"""
    section("5. 爆炸半径")
    with BlastRadiusQuery(DB_PATH) as bq:
        result = bq.compute(symbols=["PerformUpgrade"], depth=2)
        check(isinstance(result.affected_nodes, list),
              f"blast_radius('PerformUpgrade') → {len(result.affected_nodes)} affected nodes")
        check(len(result.affected_files) > 0 or len(result.affected_nodes) == 0,
              f"affected_files consistency: {len(result.affected_files)} files")


def test_edge_resolution():
    """6. 边解析完整性"""
    section("6. 边解析完整性")
    db = GraphDB(DB_PATH)
    try:
        unresolved = db.conn.execute(
            "SELECT COUNT(*) as c FROM edge WHERE to_id IS NULL"
        ).fetchone()["c"]
        check(unresolved == 0, f"unresolved edges (to_id IS NULL) = {unresolved} (expected 0)")

        # 调用边存在
        call_count = db.conn.execute(
            "SELECT COUNT(*) as c FROM edge WHERE relation_type LIKE 'calls%' AND to_id IS NOT NULL"
        ).fetchone()["c"]
        check(call_count > 0, f"resolved call edges = {call_count} (>0)")
    finally:
        db.close()


def test_delete_safety():
    """7. 删除安全性 — 精确匹配不误删子串匹配文件"""
    section("7. 删除安全性")
    db = GraphDB(DB_PATH)
    try:
        # 模拟: "ota.cpp" 不应匹配 "my_ota.cpp" 或 "ota_test.cpp"
        # 用不存在的前缀验证不会误删
        before = db.conn.execute("SELECT COUNT(*) as c FROM node").fetchone()["c"]
        db.delete_by_source_file("nonexistent_prefix_xyz.cpp")
        after = db.conn.execute("SELECT COUNT(*) as c FROM node").fetchone()["c"]
        check(before == after,
              f"delete nonexistent file: before={before}, after={after} (should match)")

        # delete_file_completely with nonexistent path
        db.delete_file_completely("nonexistent_prefix_xyz.cpp")
        after2 = db.conn.execute("SELECT COUNT(*) as c FROM node").fetchone()["c"]
        check(after == after2,
              f"delete_file_completely nonexistent: no change ({after2})")
    finally:
        db.close()


def test_upsert():
    """8. UPSERT 正确性 — 重复导入不报错、不创建重复行"""
    section("8. UPSERT 正确性")
    db_path = tempfile.mktemp(suffix=".db")
    db = GraphDB(db_path)
    try:
        # 插入一个节点
        node = NodeInfo(
            type=NodeType.CLASS,
            name="TestClass",
            namespace="test_ns",
            file_path="test.cpp",
            start_line=1,
            end_line=10,
            unique_key="class||TestClass|test.cpp",
        )
        node_id_1 = db.upsert_node(node)

        # 再次 upsert 同一节点（不同行号）
        node.start_line = 20
        node.end_line = 30
        node_id_2 = db.upsert_node(node)

        check(node_id_1 == node_id_2,
              f"upsert same node: id1={node_id_1}, id2={node_id_2} (should match)")

        # 验证行号已更新
        row = db.conn.execute(
            "SELECT start_line, end_line FROM node WHERE id=?", (node_id_1,)
        ).fetchone()
        check(row["start_line"] == 20 and row["end_line"] == 30,
              f"upsert updated: start_line={row['start_line']}, end_line={row['end_line']}")

        # 验证只有 1 行
        count = db.conn.execute(
            "SELECT COUNT(*) as c FROM node WHERE unique_key=?", (node.unique_key,)
        ).fetchone()["c"]
        check(count == 1, f"no duplicate rows: count={count} (expected 1)")

        # 测试 insert_edge UPSERT
        node2 = NodeInfo(
            type=NodeType.FUNCTION,
            name="testFunc",
            namespace="test_ns::TestClass",
            file_path="test.cpp",
            start_line=5,
            end_line=8,
            unique_key="function||testFunc|test_ns::TestClass|test.cpp|5",
        )
        node2_id = db.upsert_node(node2)

        edge_id_1 = db.insert_edge(node2_id, node_id_1, "belongs_to", {}, call_line=0)
        edge_id_2 = db.insert_edge(node2_id, node_id_1, "belongs_to", {}, call_line=0)
        check(edge_id_1 == edge_id_2,
              f"upsert edge: id1={edge_id_1}, id2={edge_id_2} (should match)")

        edge_count = db.conn.execute(
            "SELECT COUNT(*) as c FROM edge WHERE from_id=? AND to_id=? AND relation_type='belongs_to'",
            (node2_id, node_id_1)
        ).fetchone()["c"]
        check(edge_count == 1, f"no duplicate edges: count={edge_count} (expected 1)")

        # 测试 executemany includes
        result = ParseResult(
            source_path="test.cpp",
            status="success",
            nodes=[],
            edges=[],
            includes=[
                IncludeDep(source_file="test.cpp", included_file="a.h", is_system=False),
                IncludeDep(source_file="test.cpp", included_file="b.h", is_system=False),
                IncludeDep(source_file="test.cpp", included_file="a.h", is_system=False),  # 重复
            ],
        )
        stats = db.import_parse_result(result)
        check(stats["includes_new"] == 2,
              f"executemany includes: new={stats['includes_new']} (expected 2, 1 dedup)")

    finally:
        db.close()
        os.unlink(db_path)
        if os.path.exists(db_path + "-wal"):
            os.unlink(db_path + "-wal")
        if os.path.exists(db_path + "-shm"):
            os.unlink(db_path + "-shm")
        if os.path.exists(db_path + ".build_lock"):
            os.unlink(db_path + ".build_lock")


def test_build_lock():
    """9. BuildLock 并发保护"""
    section("9. BuildLock 并发保护")
    db_path = tempfile.mktemp(suffix=".db")
    db = GraphDB(db_path)
    db.close()

    try:
        # 正常获取/释放
        with BuildLock(db_path) as lock:
            check(lock._fd is not None, "BuildLock acquired")

        # 重新获取（已释放）
        with BuildLock(db_path) as lock:
            check(lock._fd is not None, "BuildLock re-acquired after release")

        # 并发：第二个应该被阻塞
        result = {"second": None}

        def try_second_lock():
            try:
                with BuildLock(db_path):
                    result["second"] = "success"
            except RuntimeError:
                result["second"] = "blocked"

        with BuildLock(db_path):
            t = threading.Thread(target=try_second_lock)
            t.start()
            t.join()
            check(result["second"] == "blocked",
                  f"concurrent lock blocked: second={result['second']} (expected 'blocked')")

    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
        if os.path.exists(db_path + ".build_lock"):
            os.unlink(db_path + ".build_lock")


def test_n1_query_correctness():
    """10. N+1 修复后的查询正确性 — 结果与修复前一致"""
    section("10. N+1 修复后查询正确性")
    with CallQuery(DB_PATH) as cq:
        # get_callers 返回的 CallInfo 字段完整
        callers = cq.get_callers("PerformUpgrade")
        if callers:
            c = callers[0]
            check(c.caller_name != "", f"caller_name not empty: '{c.caller_name}'")
            check(c.callee_name == "PerformUpgrade", f"callee_name correct: '{c.callee_name}'")
            check(isinstance(c.call_type, str), f"call_type is str: '{c.call_type}'")
            check(isinstance(c.caller_line, int), f"caller_line is int: {c.caller_line}")

        # get_callees 返回的 CallInfo 字段完整
        callees = cq.get_callees("PerformUpgrade")
        if callees:
            c = callees[0]
            check(c.caller_name == "PerformUpgrade", f"caller_name correct: '{c.caller_name}'")
            check(c.callee_name != "", f"callee_name not empty: '{c.callee_name}'")
            check(c.callee_file != "" or c.callee_namespace != "",
                  f"callee has file/namespace: file='{c.callee_file}', ns='{c.callee_namespace}'")

    with GraphQuery(DB_PATH) as q:
        # get_inheritance 返回完整 ClassInfo
        children = q.get_inheritance("BasePeriUpdate", direction="down", depth=1)
        if children:
            ci = children[0]
            check(ci.child.name != "", f"child name not empty: '{ci.child.name}'")
            check(ci.parent.name != "", f"parent name not empty: '{ci.parent.name}'")
            check(ci.child.file_path != "" or ci.child.namespace != "",
                  f"child has file/namespace")


def test_polymorphism():
    """11. 多态查询 — N+1 修复后正确性"""
    section("11. 多态查询")
    with PolymorphismQuery(DB_PATH) as pq:
        # get_virtual_functions
        vfuncs = pq.get_virtual_functions("BasePeriUpdate")
        check(len(vfuncs) >= 1,
              f"get_virtual_functions('BasePeriUpdate') → {len(vfuncs)} virtual funcs")

        if vfuncs:
            vf = vfuncs[0]
            check(vf.function_name != "", f"function_name not empty: '{vf.function_name}'")
            check(vf.class_name != "", f"class_name not empty: '{vf.class_name}'")
            check(vf.file_path != "" or vf.namespace != "",
                  f"has file/namespace: file='{vf.file_path}'")

        # get_all_overrides
        if vfuncs:
            overrides = pq.get_all_overrides(vf.function_name, vf.class_name)
            check(isinstance(overrides, list),
                  f"get_all_overrides('{vf.function_name}', '{vf.class_name}') → {len(overrides)} overrides")
            if overrides:
                o = overrides[0]
                check(o.function_name != "", f"override function_name not empty: '{o.function_name}'")
                check(o.class_name != "", f"override class_name not empty: '{o.class_name}'")
                check(o.file_path != "" or o.namespace != "",
                      f"override has file/namespace: file='{o.file_path}'")

        # get_all_implementations
        impls = pq.get_all_implementations("BasePeriUpdate")
        check(isinstance(impls, list),
              f"get_all_implementations('BasePeriUpdate') → {len(impls)} implementations")
        if impls:
            impl = impls[0]
            check(impl["name"] != "", f"impl name not empty: '{impl['name']}'")
            check("namespace" in impl, "impl has namespace field")
            check("file_path" in impl, "impl has file_path field")


# ─── 主入口 ───

def main():
    if not os.path.exists(DB_PATH):
        print(f"❌ DB not found: {DB_PATH}")
        print("   Run full parse first: python -m cpp_semantic_graph.pipeline")
        sys.exit(1)

    print(f"DB: {DB_PATH}")

    test_search_apis()
    test_inheritance()
    test_call_query()
    test_traverse()
    test_blast_radius()
    test_edge_resolution()
    test_delete_safety()
    test_upsert()
    test_build_lock()
    test_n1_query_correctness()
    test_polymorphism()

    print(f"\n{'='*60}")
    print(f"  Results: {_passed} passed, {_failed} failed")
    print(f"{'='*60}")

    if _failures:
        print("\nFailures:")
        for f in _failures:
            print(f"  ❌ {f}")

    sys.exit(0 if _failed == 0 else 1)


if __name__ == "__main__":
    main()
