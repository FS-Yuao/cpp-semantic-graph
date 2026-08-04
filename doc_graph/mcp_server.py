#!/usr/bin/env python3
"""
文档知识图谱 MCP Server

暴露 doc_graph_search 工具，让 Agent 通过 MCP 查询文档图谱。

注册到 CodeBuddy (~/.codebuddy/mcp.json):
  {
    "mcpServers": {
      "doc-graph": {
        "command": "python3",
        "args": ["/path/to/doc_graph/mcp_server.py"],
        "env": {
          "DOC_GRAPH_DB": "/path/to/doc_graph.db",
          "CPP_GRAPH_DB": "/path/to/semantic_graph_full.db"
        }
      }
    }
  }
"""

from __future__ import annotations
import os, sys, json

# 确保 query.py 可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from query import doc_graph_search

# ─── 配置 ─────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(SCRIPT_DIR, "doc_graph.db")

mcp = FastMCP("doc-graph")


# ─── 工具定义 ─────────────────────────────────────────────────

@mcp.tool()
def doc_graph_search_tool(
    keyword: str,
    depth: int = 2,
    bridge_to_code: bool = True,
    edge_filter: list[str] | None = None,
) -> str:
    """搜索文档知识图谱，返回文档、知识点、代码符号和关联的代码节点。

    当用户问关于文档关联、任务历史、决策追溯、设计方案等问题时使用此工具。
    当用户需要同时查文档和代码时，设置 bridge_to_code=true。

    Args:
        keyword: 搜索关键词（中文或英文，如"分区"、"GNSS"、"UpdateManager"）
        depth: BFS 遍历深度，默认 2（1=直接关联，2=关联的关联）
        bridge_to_code: 是否桥接到 cppsg 代码图谱，默认 true
        edge_filter: 限定边类型，可选值: has_knowledge, relates_to, mentions_symbol

    Returns:
        JSON 格式的搜索结果，包含:
        - start_docs: FTS5/LIKE 命中的起始文档
        - docs: BFS 遍历到的关联文档
        - knowledge: BFS 遍历到的知识点
        - symbols: BFS 遍历到的代码符号
        - code_nodes: 符号桥接到 cppsg 的代码节点（含 callers/callees）

    示例:
        # 搜索"分区"相关文档和代码
        doc_graph_search_tool("分区", depth=2, bridge_to_code=true)

        # 只搜文档间关联，不查代码
        doc_graph_search_tool("GNSS", depth=1, bridge_to_code=false)
    """
    db_path = os.environ.get("DOC_GRAPH_DB", DEFAULT_DB)
    result = doc_graph_search(
        keyword=keyword,
        depth=depth,
        edge_filter=edge_filter,
        bridge_to_code=bridge_to_code,
        db_path=db_path,
    )
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def list_documents(doc_type: str = "", status: str = "") -> str:
    """列出文档图谱中的所有文档，可按类型和状态过滤。

    Args:
        doc_type: 文档类型过滤，可选: task, diary, review, design, link, requirement, report
        status: 状态过滤，如: 已完成, 待评审, 通过

    Returns:
        JSON 格式的文档列表，含 doc_id, title, summary, doc_type, status, date, path
    """
    import sqlite3
    db_path = os.environ.get("DOC_GRAPH_DB", DEFAULT_DB)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    query = "SELECT id, title, summary, doc_type, status, date, path FROM node WHERE type = 'document'"
    params = []
    if doc_type:
        query += " AND doc_type = ?"
        params.append(doc_type)
    if status:
        query += " AND status LIKE ?"
        params.append(f"%{status}%")
    query += " ORDER BY date DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    docs = [dict(r) for r in rows]
    return json.dumps({
        "total": len(docs),
        "documents": docs,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_doc_stats() -> str:
    """获取文档知识图谱的统计信息。

    Returns:
        JSON 格式的统计信息，含节点数、边数、文档类型分布、边类型分布等。
    """
    import sqlite3
    db_path = os.environ.get("DOC_GRAPH_DB", DEFAULT_DB)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    node_total = conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
    edge_total = conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0]

    doc_types = conn.execute("""
        SELECT doc_type, COUNT(*) as cnt
        FROM node WHERE type = 'document' GROUP BY doc_type ORDER BY cnt DESC
    """).fetchall()

    edge_types = conn.execute("""
        SELECT rel, COUNT(*) as cnt, SUM(manual) as manual_cnt
        FROM edge GROUP BY rel ORDER BY cnt DESC
    """).fetchall()

    legacy = conn.execute("""
        SELECT COUNT(*) FROM node WHERE type = 'document' AND legacy = 1
    """).fetchone()[0]

    isolated = conn.execute("""
        SELECT COUNT(*) FROM node n
        WHERE n.type = 'document'
          AND n.id NOT IN (SELECT src FROM edge WHERE src LIKE 'doc:%')
          AND n.id NOT IN (SELECT dst FROM edge WHERE dst LIKE 'doc:%')
    """).fetchone()[0]

    top_symbols = conn.execute("""
        SELECT dst, COUNT(*) as cnt
        FROM edge WHERE rel = 'mentions_symbol'
        GROUP BY dst ORDER BY cnt DESC LIMIT 10
    """).fetchall()

    has_fm = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='document' AND manual=1"
    ).fetchone()[0]

    # summary 覆盖率
    doc_with_summary = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='document' AND summary IS NOT NULL AND summary != ''"
    ).fetchone()[0]
    know_with_summary = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='knowledge' AND summary IS NOT NULL AND summary != ''"
    ).fetchone()[0]
    know_total = conn.execute(
        "SELECT COUNT(*) FROM node WHERE type='knowledge'"
    ).fetchone()[0]

    conn.close()

    return json.dumps({
        "nodes": node_total,
        "edges": edge_total,
        "documents_with_frontmatter": has_fm,
        "document_summary_coverage": f"{doc_with_summary}/{node_total - edge_total + len(doc_types)}",
        "document_summary_pct": round(100.0 * doc_with_summary / max(1, has_fm), 1),
        "knowledge_summary_coverage": f"{know_with_summary}/{know_total}",
        "knowledge_summary_pct": round(100.0 * know_with_summary / max(1, know_total), 1),
        "legacy_documents": legacy,
        "isolated_documents": isolated,
        "doc_type_distribution": [dict(r) for r in doc_types],
        "edge_type_distribution": [dict(r) for r in edge_types],
        "top_symbols": [dict(r) for r in top_symbols],
    }, ensure_ascii=False, indent=2)


# ─── 主函数 ───────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
