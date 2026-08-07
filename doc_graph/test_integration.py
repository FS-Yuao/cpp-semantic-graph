#!/usr/bin/env python3
"""
文档知识图谱 × 代码语义图谱 — 融合集成测试

测试矩阵：
  T1  数据库完整性        — 节点/边引用一致性、无孤儿、FTS5 可用
  T2  Frontmatter 覆盖率   — 迁移后 legacy 比例、字段完整性
  T3  FTS5 全文搜索        — 中文/英文/混合关键词命中
  T4  BFS 图遍历           — depth 0/1/2，三种边类型都遍历到
  T5  符号提取             — mentions_symbol 边指向合法符号名
  T6  代码↔文档桥接（核心）— doc_graph 符号 → cppsg 代码节点存在性验证
  T7  反向查找             — 从已知代码函数 → 找到引用它的文档
  T8  端到端搜索           — keyword → docs → knowledge → symbols → code_nodes
  T9  MCP 工具契约         — 三个 MCP 工具返回合法 JSON
"""

from __future__ import annotations
import os, sys, json, sqlite3, re, time
from collections import Counter, defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOC_DB = os.path.join(SCRIPT_DIR, "doc_graph.db")
CPPSG_DB = os.path.join(SCRIPT_DIR, "..", "semantic_graph_full.db")

sys.path.insert(0, SCRIPT_DIR)
from query import doc_graph_search, fts5_search, bfs_traverse, bridge_symbol_to_cppsg, get_conn

# ─── 测试框架 ─────────────────────────────────────────────────

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []

def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(f"  ✅ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ❌ {name}  {detail}")

def skip(name, reason=""):
    global SKIP
    SKIP += 1
    RESULTS.append(f"  ⏭️  {name}  (跳过: {reason})")

def section(title):
    RESULTS.append(f"\n{'─'*60}")
    RESULTS.append(f" {title}")
    RESULTS.append(f"{'─'*60}")


# ─── T1: 数据库完整性 ─────────────────────────────────────────

def test_db_integrity(conn):
    section("T1: 数据库完整性")

    # 节点总数 > 0
    node_count = conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
    test("节点总数 > 0", node_count > 0, f"actual={node_count}")

    # 边总数 > 0
    edge_count = conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0]
    test("边总数 > 0", edge_count > 0, f"actual={edge_count}")

    # 节点类型: document/knowledge/symbol（symbol 为被引用代码符号的实体节点）
    types = dict(conn.execute(
        "SELECT type, COUNT(*) FROM node GROUP BY type").fetchall())
    test("节点类型合法", set(types.keys()) <= {"document", "knowledge", "symbol"},
         f"types={set(types.keys())}")

    # 边类型只有三种
    rels = dict(conn.execute(
        "SELECT rel, COUNT(*) FROM edge GROUP BY rel").fetchall())
    test("边类型合法", set(rels.keys()) == {"has_knowledge", "relates_to", "mentions_symbol"},
         f"rels={set(rels.keys())}")

    # has_knowledge 边的 dst 都存在于 node 表
    orphan_hk = conn.execute("""
        SELECT COUNT(*) FROM edge e
        WHERE e.rel = 'has_knowledge'
          AND e.dst NOT IN (SELECT id FROM node)
    """).fetchone()[0]
    test("has_knowledge 边 dst 全部存在", orphan_hk == 0,
         f"orphan={orphan_hk}")

    # relates_to 边的 src 和 dst 都必须是 document 节点（后处理已丢弃无法匹配的悬挂边）
    orphan_rt = conn.execute("""
        SELECT COUNT(*) FROM edge e
        WHERE e.rel = 'relates_to'
          AND (e.src NOT IN (SELECT id FROM node WHERE type='document')
            OR e.dst NOT IN (SELECT id FROM node WHERE type='document'))
    """).fetchone()[0]
    rt_total = conn.execute(
        "SELECT COUNT(*) FROM edge WHERE rel='relates_to'").fetchone()[0]
    orphan_rate = orphan_rt / rt_total if rt_total > 0 else 1.0
    test(f"relates_to 悬挂边为 0 (orphan={orphan_rt}/{rt_total})",
         orphan_rt == 0,
         f"orphan_rate={orphan_rate:.1%}")

    # mentions_symbol 边的 dst 必须有对应 symbol 节点（后处理已建节点，应为 0）
    orphan_ms = conn.execute("""
        SELECT COUNT(*) FROM edge e
        WHERE e.rel = 'mentions_symbol'
          AND e.dst NOT IN (SELECT id FROM node)
    """).fetchone()[0]
    test("mentions_symbol 边 dst 全部存在", orphan_ms == 0,
         f"orphan={orphan_ms}")

    # 无重复边
    dup_edges = conn.execute("""
        SELECT src, dst, rel, COUNT(*) as cnt FROM edge
        GROUP BY src, dst, rel HAVING cnt > 1
    """).fetchall()
    test("无重复边", len(dup_edges) == 0, f"duplicates={len(dup_edges)}")

    # FTS5 表存在且有数据
    fts_count = conn.execute(
        "SELECT COUNT(*) FROM doc_fts").fetchone()[0]
    test("FTS5 表有数据", fts_count > 0, f"fts_rows={fts_count}")

    # 孤立文档（没有任何出边/入边）-- 分母用文档数，非全节点（避免断言形同虚设）
    doc_count = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='document'").fetchone()[0]
    isolated = conn.execute("""
        SELECT COUNT(*) FROM node n
        WHERE n.type = 'document'
          AND n.id NOT IN (SELECT src FROM edge WHERE src LIKE 'doc:%')
          AND n.id NOT IN (SELECT dst FROM edge WHERE dst LIKE 'doc:%')
    """).fetchone()[0]
    test(f"孤立文档 < 20% (isolated={isolated}/{doc_count})",
         isolated < doc_count * 0.2,
         f"rate={isolated/max(1,doc_count):.1%}")

    return {"nodes": node_count, "edges": edge_count,
            "types": types, "rels": rels, "isolated": isolated}


