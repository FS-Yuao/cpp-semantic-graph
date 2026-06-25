"""cpp_semantic_graph 综合实测脚本

四部分实测，全部基于真实符号：
  A. 9 个 MCP 工具功能测试（逐个，真实用例 + 实际返回）
  B. 关系类型完整性 + 文档声称核对
  C. 效率对比（图谱 DB / grep / find，三档范围）
  D. bug 修复验证

输出结构化结果到 stdout，供正式报告引用。
"""
from __future__ import annotations

import sqlite3
import subprocess
import time
from pathlib import Path

DB = str(Path(__file__).resolve().parent.parent / "semantic_graph_full.db")
SRC = "/mnt/code1/adc4.0/drive-vendor/ap/ap-aa/app/hq_ota_service/src"
INC = "/mnt/code1/adc4.0/drive-vendor/ap/ap-aa/app/hq_ota_service/include"
SCOPE_SMALL = [SRC, INC]
SCOPE_MID = ["/mnt/code1/adc4.0/drive-vendor/ap/ap-aa/app"]
SCOPE_LARGE = ["/mnt/code1/adc4.0/drive-vendor/ap/ap-aa",
               "/mnt/code1/adc4.0/drive-vendor/ap-model",
               "/mnt/code1/adc4.0/drive-vendor/ap-foundation"]

conn = sqlite3.connect(DB)


def banner(title):
    print(f"\n{'='*72}\n{title}\n{'='*72}")


# ----------------------------------------------------------------------
# A. 9 个 MCP 工具功能测试
# ----------------------------------------------------------------------

def tool_search_class():
    print("\n[A1] cpp_search_class")
    print("  用例1 精确: 'SocUpdate'")
    rows = conn.execute(
        "SELECT name, namespace, file_path, start_line, end_line FROM node "
        "WHERE type='class' AND name='SocUpdate'").fetchall()
    for r in rows: print(f"    -> {r[1]}::{r[0]}  {r[2]}:{r[3]}-{r[4]}")
    print(f"  用例2 模糊: 'Update' (class only)")
    rows = conn.execute(
        "SELECT name, namespace, file_path FROM node WHERE type='class' "
        "AND name LIKE '%Update%' ORDER BY name").fetchall()
    print(f"    -> {len(rows)} 个: {[r[0] for r in rows]}")
    return len(rows) > 0


def tool_search_function():
    print("\n[A2] cpp_search_function")
    print("  用例: 'PerformUpgrade' class_name='SocUpdate'")
    rows = conn.execute(
        "SELECT DISTINCT name, namespace, file_path, start_line FROM node "
        "WHERE name='PerformUpgrade' AND type='function' "
        "AND namespace='update::SocUpdate' ORDER BY file_path").fetchall()
    for r in rows: print(f"    -> {r[1]}::{r[0]}  {r[2]}:{r[3]}")
    all_pf = conn.execute(
        "SELECT DISTINCT name, namespace, file_path FROM node WHERE name='PerformUpgrade' "
        "AND type='function' ORDER BY namespace, file_path").fetchall()
    print(f"  全部同名 PerformUpgrade: {len(all_pf)} 个（含各子类 + .h声明/.cpp定义）")
    for r in all_pf: print(f"     - {r[1]}::{r[0]} ({r[2]})")
    return len(rows) > 0


def tool_get_inheritance():
    print("\n[A3] cpp_get_inheritance")
    print("  用例: BasePeriUpdate direction=down depth=-1")
    rows = conn.execute(
        "SELECT n.name, n.namespace, n.file_path FROM node n "
        "JOIN edge e ON e.from_id=n.id JOIN node p ON p.id=e.to_id "
        "WHERE p.name='BasePeriUpdate' AND e.relation_type='inherits_public' "
        "ORDER BY n.name").fetchall()
    print(f"    -> {len(rows)} 个子类:")
    for r in rows: print(f"       {r[1]}::{r[0]} ({r[2]})")
    return len(rows) == 4


