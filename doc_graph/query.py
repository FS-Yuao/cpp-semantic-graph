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
  python3 query.py "TryActivate" --bridge
  python3 query.py "分区" --depth 3 --json
"""

from __future__ import annotations
import os, sys, json, sqlite3, argparse, re
from collections import deque

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(SCRIPT_DIR, "doc_graph.db")
# cppsg 代码图谱：默认在上级目录（cpp_semantic_graph/semantic_graph_full.db）
CPPSG_DB = os.environ.get("CPP_GRAPH_DB",
                          os.path.join(SCRIPT_DIR, "..", "semantic_graph_full.db"))


def get_conn(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ─── 1. FTS5 起始节点定位 ─────────────────────────────────────

def fts5_search(conn: sqlite3.Connection, keyword: str, limit: int = 5) -> list[dict]:
    """FTS5 全文搜索 → 起始文档列表。未命中时降级为 LIKE 搜索。"""
    try:
        tokens = re.findall(r'[A-Za-z]+|\d+|[\u4e00-\u9fff]+', keyword)
        # 空串或纯标点（提取不到有效 token）直接返回空，避免 FTS5 语法错 + LIKE %% 全匹配
        if not tokens:
            return []
        match_parts = []
        for t in tokens:
            if re.match(r'^[\u4e00-\u9fff]+$', t):
                match_parts.extend(list(t))
            else:
                match_parts.append(t)
        match_expr = ' OR '.join(match_parts)

        rows = conn.execute("""
            SELECT doc_id, bm25(doc_fts) AS score
            FROM doc_fts WHERE doc_fts MATCH ?
            ORDER BY score LIMIT ?
        """, (match_expr, limit)).fetchall()

        if rows:
            return [{"doc_id": r["doc_id"], "score": r["score"], "source": "fts5"} for r in rows]
    except Exception as e:
        # 记录错误但不中断，降级到 LIKE 搜索
        print(f"[doc_graph] FTS5 搜索降级到 LIKE: {e}", file=sys.stderr)

    # 降级：LIKE 搜索标题/路径/标签/摘要
    rows = conn.execute("""
        SELECT id AS doc_id, 0 AS score
        FROM node WHERE type = 'document'
          AND (title LIKE ? OR path LIKE ? OR tags LIKE ? OR summary LIKE ?)
        LIMIT ?
    """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit)).fetchall()

    if rows:
        return [{"doc_id": r["doc_id"], "score": 0, "source": "like"} for r in rows]

    # 再降级：符号反查（搜索关键词匹配 symbol 名 → 找到提到该符号的文档）
    rows = conn.execute("""
        SELECT DISTINCT e.src AS doc_id, 0 AS score
        FROM edge e
        WHERE e.rel = 'mentions_symbol' AND e.dst LIKE ?
        LIMIT ?
    """, (f"%{keyword}%", limit)).fetchall()

    return [{"doc_id": r["doc_id"], "score": 0, "source": "symbol"} for r in rows]


# ─── 2. BFS 图遍历 ────────────────────────────────────────────

def bfs_traverse(conn: sqlite3.Connection, start_id: str,
                 depth: int = 2, edge_filter: list[str] | None = None) -> dict:
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

        # Level 1: 精确匹配
        rows = conn.execute("""
            SELECT id, type, name, namespace, file_path, parent_class
            FROM node WHERE name = ? AND type IN ('class', 'struct', 'function') LIMIT 5
        """, (short_name,)).fetchall()

        # Level 2: ClassName::MethodName 拆分
        if not rows and "::" in symbol_name:
            cls_name, method_name = symbol_name.rsplit("::", 1)
            rows = conn.execute("""
                SELECT id, type, name, namespace, file_path, parent_class
                FROM node WHERE name = ? AND parent_class = ? AND type = 'function' LIMIT 5
            """, (method_name, cls_name)).fetchall()

        # Level 3: 模糊匹配
        if not rows:
            rows = conn.execute("""
                SELECT id, type, name, namespace, file_path, parent_class
                FROM node WHERE name LIKE ? AND type IN ('class', 'function') LIMIT 5
            """, (f"%{short_name}%",)).fetchall()

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

def doc_graph_search(keyword: str, depth: int = 2,
                     edge_filter: list[str] | None = None,
                     bridge_to_code: bool = True,
                     db_path: str = DEFAULT_DB) -> dict:
    """搜索文档图谱，返回结构化关联结果"""
    conn = get_conn(db_path)
    try:
        # Step 1: FTS5 定位
        start_docs = fts5_search(conn, keyword, limit=3)

        if not start_docs:
            return {"query": keyword, "start_docs": [], "docs": [], "knowledge": [],
                    "symbols": [], "code_nodes": [],
                    "message": f"未找到匹配 '{keyword}' 的文档"}

        # Step 2: BFS 遍历
        all_results = {"docs": [], "knowledge": [], "symbols": []}
        for start in start_docs:
            result = bfs_traverse(conn, start["doc_id"], depth, edge_filter)
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

        # Step 3: 符号桥接
        code_nodes = []
        if bridge_to_code:
            for sym in all_results["symbols"][:10]:
                cppsg_nodes = bridge_symbol_to_cppsg(sym["name"])
                if cppsg_nodes:
                    code_nodes.append({"symbol": sym["name"],
                                       "confidence": sym["confidence"], "cppsg": cppsg_nodes})

        return {"query": keyword, "start_docs": start_docs,
                **all_results, "code_nodes": code_nodes}
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


def main():
    parser = argparse.ArgumentParser(description='文档知识图谱查询')
    parser.add_argument('keyword', help='搜索关键词')
    parser.add_argument('--depth', type=int, default=2, help='BFS 遍历深度')
    parser.add_argument('--bridge', action='store_true', help='桥接到 cppsg')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    parser.add_argument('--db', default=DEFAULT_DB, help='数据库路径')
    parser.add_argument('--edge-filter', nargs='+',
                        choices=['has_knowledge', 'relates_to', 'mentions_symbol'])
    args = parser.parse_args()

    result = doc_graph_search(args.keyword, depth=args.depth,
                              edge_filter=args.edge_filter,
                              bridge_to_code=args.bridge, db_path=args.db)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print_result(result)


if __name__ == '__main__':
    main()