# ─── T2: Frontmatter 覆盖率 ───────────────────────────────────

def test_frontmatter(conn):
    section("T2: Frontmatter 覆盖率")

    total_docs = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='document'").fetchone()[0]
    legacy = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='document' AND legacy=1").fetchone()[0]
    manual = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='document' AND manual=1").fetchone()[0]

    test("frontmatter 覆盖率 > 80%", (total_docs - legacy) / total_docs > 0.8,
         f"legacy={legacy}/{total_docs} ({100*legacy//total_docs}%)")
    test("manual 文档 > 0", manual > 0, f"manual={manual}")

    # 有 doc_type 的文档
    with_type = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='document' AND doc_type IS NOT NULL AND doc_type != ''"
    ).fetchone()[0]
    test("有 doc_type 的文档 > 80%", with_type / total_docs > 0.8,
         f"with_type={with_type}/{total_docs}")

    # 有 tags 的文档（非空列表）
    with_tags = conn.execute("""
        SELECT COUNT(*) FROM node
        WHERE type='document' AND tags IS NOT NULL AND tags != '[]' AND tags != ''
    """).fetchone()[0]
    test("有 tags 的文档 > 40%", with_tags / total_docs > 0.4,
         f"with_tags={with_tags}/{total_docs}")

    # doc_type 分布合理
    dt_dist = dict(conn.execute("""
        SELECT doc_type, COUNT(*) FROM node
        WHERE type='document' GROUP BY doc_type ORDER BY COUNT(*) DESC
    """).fetchall())
    test("doc_type 分布 > 3 种", len([k for k in dt_dist if k]) > 3,
         f"distribution={dt_dist}")

    # ── summary 覆盖率（关键质量指标）──
    doc_with_summary = conn.execute("""
        SELECT COUNT(*) FROM node
        WHERE type='document' AND summary IS NOT NULL AND summary != ''
    """).fetchone()[0]
    test("文档摘要覆盖率 > 85%", doc_with_summary / total_docs > 0.85,
         f"with_summary={doc_with_summary}/{total_docs}")

    # 文档摘要长度合理（10-300 字）
    short_doc_summaries = conn.execute("""
        SELECT COUNT(*) FROM node
        WHERE type='document' AND summary IS NOT NULL
          AND (length(summary) < 10 OR length(summary) > 500)
    """).fetchone()[0]
    test("文档摘要长度合理 (10-500字)", short_doc_summaries < total_docs * 0.15,
         f"bad_length={short_doc_summaries}/{total_docs}")

    # 知识点摘要覆盖率
    total_know = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='knowledge'").fetchone()[0]
    know_with_summary = conn.execute("""
        SELECT COUNT(*) FROM node
        WHERE type='knowledge' AND summary IS NOT NULL AND summary != ''
    """).fetchone()[0]
    test("知识点摘要覆盖率 > 70%", know_with_summary / total_know > 0.7,
         f"with_summary={know_with_summary}/{total_know}")

    # 高频标题必须有摘要（"概述"、"目标"等无信息量标题必须有摘要区分）
    generic_titles = ['概述', '目标', '风险点', '验收标准', '现状问题', '设计方案', '总结']
    generic_without_summary = conn.execute("""
        SELECT COUNT(*) FROM node
        WHERE type='knowledge' AND title IN ({})
          AND (summary IS NULL OR summary = '')
    """.format(",".join(f"'{t}'" for t in generic_titles))).fetchone()[0]
    generic_total = conn.execute("""
        SELECT COUNT(*) FROM node
        WHERE type='knowledge' AND title IN ({})
    """.format(",".join(f"'{t}'" for t in generic_titles))).fetchone()[0]
    if generic_total > 0:
        test(f"高频标题({generic_total}个)有摘要率 > 90%",
             (generic_total - generic_without_summary) / generic_total > 0.9,
             f"without_summary={generic_without_summary}/{generic_total}")
    else:
        test("无高频标题（跳过）", True, "no generic titles")