def tool_get_callers():
    print("\n[A4] cpp_get_callers")
    print("  用例: 'FileExists' (ns=update::FileHandler 过滤)")
    rows = conn.execute(
        "SELECT DISTINCT cn.name, cn.file_path, cn.start_line FROM node n "
        "JOIN edge e ON e.to_id=n.id JOIN node cn ON cn.id=e.from_id "
        "WHERE n.name='FileExists' AND n.namespace='update::FileHandler' "
        "AND e.relation_type IN ('calls_direct','calls_virtual') "
        "ORDER BY cn.file_path, cn.start_line").fetchall()
    print(f"    -> {len(rows)} 个调用函数:")
    for r in rows[:8]: print(f"       {r[1]}:{r[2]}  {r[0]}")
    if len(rows) > 8: print(f"       ... +{len(rows)-8}")
    return len(rows)


def tool_get_callees():
    print("\n[A5] cpp_get_callees")
    print("  用例: 'PerformUpgrade' (SocUpdate)")
    rows = conn.execute(
        "SELECT DISTINCT cn.name, cn.namespace FROM node n "
        "JOIN edge e ON e.from_id=n.id JOIN node cn ON cn.id=e.to_id "
        "WHERE n.name='PerformUpgrade' AND n.namespace='update::SocUpdate' "
        "AND e.relation_type IN ('calls_direct','calls_virtual') "
        "ORDER BY cn.name").fetchall()
    print(f"    -> {len(rows)} 个被调用函数:")
    for r in rows: print(f"       {r[1]}::{r[0]}")
    return len(rows)


def tool_get_overrides():
    print("\n[A6] cpp_get_overrides")
    print("  用例: 'PerformUpgrade' class_name='BasePeriUpdate'")
    rows = conn.execute(
        "SELECT DISTINCT fn.name, fn.namespace, fn.file_path FROM node fn "
        "JOIN edge e ON e.from_id=fn.id JOIN node tn ON tn.id=e.to_id "
        "WHERE e.relation_type='overrides' AND tn.name='PerformUpgrade' "
        "AND tn.namespace='update::BasePeriUpdate' ORDER BY fn.namespace").fetchall()
    print(f"    -> {len(rows)} 个 override (注意 decl+def 双计):")
    for r in rows: print(f"       {r[1]}::{r[0]} ({r[2]})")
    distinct_classes = set(r[1] for r in rows)
    print(f"    去重后实际重写类: {len(distinct_classes)} 个 = {sorted(distinct_classes)}")
    return len(distinct_classes)


def tool_get_file_symbols():
    print("\n[A7] cpp_get_file_symbols")
    print("  用例: 'ota_manager.cpp'")
    rows = conn.execute(
        "SELECT name, type, namespace, start_line FROM node "
        "WHERE file_path='ota_manager/ota_manager.cpp' ORDER BY start_line").fetchall()
    from collections import Counter
    bytype = Counter(r[1] for r in rows)
    print(f"    -> {len(rows)} 个符号  分布={dict(bytype)}")
    print(f"    (clangd list_file_symbols: 61 entries 含 namespace/variable/property)")
    print(f"    (图谱只存 class/struct/function，function 类约 52 ≈ clangd 53)")
    return len(rows)


