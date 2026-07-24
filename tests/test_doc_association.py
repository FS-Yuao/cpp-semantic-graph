"""文档↔代码关联测试（P0 正向过滤 + P1 反向工具）

遵循 CLAUDE.md 测试经验：测系统级指标，不只测改动点。
  A. P1 反向：cpp_get_code_docs（代码->文档）返回相关设计文档
  B. P0 正向：cpp_search_docs 默认 min_confidence=0.7 过滤低质量关联
  C. 系统级回归：纯查询无写入；反向关联 confidence 普遍 0.6（content_scan）
  D. 证伪：不存在符号返回空；min_confidence=1.0 仅高质量
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cpp_semantic_graph.query.doc_query import DocQuery

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


def test_reverse():
    section("A. P1 反向：cpp_get_code_docs（代码 -> 文档）")
    with DocQuery(DB) as dq:
        # A1: PerformUpgrade 返回设计文档
        docs = dq.get_docs_for_function("PerformUpgrade")
        check("A1 PerformUpgrade 返回关联文档",
              len(docs) > 0, f"{len(docs)} 个切片")
        titles = {d.title for d in docs}
        files = {d.file_path for d in docs}
        # 应命中核心设计文档之一
        hit_design = any("AB_PARTITION" in f or "OTA_SW_HLD" in f or "ARCHITECTURE" in f
                        for f in files)
        check("A1 命中核心设计文档", hit_design, ",".join(list(files)[:3]))

        # A2: 切片字段完整（title/file_path/行号/预览）
        if docs:
            d = docs[0]
            check("A2 切片字段完整",
                  bool(d.title) and bool(d.file_path)
                  and d.start_line >= 0 and d.end_line >= 0,
                  f"title={d.title[:20]} file={d.file_path[-30:]}")

        # A3: SocUpdate（类）反向也工作
        docs_c = dq.get_docs_for_class("SocUpdate")
        check("A3 SocUpdate 类反向返回文档",
              len(docs_c) > 0, f"{len(docs_c)} 个切片")

        # A4: 不存在符号返回空（不报错）
        docs_empty = dq.get_docs_for_function("__nonexistent_xyz__")
        check("A4 不存在符号返回空", len(docs_empty) == 0)


def test_forward_filter():
    section("B. P0 正向：默认 min_confidence=0.7 过滤低质量关联")
    with DocQuery(DB) as dq:
        # B1: 默认 0.7 过滤 0.6 的关联代码（刷写文档的关联代码应减少）
        docs_07 = dq.search_documentation("刷写", max_results=5, min_confidence=0.7)
        docs_00 = dq.search_documentation("刷写", max_results=5, min_confidence=0.0)
        # 统计关联代码数
        rc_07 = sum(len(d.related_code) for d in docs_07)
        rc_00 = sum(len(d.related_code) for d in docs_00)
        check("B1 0.7 过滤后关联代码 ≤ 0.0",
              rc_07 <= rc_00, f"0.7->{rc_07} 条, 0.0->{rc_00} 条")

        # B2: 文档命中数不变（min_confidence 只过滤关联代码，不过滤文档）
        check("B2 文档命中数不变（只过滤关联代码）",
              len(docs_07) == len(docs_00), f"0.7->{len(docs_07)} 篇, 0.0->{len(docs_00)} 篇")


def test_system_regression():
    section("C. 系统级回归")
    # C1: 纯查询，DB 不变
    conn = sqlite3.connect(DB)
    n0 = conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
    e0 = conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0]
    with DocQuery(DB) as dq:
        dq.get_docs_for_function("PerformUpgrade")
        dq.get_docs_for_class("SocUpdate")
        dq.search_documentation("升级", min_confidence=0.7)
    n1 = conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
    e1 = conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0]
    check("C1 纯查询 DB 节点不变", n0 == n1, f"{n0}->{n1}")
    check("C1 纯查询 DB 边不变", e0 == e1, f"{e0}->{e1}")
    conn.close()

    # C2: 反向关联 confidence 普遍 0.6（content_scan），证明默认 0.0 的合理性
    conn = sqlite3.connect(DB)
    dist = dict(conn.execute(
        "SELECT ROUND(confidence,1), COUNT(*) FROM edge "
        "WHERE relation_type='code_refers_to_doc' GROUP BY ROUND(confidence,1)"
    ).fetchall())
    conn.close()
    c06 = dist.get(0.6, 0)
    total = sum(dist.values())
    check("C2 反向关联 0.6 占多数（默认0.0不过滤合理）",
          total > 0 and c06 / total > 0.5, f"0.6占{c06}/{total}")


def test_falsification():
    section("D. 证伪测试")
    with DocQuery(DB) as dq:
        # D1: min_confidence=1.0 仅高质量（PerformUpgrade 的关联都是0.6，应返回空或极少）
        docs_10 = dq.get_docs_for_function("PerformUpgrade", min_confidence=1.0)
        docs_00 = dq.get_docs_for_function("PerformUpgrade", min_confidence=0.0)
        check("D1 min_confidence=1.0 比 0.0 少",
              len(docs_10) <= len(docs_00),
              f"1.0->{len(docs_10)} 0.0->{len(docs_00)}")


def main():
    print(f"DB: {DB}")
    test_reverse()
    test_forward_filter()
    test_system_regression()
    test_falsification()
    print(f"\n{'=' * 72}\n结果: PASS={_passed}  FAIL={_failed}\n{'=' * 72}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