# ─── T3: FTS5 全文搜索 ────────────────────────────────────────

def test_fts5(conn):
    section("T3: FTS5 全文搜索")

    keywords = ["分区", "OTA", "GNSS", "分区切换", "OtaManager"]
    for kw in keywords:
        results = fts5_search(conn, kw, limit=5)
        test(f"FTS5 搜 '{kw}' 有结果", len(results) > 0,
             f"results={len(results)}")

    # 验证 LIKE 降级
    like_results = fts5_search(conn, "doPartitionSwitch", limit=5)
    test("符号名搜索能降级到 symbol 反查", len(like_results) > 0,
         f"results={len(like_results)}")


# ─── T4: BFS 图遍历 ──────────────────────────────────────────

def test_bfs(conn):
    section("T4: BFS 图遍历")

    # 取一个有 relates_to 边的文档
    doc_with_edges = conn.execute("""
        SELECT src FROM edge WHERE rel='relates_to' LIMIT 1
    """).fetchone()
    if not doc_with_edges:
        skip("BFS 遍历", "无 relates_to 边")
        return

    start_id = doc_with_edges["src"]

    # depth 0: 只有起始节点
    r0 = bfs_traverse(conn, start_id, depth=0)
    test("depth=0 返回起始文档", len(r0["docs"]) >= 1,
         f"docs={len(r0['docs'])}")

    # depth 1: 至少有一些边
    r1 = bfs_traverse(conn, start_id, depth=1)
    total_1 = len(r1["docs"]) + len(r1["knowledge"]) + len(r1["symbols"])
    test("depth=1 有关联结果", total_1 >= 2,
         f"docs={len(r1['docs'])} knowledge={len(r1['knowledge'])} symbols={len(r1['symbols'])}")

    # depth 2: 结果 >= depth 1
    r2 = bfs_traverse(conn, start_id, depth=2)
    total_2 = len(r2["docs"]) + len(r2["knowledge"]) + len(r2["symbols"])
    test("depth=2 结果 >= depth=1", total_2 >= total_1,
         f"d1={total_1} d2={total_2}")

    # 验证三种边类型在某个文档上都有遍历到
    all_symbols = set()
    all_knowledge = set()
    all_docs = set()
    for r in [r0, r1, r2]:
        for d in r["docs"]: all_docs.add(d["id"])
        for k in r["knowledge"]: all_knowledge.add(k["id"])
        for s in r["symbols"]: all_symbols.add(s["name"])

    test("BFS 能遍历到知识点", len(all_knowledge) > 0,
         f"knowledge={len(all_knowledge)}")
    test("BFS 能遍历到符号", len(all_symbols) > 0,
         f"symbols={len(all_symbols)}")