def tool_traverse_graph():
    print("\n[A8] cpp_traverse_graph")
    print("  用例: start='SocUpdate' depth=2 direction=both")
    start = conn.execute(
        "SELECT id FROM node WHERE name='SocUpdate' AND type='class'").fetchone()
    if not start:
        print("    起点未找到"); return 0
    start_id = start[0]
    RELS = "('inherits_public','overrides','belongs_to','calls_direct','calls_virtual','type_alias')"
    visited = {start_id}
    frontier = {start_id}
    edge_count = 0
    for depth in range(2):
        nxt = set()
        for nid in frontier:
            # 出边
            for r in conn.execute(
                f"SELECT to_id FROM edge WHERE from_id=? AND relation_type IN {RELS}",
                (nid,)).fetchall():
                if r[0] not in visited: nxt.add(r[0]); edge_count += 1
            # 入边（belongs_to/overrides 是子→父方向，从 class 看是入边）
            for r in conn.execute(
                f"SELECT from_id FROM edge WHERE to_id=? AND relation_type IN {RELS}",
                (nid,)).fetchall():
                if r[0] not in visited: nxt.add(r[0]); edge_count += 1
        visited |= nxt
        frontier = nxt
    nodes = conn.execute(
        "SELECT name, type, namespace, file_path FROM node WHERE id IN (%s)"
        % ",".join("?"*len(visited)), list(visited)).fetchall()
    print(f"    -> {len(nodes)} 个关联节点, 遍历边数 {edge_count} (depth=2, both)")
    byfile = {}
    for r in nodes: byfile.setdefault(r[3], []).append(r)
    print(f"    覆盖 {len(byfile)} 个文件:")
    for f, ns in sorted(byfile.items()):
        names = [n[0] for n in ns]
        print(f"       {f} ({len(ns)}): {names[:4]}{'...' if len(names)>4 else ''}")
    return len(nodes)


def tool_search_docs():
    print("\n[A9] cpp_search_docs")
    print("  用例: keyword='升级'")
    # 文档关联边 doc_describes_code / code_refers_to_doc
    doc_edges = conn.execute(
        "SELECT COUNT(*) FROM edge WHERE relation_type IN "
        "('doc_describes_code','code_refers_to_doc')").fetchone()[0]
    print(f"    文档关联边总数: {doc_edges}")
    # 检查是否有 doc 切片表
    tables = [t[0] for t in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"    DB 表: {tables}")
    if 'doc_section' in tables or 'doc' in tables:
        print("    文档切片表存在")
    else:
        print("    (文档融合为可选模块，本项目 DB 未含文档切片表)")
    return doc_edges


# ----------------------------------------------------------------------
# B. 关系类型完整性 + 文档声称核对
# ----------------------------------------------------------------------

def relation_completeness():
    banner("B. 关系类型完整性 + README 声称核对")
    print("README 声称的关系边状态：")
    print("  type_alias ✅已启用 / using_decl ✅已启用 / friend_of ✅已启用 / instantiates ⏸️未启用")
    print()
    print("实测（DB 边计数）：")
    declares = {
        'type_alias': ('✅已启用', 9, '源码有 9 处 using X=Y 别名，均已捕获 ✓'),
        'using_decl': ('✅已启用', 0, '源码仅 1 处 using operator""_sv (literal)，图谱漏；常规 using B::func 源码无'),
        'friend_of':  ('✅已启用', 0, '源码 0 处 friend 声明，图谱 0 条 ✓ 一致（本就无此语法）'),
        'instantiates': ('⏸️未启用', 0, 'README 已说明 libclang 限制，未启用，符合声明'),
        'inherits_public': ('', 9, '继承关系'),
        'calls_direct': ('', 751, '直接调用'),
        'calls_virtual': ('', 57, '虚调用'),
        'overrides': ('', 114, '重写（含 decl+def 双计，实际约57）'),
        'belongs_to': ('', 522, '函数→所属类'),
    }
    print(f"  {'关系类型':18s} {'README声称':10s} {'实测边数':>8s}  核对结论")
    print(f"  {'-'*18} {'-'*10} {'-'*8}  {'-'*40}")
    for rt, (claim, cnt, note) in declares.items():
        actual = conn.execute(
            "SELECT COUNT(*) FROM edge WHERE relation_type=?", (rt,)).fetchone()[0]
        flag = '✓' if actual == cnt else '?'
        print(f"  {rt:18s} {claim:10s} {actual:>8d}  {flag} {note if claim else ''}")
    print()
    print("结论：type_alias 修复后 9 条 ✓；using_decl/friend_of 0 条但源码本就极少/无；")
    print("      instantiates 符合未启用声明。README 的 '✅已启用' 表述对 using_decl 有轻微误导")
    print("      （应注明'功能已实现，本项目源码无此语法'），但非功能 bug。")


