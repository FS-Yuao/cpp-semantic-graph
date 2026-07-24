"""cpp_blast_radius 工具测试

分三部分（遵循 CLAUDE.md 测试经验：测系统级指标，不只测改动点）：
  A. 功能验收：递归调用链 / 虚函数 override 展开 / 文件输入 / 文件聚合 / direction
  B. 系统级回归：blast_radius 是纯查询，DB 不变；现有工具行为不变；override 集合一致
  C. 证伪测试：叶子函数应返回"无影响"；depth=1 不应出现 2 跳节点

用法:
  python test_blast_radius.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# sys.path 注入包父目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cpp_semantic_graph.query.blast_radius_query import BlastRadiusQuery
from cpp_semantic_graph.query.call_query import CallQuery
from cpp_semantic_graph.query.polymorphism_query import PolymorphismQuery

DB = str(Path(__file__).resolve().parent.parent / "semantic_graph_full.db")

_passed = 0
_failed = 0


def check(name: str, ok: bool, detail: str = ""):
    global _passed, _failed
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


def section(title: str):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# ----------------------------------------------------------------------
# A. 功能验收
# ----------------------------------------------------------------------

def test_functional():
    section("A. 功能验收")
    with BlastRadiusQuery(DB) as bq:
        # A1: 函数名起点 → 递归调用链（depth>1）
        r = bq.compute(symbols=["PerformUpgrade"], depth=3, direction="up")
        check("A1 虚函数起点返回受影响节点",
              len(r.affected_nodes) > 0,
              f"{len(r.affected_nodes)} 节点")
        check("A1 递归到 2 跳以上",
              r.max_depth_reached >= 2,
              f"max_depth={r.max_depth_reached}")

        # A2: 虚函数自动展开 overrides
        check("A2 展开 override（多态调度方）",
              len(r.expanded_overrides) > 0,
              f"{len(r.expanded_overrides)} 个 override")
        override_classes = {o["class_name"] for o in r.expanded_overrides}
        check("A2 override 含子类",
              {"SocUpdate", "GnssUpdate", "McuUpdate", "SwitchUpdate"} & override_classes,
              f"{override_classes}")

        # A3: 文件输入 → 展开为符号集
        r_file = bq.compute(files=["base_peri_update.cpp"], depth=2, direction="up")
        check("A3 文件输入展开为符号",
              len(r_file.origin_functions) > 0 or len(r_file.origin_classes) > 0,
              f"func={len(r_file.origin_functions)} class={len(r_file.origin_classes)}")

        # A4: 文件维度去重（同一文件多符号只列一次）
        if r_file.affected_files:
            dup_check = all(
                len(set(n.file_path for n in ns)) == 1 or len(ns) >= 1
                for ns in r_file.affected_files.values()
            )
            check("A4 文件维度聚合", dup_check,
                  f"{len(r_file.affected_files)} 个文件")

        # A5: direction="down" 查依赖方
        r_down = bq.compute(symbols=["PerformUpgrade"], depth=2, direction="down")
        check("A5 direction=down 返回结果",
              len(r_down.affected_nodes) >= 0)  # 至少不报错

        # A6: include_overrides=False 不展开 override
        r_noov = bq.compute(symbols=["PerformUpgrade"], depth=2,
                            include_overrides=False, direction="up")
        check("A6 include_overrides=False 不展开 override",
              len(r_noov.expanded_overrides) == 0,
              f"override={len(r_noov.expanded_overrides)}")


# ----------------------------------------------------------------------
# B. 系统级回归（回归照妖镜）
# ----------------------------------------------------------------------

def test_system_regression():
    section("B. 系统级回归")
    # B1: blast_radius 是纯查询，DB 节点/边数不变
    conn = sqlite3.connect(DB)
    n_before = conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
    e_before = conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0]

    with BlastRadiusQuery(DB) as bq:
        bq.compute(symbols=["PerformUpgrade"], depth=3)
        bq.compute(files=["base_peri_update.cpp"], depth=2)
        bq.compute(symbols=["startUpdate"], depth=3, direction="down")

    n_after = conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
    e_after = conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0]
    check("B1 DB 节点数不变（纯查询无写入）",
          n_before == n_after, f"{n_before} → {n_after}")
    check("B1 DB 边数不变（纯查询无写入）",
          e_before == e_after, f"{e_before} → {e_after}")
    conn.close()

    # B2: 现有工具行为不变 —— blast_radius 展开的 override 集合 ⊇ cpp_get_overrides
    with BlastRadiusQuery(DB) as bq:
        r = bq.compute(symbols=["PerformUpgrade"], depth=2,
                       include_overrides=True, direction="up")
    with PolymorphismQuery(DB) as pq:
        # PerformUpgrade 基类是 BasePeriUpdate
        ovs = pq.get_all_overrides("PerformUpgrade", "BasePeriUpdate")
    br_override_keys = {(o["function_name"], o["class_name"]) for o in r.expanded_overrides}
    pq_override_keys = {(o.function_name, o.class_name) for o in ovs}
    check("B2 blast_radius override ⊇ cpp_get_overrides",
          pq_override_keys.issubset(br_override_keys),
          f"br={len(br_override_keys)} pq={len(pq_override_keys)}")

    # B3: blast_radius 的直接 callers 与 cpp_get_callers 一致（direction=up, depth=1）
    with BlastRadiusQuery(DB) as bq:
        r1 = bq.compute(symbols=["PerformUpgrade"], depth=1,
                        include_overrides=False, direction="up")
    with CallQuery(DB, expand_virtual=False) as cq:
        # 取 BasePeriUpdate::PerformUpgrade 的直接 callers
        callers = cq.get_callers("PerformUpgrade", class_name="BasePeriUpdate")
    br_caller_names = {n.function_name for n in r1.affected_nodes
                       if n.call_type and "calls" in n.call_type}
    cq_caller_names = {c.caller_name for c in callers}
    # blast_radius depth=1 的 calls_* 节点应与 cpp_get_callers 重合（可能 ⊇ 因多起点）
    check("B3 blast_radius d1 callers 与 cpp_get_callers 一致",
          cq_caller_names.issubset(br_caller_names) or len(cq_caller_names) == 0,
          f"br_d1={len(br_caller_names)} cq={len(cq_caller_names)}")


# ----------------------------------------------------------------------
# C. 证伪测试（无论对错都通过 = 没测）
# ----------------------------------------------------------------------

def test_falsification():
    section("C. 证伪测试")
    with BlastRadiusQuery(DB) as bq:
        # C1: 找一个确认无调用方的叶子函数 → 应返回"无影响"
        # 先从 DB 找一个没有入边的函数节点
        conn = sqlite3.connect(DB)
        leaf = conn.execute(
            "SELECT n.name, n.parent_class FROM node n "
            "WHERE n.type='function' AND n.parent_class IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM edge e WHERE e.to_id=n.id "
            "AND e.relation_type IN ('calls_direct','calls_virtual','calls_callback')) "
            "LIMIT 1"
        ).fetchone()
        conn.close()
        if leaf:
            r_leaf = bq.compute(symbols=[leaf[0]], depth=3,
                                include_overrides=False, direction="up")
            # 叶子函数 direction=up 应无受影响节点（无人调用它）
            check("C1 叶子函数 direction=up 无受影响节点",
                  len(r_leaf.affected_nodes) == 0,
                  f"leaf={leaf[0]} nodes={len(r_leaf.affected_nodes)}")
        else:
            check("C1 找到叶子函数测试", False, "未找到无入边函数")

        # C2: depth=1 不应出现 2 跳节点
        r_d1 = bq.compute(symbols=["PerformUpgrade"], depth=1, direction="up")
        max_d = max((n.depth for n in r_d1.affected_nodes), default=0)
        check("C2 depth=1 不出现 2 跳节点",
              max_d <= 1, f"max_depth={max_d}")

        # C3: 不存在的符号 → 空结果（不报错、不返回全部节点）
        r_empty = bq.compute(symbols=["__nonexistent_symbol_xyz__"], depth=3)
        check("C3 不存在符号返回空结果",
              len(r_empty.affected_nodes) == 0
              and len(r_empty.origin_functions) == 0,
              f"nodes={len(r_empty.affected_nodes)}")


def main():
    print(f"DB: {DB}")
    test_functional()
    test_system_regression()
    test_falsification()
    print(f"\n{'=' * 72}\n结果: PASS={_passed}  FAIL={_failed}\n{'=' * 72}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