# ─── T5: 符号提取质量 ─────────────────────────────────────────

def test_symbol_extraction(conn):
    section("T5: 符号提取质量")

    # 获取所有 mentions_symbol 边
    sym_edges = conn.execute("""
        SELECT src, dst FROM edge WHERE rel='mentions_symbol'
    """).fetchall()

    test("mentions_symbol 边 > 100", len(sym_edges) > 100,
         f"count={len(sym_edges)}")

    # 符号名格式检查（允许 C++ 标识符 + :: + Python . 记法 + 文件名）
    bad_names = []
    for e in sym_edges:
        sym = e["dst"].replace("symbol:", "")
        # 允许: 字母数字下划线 + :: (C++) + . (Python/filename) + - (kebab filename)
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_./\-]*(::[A-Za-z_][A-Za-z0-9_]*)*$', sym):
            bad_names.append(sym)

    test("符号名格式合法 (>99%)", len(bad_names) < len(sym_edges) * 0.01,
         f"bad={len(bad_names)}/{len(sym_edges)} samples={bad_names[:5]}")

    # 唯一符号数
    unique_syms = conn.execute("""
        SELECT COUNT(DISTINCT dst) FROM edge WHERE rel='mentions_symbol'
    """).fetchone()[0]
    test("唯一符号数 > 50", unique_syms > 50,
         f"unique={unique_syms}")

    # 被引用最多的符号
    top_syms = conn.execute("""
        SELECT dst, COUNT(*) as cnt FROM edge
        WHERE rel='mentions_symbol'
        GROUP BY dst ORDER BY cnt DESC LIMIT 5
    """).fetchall()
    test("Top 符号引用次数 >= 5", top_syms[0]["cnt"] >= 5,
         f"top={top_syms[0]['dst']} ({top_syms[0]['cnt']}次)")

    return [dict(r) for r in top_syms]


# ─── T6: 代码↔文档桥接（核心测试）────────────────────────────

def test_code_doc_bridge(conn):
    section("T6: 代码 ↔ 文档桥接（核心）")

    if not os.path.exists(CPPSG_DB):
        skip("代码↔文档桥接", f"cppsg DB 不存在: {CPPSG_DB}")
        return

    cppsg_conn = sqlite3.connect(CPPSG_DB)
    cppsg_conn.row_factory = sqlite3.Row

    # 检查 cppsg 有数据
    cppsg_nodes = cppsg_conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
    test("cppsg 代码图谱有数据", cppsg_nodes > 100,
         f"nodes={cppsg_nodes}")

    # 取 doc_graph 中的 top 20 符号
    doc_syms = conn.execute("""
        SELECT dst, COUNT(*) as cnt FROM edge
        WHERE rel='mentions_symbol'
        GROUP BY dst ORDER BY cnt DESC LIMIT 20
    """).fetchall()

    # 逐个验证在 cppsg 中是否存在
    found = 0
    not_found = []
    for s in doc_syms:
        sym_name = s["dst"].replace("symbol:", "")
        short_name = sym_name.split("::")[-1]

        # 精确匹配
        r = cppsg_conn.execute("""
            SELECT id, name, type, file_path FROM node
            WHERE name = ? AND type IN ('class', 'struct', 'function')
            LIMIT 1
        """, (short_name,)).fetchone()

        if r:
            found += 1
        else:
            # 模糊匹配
            r2 = cppsg_conn.execute("""
                SELECT id, name FROM node
                WHERE name LIKE ? AND type IN ('class', 'function')
                LIMIT 1
            """, (f"%{short_name}%",)).fetchone()
            if r2:
                found += 1
            else:
                not_found.append(sym_name)

    hit_rate = found / len(doc_syms) if doc_syms else 0
    test(f"Top 20 符号在 cppsg 命中率 > 50%", hit_rate > 0.5,
         f"found={found}/{len(doc_syms)} ({100*hit_rate:.0f}%) missing={not_found[:5]}")

    # 反向：取 cppsg 中的已知函数，验证 doc_graph 中有文档引用它
    known_funcs = ["doPartitionSwitch", "PerformUpgrade", "UpdateSinglePeripheral",
                   "CheckPartitionSwitchResult", "TryPrepare"]
    reverse_found = 0
    for fn in known_funcs:
        docs = conn.execute("""
            SELECT DISTINCT src FROM edge
            WHERE rel='mentions_symbol' AND dst LIKE ?
        """, (f"%{fn}%",)).fetchall()
        if docs:
            reverse_found += 1

    test(f"已知函数在文档中被引用 > 60%", reverse_found / len(known_funcs) > 0.6,
         f"found={reverse_found}/{len(known_funcs)}")

    # bridge_symbol_to_cppsg 函数测试
    test_sym = doc_syms[0]["dst"] if doc_syms else None
    if test_sym:
        bridge_result = bridge_symbol_to_cppsg(test_sym)
        test("bridge_symbol_to_cppsg 返回结果", len(bridge_result) > 0,
             f"symbol={test_sym} results={len(bridge_result)}")
        if bridge_result and "error" not in bridge_result[0]:
            first = bridge_result[0]
            test("bridge 结果有 name 字段", "name" in first,
                 f"keys={list(first.keys())}")
            test("bridge 结果有 type 字段", "type" in first,
                 f"type={first.get('type', 'MISSING')}")
            test("bridge 结果有 file_path", "file_path" in first and first["file_path"],
                 f"file_path={first.get('file_path', 'MISSING')}")

    cppsg_conn.close()