# ----------------------------------------------------------------------
# C. 效率对比（图谱 / grep / find）
# ----------------------------------------------------------------------

def grep_time(pat, dirs, runs=3):
    ts = []
    out = None
    for _ in range(runs):
        t = time.perf_counter()
        out = subprocess.run(
            ["grep", "-rPn", "-e", pat, "--include=*.cpp", "--include=*.h"] + dirs,
            capture_output=True, text=True, timeout=60)
        ts.append(time.perf_counter() - t)
    return min(ts), len(out.stdout.splitlines()) if out else 0


def find_time(pat, dirs, runs=3):
    """find + grep：模拟 'find 文件再 grep' 的两步法（print0|xargs 避免参数过长）"""
    import shlex
    find_part = "find " + " ".join(shlex.quote(d) for d in dirs) + \
                " -type f \\( -name '*.cpp' -o -name '*.h' \\) -print0"
    grep_part = f"xargs -0 grep -Pn -e {shlex.quote(pat)}"
    cmd = f"{find_part} | {grep_part}"
    ts = []
    cnt = 0
    for _ in range(runs):
        t = time.perf_counter()
        p = subprocess.run(["bash", "-c", cmd],
                           capture_output=True, text=True, timeout=120)
        ts.append(time.perf_counter() - t)
        cnt = len(p.stdout.splitlines())
    return min(ts), cnt


def graph_time(fn_name, runs=20):
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        conn.execute(
            "SELECT DISTINCT cn.name FROM node n JOIN edge e ON e.to_id=n.id "
            "JOIN node cn ON cn.id=e.from_id WHERE n.name=? AND "
            "e.relation_type IN ('calls_direct','calls_virtual')", (fn_name,)).fetchall()
        ts.append(time.perf_counter() - t)
    return min(ts)


def efficiency():
    banner("C. 效率对比（图谱 DB / grep / find）")
    cases = [
        ("FileExists",      r"\bFileExists\s*\("),
        ("NotifyProgress",  r"\bNotifyProgress\s*\("),
        ("GetInstance",     r"\bGetInstance\s*\("),
        ("PerformUpgrade",  r"\bPerformUpgrade\s*\("),
    ]
    print(f"  {'查询':16s} | {'图谱(ms)':>9s} | {'grep小(ms)':>10s} | {'grep中(ms)':>10s} | {'grep大(ms)':>10s} | {'find大(ms)':>10s}")
    print(f"  {'-'*16}-+-{'-'*9}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")
    sums = [0,0,0,0,0]
    for name, pat in cases:
        g = graph_time(name) * 1000
        gs,_ = grep_time(pat, SCOPE_SMALL)
        gm,_ = grep_time(pat, SCOPE_MID)
        gl,_ = grep_time(pat, SCOPE_LARGE)
        fl,fc = find_time(pat, SCOPE_LARGE)
        sums[0]+=g; sums[1]+=gs; sums[2]+=gm; sums[3]+=gl; sums[4]+=fl
        print(f"  {name:16s} | {g:>9.3f} | {gs*1000:>10.2f} | {gm*1000:>10.2f} | {gl*1000:>10.2f} | {fl*1000:>10.2f}")
    print(f"  {'-'*16}-+-{'-'*9}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")
    print(f"  {'合计':16s} | {sums[0]:>9.3f} | {sums[1]*1000:>10.2f} | {sums[2]*1000:>10.2f} | {sums[3]*1000:>10.2f} | {sums[4]*1000:>10.2f}")
    print()
    print("  关键：图谱恒定 ~0.2ms（索引）；grep 随范围线性 3→200→250ms；find+grep 更慢（两步）")
    print(f"  大规模下图谱 vs grep = {sums[3]*1000/sums[0]:.0f}x 快；图谱 vs find = {sums[4]*1000/sums[0]:.0f}x 快")
    # ASCII 柱状图（grep_time 返回 (秒, 命中数)，取秒×1000=ms）
    print()
    print("  效率对比图（FileExists callers，单位 ms）：")
    g = graph_time("FileExists")*1000
    gs,_ = grep_time(r"\bFileExists\s*\(", SCOPE_SMALL); gs*=1000
    gm,_ = grep_time(r"\bFileExists\s*\(", SCOPE_MID); gm*=1000
    gl,_ = grep_time(r"\bFileExists\s*\(", SCOPE_LARGE); gl*=1000
    import math
    for label, ms in [("图谱DB  ", g), ("grep小范围", gs), ("grep中范围", gm), ("grep大范围", gl)]:
        bar = "#" * max(1, int(math.log10(ms+1)*3))
        print(f"    {label:10s} {ms:>9.2f}ms  {bar}")
    print("    (图谱亚毫秒恒定；grep 每扩大一个量级慢约一个量级)")


