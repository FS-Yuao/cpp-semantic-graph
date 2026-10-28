#!/usr/bin/env python3
"""
文档知识图谱查询层

功能：
  1. FTS5 全文搜索 → 定位起始文档
  2. BFS 图遍历 → 收集关联文档/知识点/符号
  3. 符号桥接 → 查 cppsg 代码节点 + callers/callees
  4. 组合搜索 → 一次调用返回结构化结果

Usage:
  python3 query.py "AB分区方案"
  python3 query.py "TryStart" --bridge
  python3 query.py "分区" --depth 3 --json
"""

from __future__ import annotations
import os, sys, json, sqlite3, argparse, re
from collections import deque

from finding_store import search_findings, get_findings_by_symbols, _lifecycle_boost

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(SCRIPT_DIR, "doc_graph.db")
# cppsg 代码图谱：默认在上级目录（cpp_semantic_graph/semantic_graph_full.db）
CPPSG_DB = os.environ.get("CPP_GRAPH_DB",
                          os.path.join(SCRIPT_DIR, "..", "semantic_graph_full.db"))


def get_conn(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ─── 1. 起始节点定位（三路并行 + RRF 融合）─────────────────────

def _rrf_fuse(rankings: list[list[str]], sources: list[str],
              k: int = 60, limit: int = 5,
              full_backstop: tuple[str, ...] = ("fts5",)) -> list[dict]:
    """多路排序结果 RRF 融合：score(d) = Σ 1/(k + rank_i(d))，k=60。

    两路同时命中的文档得分高于任一单路，天然实现"多证据优先"。
    保底规则（回归护栏 A6）：
      - full_backstop 中的路（默认 fts5）：top-limit 全部保底入选——
        旧降级链在 FTS5 命中时起点集就是 FTS5 top-limit，全保才能保证
        融合起始集是旧行为的超集，BFS 召回单调不减；
      - 其他路：rank-1 保底（旧链中它们只在 FTS5 miss 时作为起点）。
    """
    scores: dict[str, float] = {}
    hit_sources: dict[str, list[str]] = {}
    backstop_ids: list[str] = []
    for ranking, src in zip(rankings, sources):
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            hit_sources.setdefault(doc_id, []).append(src)
        if src in full_backstop:
            backstop_ids.extend(ranking[:limit])
        elif ranking:
            backstop_ids.append(ranking[0])

    fused_ids = [d for d, _ in sorted(scores.items(), key=lambda x: -x[1])]
    # 保底：RRF top-limit 之后，按优先级追加未入选的保底文档
    selected = fused_ids[:limit]
    for bid in backstop_ids:
        if bid not in selected:
            selected.append(bid)

    return [{"doc_id": d, "score": round(scores.get(d, 0.0), 6),
             "source": "+".join(hit_sources.get(d, ["leader"]))}
            for d in selected]


def fts5_search(conn: sqlite3.Connection, keyword: str, limit: int = 5,
                include_history: bool = False) -> list[dict]:
    """三路并行检索起始文档 + RRF 融合排序。

    路 1 FTS5 全文（title/summary/path/tags）
    路 2 LIKE 子串（标题/摘要兜底，FTS5 分词 miss 时仍可命中）
    路 3 符号反查（关键词命中 symbol 名 → 提到该符号的文档）

    原为降级链（FTS5 命中即短路），现改为三路全跑 + 融合：
    排序只会因多路证据而更优，且单路 miss 不再丢结果。
    """
    tokens = re.findall(r'[A-Za-z]+|\d+|[\u4e00-\u9fff]+', keyword)
    if not tokens:
        return []
    match_parts = []
    for t in tokens:
        if re.match(r'^[\u4e00-\u9fff]+$', t):
            match_parts.extend(list(t))
        else:
            match_parts.append(t)
    match_expr = ' OR '.join(match_parts)

    visible = "" if include_history else " AND COALESCE(status,'') NOT IN ('archived','revoked','superseded','deprecated')"
    per_route = limit * 3  # 每路多取，融合后截断
    rankings: list[list[str]] = []
    route_names: list[str] = []

    # 路 1: FTS5
    try:
        rows = conn.execute(f"""
            SELECT doc_id, bm25(doc_fts) AS score
            FROM doc_fts WHERE doc_fts MATCH ?
            AND doc_id IN (SELECT id FROM node WHERE type='document' {visible})
            ORDER BY score LIMIT ?
        """, (match_expr, per_route)).fetchall()
        if rows:
            rankings.append([r["doc_id"] for r in rows])
            route_names.append("fts5")
    except Exception as e:
        print(f"[doc_graph] FTS5 路异常（跳过，不影响其他路）: {e}", file=sys.stderr)

    # 路 2: LIKE（标题/摘要/路径/标签子串）
    rows = conn.execute(f"""
        SELECT id AS doc_id
        FROM node WHERE type = 'document' {visible}
          AND (title LIKE ? OR summary LIKE ? OR path LIKE ? OR tags LIKE ?)
        LIMIT ?
    """, (f"%{keyword}%",) * 4 + (per_route,)).fetchall()
    if rows:
        rankings.append([r["doc_id"] for r in rows])
        route_names.append("like")

    # 路 3: 符号反查
    rows = conn.execute(f"""
        SELECT DISTINCT e.src AS doc_id
        FROM edge e
        WHERE e.rel = 'mentions_symbol' AND e.dst LIKE ?
        AND e.src IN (SELECT id FROM node WHERE type='document' {visible})
        LIMIT ?
    """, (f"%{keyword}%", per_route)).fetchall()
    if rows:
        rankings.append([r["doc_id"] for r in rows])
        route_names.append("symbol")

    if not rankings:
        return []
    return _rrf_fuse(rankings, route_names, limit=limit)


# ─── 2. BFS 图遍历 ────────────────────────────────────────────

def bfs_traverse(conn: sqlite3.Connection, start_id: str,
                 depth: int = 2, edge_filter: list[str] | None = None,
                 include_history: bool = False) -> dict:
    """从节点出发 BFS 遍历，返回关联的文档/知识点/符号"""
    visited = set()
    results = {"docs": [], "knowledge": [], "symbols": []}
    queue = deque([(start_id, 0)])
    allowed_rels = edge_filter or ["has_knowledge", "relates_to", "mentions_symbol"]

    while queue:
        node_id, hop = queue.popleft()
        if node_id in visited or hop > depth:
            continue
        visited.add(node_id)

        row = conn.execute("SELECT * FROM node WHERE id = ?", (node_id,)).fetchone()
        if not row:
            continue

        node = dict(row)
        if not include_history and node.get("status") in {"archived", "revoked", "superseded", "deprecated"}:
            continue
        tags = node.get("tags", "[]")
        try:
            node["tags"] = json.loads(tags) if isinstance(tags, str) else tags
        except (json.JSONDecodeError, TypeError):
            node["tags"] = []

        if node["type"] == "document":
            results["docs"].append({**node, "hop": hop})
        elif node["type"] == "knowledge":
            parent = conn.execute(
                "SELECT src FROM edge WHERE dst = ? AND rel = 'has_knowledge' LIMIT 1",
                (node_id,)).fetchone()
            results["knowledge"].append({**node, "hop": hop,
                                         "parent_doc": parent["src"] if parent else None})

        edges = conn.execute(
            "SELECT dst, rel, manual FROM edge WHERE src = ? AND rel IN ({})"
            .format(",".join("?" * len(allowed_rels))),
            [node_id] + allowed_rels
        ).fetchall()

        for edge in edges:
            if edge["rel"] == "mentions_symbol":
                results["symbols"].append({
                    "name": edge["dst"],
                    "source": node_id,
                    "confidence": "manual" if edge["manual"] else "auto",
                    "hop": hop,
                })
            else:
                queue.append((edge["dst"], hop + 1))

    return results


# ─── 3. 符号桥接（→ cppsg）────────────────────────────────────

def bridge_symbol_to_cppsg(symbol_name: str, cppsg_db_path: str = CPPSG_DB) -> list[dict]:
    """文档图谱符号名 → cppsg 代码节点查找（三级降级）"""
    if not os.path.exists(cppsg_db_path) or os.path.getsize(cppsg_db_path) == 0:
        return [{"error": "cppsg 数据库为空或不存在，请先构建代码图谱"}]

    if symbol_name.startswith("symbol:"):
        symbol_name = symbol_name[7:]

    try:
        conn = sqlite3.connect(cppsg_db_path)
        conn.row_factory = sqlite3.Row
    except Exception:
        return [{"error": "无法连接 cppsg 数据库"}]

    try:
        short_name = symbol_name.split("::")[-1]

        if "::" in symbol_name:
            owner, method = symbol_name.rsplit("::", 1)
            rows = conn.execute("""SELECT id,type,name,namespace,file_path,parent_class FROM node
                WHERE name=? AND (parent_class=? OR namespace=? OR namespace || '::' || parent_class=?)
                AND type IN ('class','struct','function') LIMIT 5""",
                (method, owner, owner, owner)).fetchall()
        else:
            rows = conn.execute("""SELECT id,type,name,namespace,file_path,parent_class FROM node
                WHERE name=? AND type IN ('class','struct','function') LIMIT 5""", (short_name,)).fetchall()
            if not rows:
                rows = conn.execute("""SELECT id,type,name,namespace,file_path,parent_class FROM node
                    WHERE name LIKE ? AND type IN ('class','function') LIMIT 5""", (f"%{short_name}%",)).fetchall()

        if not rows:
            return []

        results = []
        for row in rows:
            node = dict(row)
            callers = conn.execute("""
                SELECT n.name, n.type, n.parent_class
                FROM edge e JOIN node n ON e.from_id = n.id
                WHERE e.to_id = ? AND e.relation_type LIKE 'calls%'
            """, (node["id"],)).fetchall()
            node["callers"] = [dict(r) for r in callers]

            callees = conn.execute("""
                SELECT n.name, n.type, n.parent_class
                FROM edge e JOIN node n ON e.to_id = n.id
                WHERE e.from_id = ? AND e.relation_type LIKE 'calls%'
            """, (node["id"],)).fetchall()
            node["callees"] = [dict(r) for r in callees]
            results.append(node)

        return results
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        conn.close()


# ─── 4. 组合搜索 ──────────────────────────────────────────────

def _compact_search_result(result: dict, symbol_cap: int = 30,
                           knowledge_cap: int = 20, doc_cap: int = 20,
                           finding_cap: int = 15) -> dict:
    """渐进披露裁剪（2026-10-23 O-1）：默认返回目录级，正文按需展开。

    对标渐进披露模式（先目录后正文）：文档只回 title/path/一句话摘要（100 字符
    截断），知识点只回标题索引（不带 summary），符号折叠为纯名字列表（manual
    优先排序保持），findings 精简为 id/title/状态/置信度，cppsg 桥接不展开。
    背景：原全量返回单次查询可超 100+ 符号对象与 30+ 知识点全文摘要，
    有效信息占比低（2026-10-22 冒烟实证）。
    2026-10-23 评审修复（P1-5）：docs/findings 加硬上限——符号密集查询经
    findings 符号反查可带入 ~90 条（全库 119），目录级若不封顶可比旧全量还大。
    """
    compact = {
        "query": result["query"],
        "mode": "compact",
        "hint": ("目录级结果。判断相关后带 include_detail=true 重查同一关键词，"
                 "可获文档/知识点全文摘要、符号详情与 cppsg 桥接节点。"),
        "start_docs": result["start_docs"],
        "docs": [{
            "id": d["id"], "title": d.get("title", ""), "path": d.get("path", ""),
            "doc_type": d.get("doc_type", ""), "status": d.get("status", ""),
            "date": d.get("date", ""), "hop": d.get("hop", 0),
            "summary": (d.get("summary") or "")[:100],
        } for d in result.get("docs", [])[:doc_cap]],
        "docs_total": len(result.get("docs", [])),
        "knowledge": [{
            "id": k["id"], "title": k.get("title", ""), "path": k.get("path", ""),
            "line": k.get("line", 0), "ktype": k.get("ktype", ""),
            "hop": k.get("hop", 0), "parent_doc": k.get("parent_doc"),
        } for k in result.get("knowledge", [])[:knowledge_cap]],
        "knowledge_total": len(result.get("knowledge", [])),
        "symbols": [s["name"] for s in result.get("symbols", [])[:symbol_cap]],
        "symbols_total": len(result.get("symbols", [])),
        "code_nodes": [],  # 渐进模式不展开桥接，include_detail=true 时返回
        "findings": [{
            "id": f["id"], "title": f.get("title", ""), "ftype": f.get("ftype", ""),
            "status": f.get("status", ""), "confidence": f.get("confidence", ""),
            "updated_at": f.get("updated_at", ""),
        } for f in result.get("findings", [])[:finding_cap]],
        "findings_total": len(result.get("findings", [])),
    }
    if result.get("message"):
        compact["message"] = result["message"]
    return compact


def doc_graph_search(keyword: str, depth: int = 2,
                     edge_filter: list[str] | None = None,
                     bridge_to_code: bool = True,
                     include_detail: bool = False,
                     db_path: str = DEFAULT_DB, include_history: bool = False) -> dict:
    """搜索文档图谱，返回结构化关联结果（含经验结论 findings）。

    include_detail=False（默认）返回目录级（渐进披露第一步，见 _compact_search_result）；
    True 返回全量（第二步）。"""
    conn = get_conn(db_path)
    try:
        # Step 1: 三路融合定位起始文档
        start_docs = fts5_search(conn, keyword, limit=3, include_history=include_history)

        # Step 1.5: 经验结论——关键词直查（即使无文档命中也返回）
        findings: list[dict] = []
        seen_fids: set[str] = set()
        for f in search_findings(keyword=keyword, limit=5, db_path=db_path):
            seen_fids.add(f["id"])
            findings.append(f)

        if not start_docs:
            result = {"query": keyword, "start_docs": [], "docs": [], "knowledge": [],
                      "symbols": [], "code_nodes": [], "findings": findings,
                      "message": (f"未找到匹配 '{keyword}' 的文档"
                                  + (f"，但有 {len(findings)} 条相关经验结论" if findings else ""))}
            return result if include_detail else _compact_search_result(result)

        # Step 2: BFS 遍历
        all_results = {"docs": [], "knowledge": [], "symbols": []}
        for start in start_docs:
            result = bfs_traverse(conn, start["doc_id"], depth, edge_filter, include_history)
            for key in all_results:
                all_results[key].extend(result[key])

        # 去重（docs 和 knowledge 先去重，顺序无关）
        for key, id_field in [("docs", "id"), ("knowledge", "id")]:
            seen = set()
            deduped = []
            for item in all_results[key]:
                k = item[id_field]
                if k not in seen:
                    seen.add(k)
                    deduped.append(item)
            all_results[key] = deduped

        # 排序：manual 优先（必须在去重前排序，保证 manual 条目优先保留）
        all_results["symbols"].sort(
            key=lambda x: (0 if x["confidence"] == "manual" else 1, x["hop"]))

        # 去重（排序后，manual 条目排在前面，优先保留）
        seen_syms = set()
        deduped_syms = []
        for item in all_results["symbols"]:
            k = item["name"]
            if k not in seen_syms:
                seen_syms.add(k)
                deduped_syms.append(item)
        all_results["symbols"] = deduped_syms

        # Step 3: 符号桥接（仅 detail 模式——渐进模式 code_nodes 不展开，
        # 省去桥接查询开销；锚定符号反查 findings 仍走 BFS 符号）
        code_nodes = []
        if bridge_to_code and include_detail:
            for sym in all_results["symbols"][:10]:
                cppsg_nodes = bridge_symbol_to_cppsg(sym["name"])
                if cppsg_nodes:
                    code_nodes.append({"symbol": sym["name"],
                                       "confidence": sym["confidence"], "cppsg": cppsg_nodes})

        # Step 4: 经验结论——BFS 符号交集 + cppsg 命中符号反查
        anchor_names = [s["name"] for s in all_results["symbols"][:30]]
        for cn in code_nodes:
            for node in cn.get("cppsg", []):
                if isinstance(node, dict) and node.get("name"):
                    anchor_names.append(node["name"])
        for f in get_findings_by_symbols(anchor_names, db_path=db_path):
            if f["id"] not in seen_fids:
                seen_fids.add(f["id"])
                findings.append(f)
        # 终排（2026-10-23 评审修复 P1-4）：统一走生命周期衰减排序（证据加成+
        # 新鲜度），最新优先——原实现按 updated_at 升序把最新结论排在最后，
        # 且丢弃了子查询内部的 evidence/衰减排序，与 HLD"衰减排序全量生效"宣称不符
        # Direct keyword findings keep their relevance order; symbol neighbors follow.

        result = {"query": keyword, "start_docs": start_docs,
                  **all_results, "code_nodes": code_nodes, "findings": findings}
        return result if include_detail else _compact_search_result(result)
    finally:
        conn.close()


# ─── CLI ──────────────────────────────────────────────────────

def print_result(result: dict):
    print(f"\n{'='*60}")
    print(f"🔍 搜索: \"{result['query']}\"")
    print(f"{'='*60}")

    if result.get("message"):
        print(f"  {result['message']}")
        return

    print(f"\n📌 起始文档 ({len(result['start_docs'])} 个):")
    for d in result["start_docs"]:
        print(f"  {d['doc_id']:50s}  [{d['source']}] score={d.get('score',0):.2f}")

    print(f"\n📄 关联文档 ({len(result['docs'])} 个):")
    for d in result["docs"]:
        hop = d.get("hop", 0)
        indent = "  " * (hop + 1)
        print(f"  {indent}hop{hop}: [{d.get('doc_type',''):6s}] {d.get('title','')[:50]}")
        if d.get("summary"):
            print(f"  {indent}       摘要: {d['summary'][:80]}")
        if d.get("status"):
            print(f"  {indent}       状态: {d['status']}")

    print(f"\n💡 知识点 ({len(result['knowledge'])} 个):")
    for k in result["knowledge"][:20]:
        hop = k.get("hop", 0)
        indent = "  " * (hop + 1)
        print(f"  {indent}{k.get('title','')[:60]}")
        if k.get("summary"):
            print(f"  {indent}    摘要: {k['summary'][:70]}")
        if k.get("ktype"):
            print(f"  {indent}    类型: {k['ktype']}")
        if k.get("conclusion"):
            print(f"  {indent}    结论: {k['conclusion'][:60]}")

    print(f"\n🔗 代码符号 ({len(result['symbols'])} 个):")
    for s in result["symbols"][:20]:
        conf = "🔒" if s["confidence"] == "manual" else "🔓"
        print(f"  {conf} {s['name']:45s}  hop{s['hop']}")

    if result.get("code_nodes"):
        print(f"\n⚙️  cppsg 代码节点 ({len(result['code_nodes'])} 组):")
        for cn in result["code_nodes"]:
            print(f"  符号: {cn['symbol']}")
            for node in cn["cppsg"]:
                if "error" in node:
                    print(f"    ❌ {node['error']}")
                    continue
                print(f"    {node.get('type',''):8s} {node.get('name',''):30s}  "
                      f"file: {node.get('file_path','')}")
                if node.get("callers"):
                    print(f"      调用方: {', '.join(c['name'] for c in node['callers'][:5])}")
                if node.get("callees"):
                    print(f"      被调用: {', '.join(c['name'] for c in node['callees'][:5])}")

    if result.get("findings"):
        print(f"\n🧠 经验结论 ({len(result['findings'])} 条):")
        for f in result['findings'][:10]:
            mark = " ⚠️stale" if f.get("status") == "stale" else ""
            conf = "" if f.get("confidence") == "confirmed" else " (suspected)"
            print(f"  [{f.get('ftype',''):10s}] {f.get('title','')[:60]}{mark}{conf}")
            if f.get("symbols"):
                print(f"      锚定符号: {', '.join(f['symbols'][:5])}")


def main():
    parser = argparse.ArgumentParser(description='文档知识图谱查询')
    parser.add_argument('keyword', help='搜索关键词')
    parser.add_argument('--depth', type=int, default=2, help='BFS 遍历深度')
    parser.add_argument('--bridge', action='store_true', help='桥接到 cppsg')
    parser.add_argument('--detail', action='store_true',
                        help='全量模式（默认渐进披露：目录级返回）')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    parser.add_argument('--db', default=DEFAULT_DB, help='数据库路径')
    parser.add_argument('--edge-filter', nargs='+',
                        choices=['has_knowledge', 'relates_to', 'mentions_symbol'])
    args = parser.parse_args()

    result = doc_graph_search(args.keyword, depth=args.depth,
                              edge_filter=args.edge_filter,
                              bridge_to_code=args.bridge, include_detail=args.detail,
                              db_path=args.db)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print_result(result)


if __name__ == '__main__':
    main()