# ─── T7: 反向查找（代码→文档）─────────────────────────────────

def test_reverse_lookup(conn):
    section("T7: 反向查找（代码函数 → 引用文档）")

    # doPartitionSwitch 应该在分区切换相关文档中被引用
    test_cases = [
        ("doPartitionSwitch", ["PARTITION_SWITCH", "AB_Switch", "partition"]),
        ("PerformUpgrade", ["OTA", "Update", "upgrade"]),
        ("UpdateSinglePeripheral", ["Update", "peri", "upgrade"]),
        ("OtaManager", ["OTA", "manager", "state"]),
    ]

    for func_name, expected_keywords in test_cases:
        docs = conn.execute("""
            SELECT DISTINCT e.src, n.title, n.path
            FROM edge e
            JOIN node n ON e.src = n.id
            WHERE e.rel='mentions_symbol' AND e.dst LIKE ?
        """, (f"%{func_name}%",)).fetchall()

        test(f"'{func_name}' 被文档引用", len(docs) > 0,
             f"docs={len(docs)}")

        if docs:
            # 验证至少一个文档的标题/路径包含预期关键词
            matched = False
            for d in docs:
                text = (d["title"] + " " + d["path"]).lower()
                if any(kw.lower() in text for kw in expected_keywords):
                    matched = True
                    break
            test(f"  └ 文档语义相关", matched,
                 f"expected_keywords={expected_keywords}")


# ─── T8: 端到端搜索 ──────────────────────────────────────────

def test_e2e_search():
    section("T8: 端到端搜索")

    test_cases = [
        ("分区", 2, True),
        ("OTA", 2, True),
        ("GNSS", 1, False),
        ("OtaManager", 2, True),
    ]

    for kw, depth, bridge in test_cases:
        result = doc_graph_search(kw, depth=depth, bridge_to_code=bridge)

        test(f"搜 '{kw}' 有起始文档", len(result["start_docs"]) > 0,
             f"start_docs={len(result['start_docs'])}")

        total = len(result["docs"]) + len(result["knowledge"]) + len(result["symbols"])
        test(f"搜 '{kw}' 有关联结果", total > 0,
             f"docs={len(result['docs'])} knowledge={len(result['knowledge'])} symbols={len(result['symbols'])}")

        if bridge and result["symbols"]:
            test(f"搜 '{kw}' 有代码桥接结果", len(result.get("code_nodes", [])) > 0,
                 f"code_nodes={len(result.get('code_nodes', []))}")