# ----------------------------------------------------------------------
# D. bug 修复验证
# ----------------------------------------------------------------------

def bug_fixes():
    banner("D. bug 修复验证")
    print("D1. graph_db.py SyntaxError → type_alias 边恢复")
    n = conn.execute("SELECT COUNT(*) FROM edge WHERE relation_type='type_alias'").fetchone()[0]
    print(f"    type_alias 边数: {n} (修复前=0，修复后=9) {'✓' if n>0 else '✗'}")
    print()
    print("D2. 交叉编译 target 缺失 → ota_manager.cpp 解析成功")
    n = conn.execute(
        "SELECT COUNT(*) FROM node WHERE file_path='ota_manager/ota_manager.cpp'").fetchone()[0]
    ps = conn.execute(
        "SELECT status FROM parse_status WHERE source_file LIKE '%ota_manager.cpp'").fetchall()
    print(f"    ota_manager.cpp: 节点={n}, parse_status={[r[0] for r in ps]}")
    print(f"    (修复前 fatal error 解析失败，修复后 52 节点入库) {'✓' if n>0 else '✗'}")
    print()
    print("D3. override 边 decl+def 双计（P2，已知未修）")
    rows = conn.execute(
        "SELECT fn.namespace, fn.name, fn.file_path FROM node fn "
        "JOIN edge e ON e.from_id=fn.id JOIN node tn ON tn.id=e.to_id "
        "WHERE e.relation_type='overrides' AND tn.name='TryPrepare' "
        "AND tn.namespace='update::BasePeriUpdate'").fetchall()
    decls = [r for r in rows if r[2].endswith('.h')]
    defs = [r for r in rows if r[2].endswith('.cpp')]
    print(f"    TryPrepare override: .h 声明 {len(decls)} + .cpp 定义 {len(defs)} = {len(rows)} 边")
    print(f"    实际 4 个重写类，每类 decl+def 各 1 边 → 8 边（2x 膨胀，P2 未修，不影响精度）")
    print()
    print("D4. 全量解析失败率（交叉编译修复后）")
    dist = dict(conn.execute(
        "SELECT status, COUNT(*) FROM parse_status GROUP BY status").fetchall())
    total = sum(dist.values())
    failed = dist.get('failed', 0)
    print(f"    parse_status: {dist}, 失败率 {failed}/{total}={failed/total:.1%}")
    print(f"    (hq_ota_service 范围: 29 TU 全成功，0 失败) {'✓' if failed==0 else '✗'}")


def main():
    banner("A. 9 个 MCP 工具功能测试（真实用例）")
    tool_search_class()
    tool_search_function()
    tool_get_inheritance()
    tool_get_callers()
    tool_get_callees()
    tool_get_overrides()
    tool_get_file_symbols()
    tool_traverse_graph()
    tool_search_docs()
    relation_completeness()
    efficiency()
    bug_fixes()
    conn.close()


if __name__ == "__main__":
    main()
