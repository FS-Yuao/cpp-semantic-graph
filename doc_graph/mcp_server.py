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
from finding_store import (record_finding, search_findings, check_freshness,
                           backfill_embeddings, FTYPES)

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
    try:
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
        docs = [dict(r) for r in rows]
        return json.dumps({
            "total": len(docs),
            "documents": docs,
        }, ensure_ascii=False, indent=2)
    finally:
        conn.close()


@mcp.tool()
def record_finding_tool(
    title: str,
    detail: str = "",
    ftype: str = "fact",
    symbols: list[str] | None = None,
    source: str = "",
    tags: list[str] | None = None,
    confidence: str = "confirmed",
) -> str:
    """沉淀一条经验结论到文档图谱（会话产出可复用结论时调用，防止下个会话重新推导）。

    什么时候该调用：分析/调试/评审得出【可复用结论】时——
    - 约束：如"QueryBootChain 不能用 SetError（IDL 未声明 application error）"
    - 教训：如"改数据模型后过时断言会误报，要同步校正"
    - 决策：如"重载区分签名选更通用方案"
    - 事实/风险：影响后续决策的代码事实或潜在风险

    不要调用：只与当前任务相关的临时信息、未验证的猜测（除非标 suspected）。

    Args:
        title: 一句话结论（必填，检索的主要命中面，写清楚主语+断言）
        detail: 展开：证据、出处、推理链（可选）
        ftype: 结论类型: fact(事实) | constraint(约束) | decision(决策) | lesson(教训) | risk(风险)
        symbols: 锚定的代码符号名列表（如 ["QueryBootChain", "SessionManager::TryStart"]，
                 符号被查询时会自动带出本条结论，强烈建议填写）
        source: 来源（会话日期/任务名/文档路径）
        tags: 自由标签
        confidence: confirmed(已验证) | suspected(推测未证实)

    Returns:
        JSON：{"action": "created"|"updated", "id": ..., "symbols_anchored": n}
        同 title 重复调用为幂等更新，不会产生重复记录。
    """
    db_path = os.environ.get("DOC_GRAPH_DB", DEFAULT_DB)
    result = record_finding(title=title, detail=detail, ftype=ftype,
                            symbols=symbols, source=source, tags=tags,
                            confidence=confidence, db_path=db_path)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def search_findings_tool(
    keyword: str = "",
    symbol: str = "",
    ftype: str = "",
    limit: int = 10,
) -> str:
    """检索已沉淀的经验结论（上次关于 X 的结论/教训/约束是什么）。

    与 doc_graph_search_tool 的区别：本工具只查经验结论（finding）表，不遍历文档图，
    适合"之前得出过什么结论"的直查。doc_graph_search_tool 的结果里也会附带 findings。

    Args:
        keyword: 关键词（匹配标题/详情，中英文均可）。**建议附带同义词提升召回**：
                 口语化查询请自行改写为关键词组合，如"程序挂了"传"崩溃 crash 挂 掉"，
                 "刷不了"传"刷写 flash 失败 fail"。服务端也有常用技术词中英对照表自动扩展。
        symbol: 代码符号名（返回锚定到该符号的所有结论）
        ftype: 类型过滤: fact | constraint | decision | lesson | risk
        limit: 最多返回条数，默认 10

    Returns:
        JSON 格式的结论列表，每条含 id/ftype/title/detail/symbols/status/confidence/source
    """
    db_path = os.environ.get("DOC_GRAPH_DB", DEFAULT_DB)
    result = search_findings(keyword=keyword, symbol=symbol, ftype=ftype,
                             limit=limit, db_path=db_path)
    return json.dumps({"total": len(result), "findings": result},
                      ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def check_finding_freshness_tool() -> str:
    """检测经验结论的时效性：锚定符号在 cppsg 代码图谱中是否还存在。

    代码重构/删除后，结论可能已失效。本工具将 finding 锚定的符号与 cppsg
    当前节点比对（宽松匹配），自动做幂等状态迁移：
    - 全部锚定符号消失 → active → stale（查询时会带 ⚠️ 提示）
    - stale 结论的符号回归 → stale → active（自动恢复）

    建议在 cppsg 重建后调用一次。

    Returns:
        JSON：{"checked": n, "stale_marked": n, "recovered": n, "detail": [...]}
    """
    db_path = os.environ.get("DOC_GRAPH_DB", DEFAULT_DB)
    result = check_freshness(db_path=db_path)
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


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
    try:
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

        doc_total = conn.execute(
            "SELECT COUNT(*) FROM node WHERE type='document'"
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

        # 经验结论（finding）统计：表不存在时视为 0（老库兼容）
        finding_total = finding_active = finding_stale = anchor_total = 0
        finding_types: list[dict] = []
        has_finding = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='finding'"
        ).fetchone()
        if has_finding:
            finding_total = conn.execute("SELECT COUNT(*) FROM finding").fetchone()[0]
            finding_active = conn.execute(
                "SELECT COUNT(*) FROM finding WHERE status='active'").fetchone()[0]
            finding_stale = conn.execute(
                "SELECT COUNT(*) FROM finding WHERE status='stale'").fetchone()[0]
            anchor_total = conn.execute(
                "SELECT COUNT(*) FROM finding_symbol").fetchone()[0]
            finding_types = [dict(r) for r in conn.execute(
                "SELECT ftype, COUNT(*) as cnt FROM finding GROUP BY ftype ORDER BY cnt DESC")]

        return json.dumps({
            "nodes": node_total,
            "edges": edge_total,
            "documents_with_frontmatter": has_fm,
            "document_summary_coverage": f"{doc_with_summary}/{doc_total}",
            "document_summary_pct": round(100.0 * doc_with_summary / max(1, doc_total), 1),
            "knowledge_summary_coverage": f"{know_with_summary}/{know_total}",
            "knowledge_summary_pct": round(100.0 * know_with_summary / max(1, know_total), 1),
            "legacy_documents": legacy,
            "isolated_documents": isolated,
            "doc_type_distribution": [dict(r) for r in doc_types],
            "edge_type_distribution": [dict(r) for r in edge_types],
            "top_symbols": [dict(r) for r in top_symbols],
            "findings": finding_total,
            "findings_active": finding_active,
            "findings_stale": finding_stale,
            "finding_symbol_anchors": anchor_total,
            "finding_type_distribution": finding_types,
        }, ensure_ascii=False, indent=2)
    finally:
        conn.close()


# ─── 主函数 ───────────────────────────────────────────────────

if __name__ == "__main__":
    # 传输模式：默认 stdio（向后兼容）；DOC_GRAPH_TRANSPORT=http 跑单例 HTTP 服务
    # http 模式配合 systemd user unit（~/.config/systemd/user/doc-graph-mcp.service），
    # 多客户端共享单进程，避免 stdio 模式每连接一个进程的资源浪费与孤儿堆积。
    # 端点：http://127.0.0.1:8930/mcp
    if os.environ.get("DOC_GRAPH_TRANSPORT", "").lower() == "http":
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = int(os.environ.get("DOC_GRAPH_PORT", "8930"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