# ─── T9: MCP 工具契约 ────────────────────────────────────────

def test_mcp_contract():
    section("T9: MCP 工具契约")

    # doc_graph_search 返回的字段
    result = doc_graph_search("分区", depth=1, bridge_to_code=False)
    required_keys = {"query", "start_docs", "docs", "knowledge", "symbols", "code_nodes"}
    test("doc_graph_search 返回必需字段", required_keys.issubset(set(result.keys())),
         f"keys={set(result.keys())}")

    # start_docs 每项有 doc_id
    if result["start_docs"]:
        sd = result["start_docs"][0]
        test("start_docs 项有 doc_id", "doc_id" in sd,
             f"keys={set(sd.keys())}")

    # symbols 每项有 name + confidence + hop
    if result["symbols"]:
        s = result["symbols"][0]
        test("symbols 项有 name/confidence/hop",
             all(k in s for k in ["name", "confidence", "hop"]),
             f"keys={set(s.keys())}")

    # get_doc_stats — 直接查 DB 验证 MCP 工具逻辑
    conn = get_conn(DOC_DB)
    node_total = conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
    edge_total = conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0]
    test("get_doc_stats 逻辑: nodes > 0", node_total > 0, f"nodes={node_total}")
    test("get_doc_stats 逻辑: edges > 0", edge_total > 0, f"edges={edge_total}")

    doc_types = conn.execute("""
        SELECT doc_type, COUNT(*) as cnt
        FROM node WHERE type='document' GROUP BY doc_type ORDER BY cnt DESC
    """).fetchall()
    test("get_doc_stats 逻辑: doc_type 分布", len(doc_types) > 3,
         f"types={len(doc_types)}")

    edge_types = conn.execute("""
        SELECT rel, COUNT(*) as cnt, SUM(manual) as manual_cnt
        FROM edge GROUP BY rel ORDER BY cnt DESC
    """).fetchall()
    test("get_doc_stats 逻辑: edge_type 分布", len(edge_types) == 3,
         f"types={len(edge_types)}")

    top_symbols = conn.execute("""
        SELECT dst, COUNT(*) as cnt FROM edge
        WHERE rel='mentions_symbol'
        GROUP BY dst ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    test("get_doc_stats 逻辑: top_symbols 有数据", len(top_symbols) > 0,
         f"count={len(top_symbols)}")

    # list_documents 逻辑验证
    all_docs = conn.execute(
        "SELECT id, title, doc_type, status, date, path FROM node WHERE type='document'"
    ).fetchall()
    test("list_documents 逻辑: 返回所有文档", len(all_docs) > 0,
         f"count={len(all_docs)}")

    design_docs = conn.execute(
        "SELECT id, title FROM node WHERE type='document' AND doc_type='design'"
    ).fetchall()
    test("list_documents 逻辑: 按类型过滤", len(design_docs) > 0,
         f"design_docs={len(design_docs)}")

    # 验证文档项字段
    if all_docs:
        d = dict(all_docs[0])
        test("document 项有 id/title/doc_type",
             all(k in d for k in ["id", "title", "doc_type"]),
             f"keys={set(d.keys())}")

    conn.close()


# ─── 主函数 ──────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  文档知识图谱 × 代码语义图谱 — 融合集成测试")
    print("=" * 60)

    conn = get_conn(DOC_DB)

    t0 = time.time()
    test_db_integrity(conn)
    test_frontmatter(conn)
    test_fts5(conn)
    test_bfs(conn)
    test_symbol_extraction(conn)
    test_code_doc_bridge(conn)
    test_reverse_lookup(conn)
    test_e2e_search()
    test_mcp_contract()
    elapsed = time.time() - t0

    conn.close()

    # 打印结果
    for line in RESULTS:
        print(line)

    print(f"\n{'='*60}")
    print(f"  结果: ✅ {PASS} 通过 | ❌ {FAIL} 失败 | ⏭️  {SKIP} 跳过")
    print(f"  耗时: {elapsed:.2f}s")
    print(f"{'='*60}")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
