"""C++ 语义图谱 MCP Server

按 MCP 协议封装查询能力，暴露 9 个工具，让 AI 直接查图谱。

用法:
  # 环境变量指定 DB 路径
  CPP_GRAPH_DB=/path/to/semantic_graph_full.db python3 -m cpp_semantic_graph.mcp_server.server

  # 或默认路径（同目录下 semantic_graph_full.db）
  python3 -m cpp_semantic_graph.mcp_server.server

注册到 Claude Code (.claude/settings.json):
  {
    "mcpServers": {
      "cpp-semantic-graph": {
        "command": "python3",
        "args": ["-m", "cpp_semantic_graph.mcp_server.server"],
        "env": {
          "CPP_GRAPH_DB": "/path/to/semantic_graph_full.db"
        }
      }
    }
  }
"""

from __future__ import annotations

import os
import sys
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 确保包可导入
_HERE = Path(__file__).resolve().parent.parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from cpp_semantic_graph.query.graph_query import GraphQuery
from cpp_semantic_graph.query.call_query import CallQuery
from cpp_semantic_graph.query.polymorphism_query import PolymorphismQuery
from cpp_semantic_graph.query.traverse import TraverseQuery
from cpp_semantic_graph.query.doc_query import DocQuery

logger = logging.getLogger(__name__)

# ── FastMCP 实例 ──

# 项目名称：模块级占位，最终值在 main() 中确定
# （环境变量 > 从 DB 路径推断）。导入期为空，避免在 main 推断前固化 instructions。
_PROJECT_NAME = os.environ.get("CPP_GRAPH_PROJECT", "")


def _build_instructions() -> str:
    """构建 MCP instructions，动态包含项目名（如已知）

    注意：必须在 _PROJECT_NAME 确定后调用（见 main()），
    否则 instructions 中不会含项目名。
    """
    base = ("C++ 语义图谱查询工具。用于搜索 C++ 类/函数定义、查继承/调用/override 关系、"
            "多跳遍历影响面分析、搜索项目文档。")
    if _PROJECT_NAME:
        base += f"适用于 {_PROJECT_NAME} 项目的 C++ 代码查询场景。"
    return base


# FastMCP 实例在导入期创建（@mcp.tool() 装饰器需要实例存在）。
# instructions 先用通用占位，main() 推断完项目名后通过 _mcp_server 覆写。
mcp = FastMCP(
    "cpp-semantic-graph",
    instructions=_build_instructions(),
)


def _update_instructions():
    """推断完项目名后更新 MCP instructions（通过内部属性覆写）。

    FastMCP.instructions 是只读 property（返回 _mcp_server.instructions），
    因此直接赋值 mcp.instructions 会报错。但 _mcp_server.instructions
    是普通实例属性，可以赋值。main() 调用此方法即可让推断的项目名生效。
    """
    mcp._mcp_server.instructions = _build_instructions()

# ── DB 连接（Lazy init） ──

_db_path: str = ""
_gq: GraphQuery | None = None
_cq: CallQuery | None = None
_pq: PolymorphismQuery | None = None
_tq: TraverseQuery | None = None
_dq: DocQuery | None = None


def _get_queries() -> tuple[GraphQuery, CallQuery, PolymorphismQuery,
                             TraverseQuery, DocQuery]:
    """Lazy init：首次调用时建立 DB 连接"""
    global _gq, _cq, _pq, _tq, _dq
    if _gq is None:
        if not _db_path or not Path(_db_path).exists():
            raise FileNotFoundError(f"图谱数据库不存在: {_db_path}")
        _gq = GraphQuery(_db_path)
        _cq = CallQuery(_db_path)
        _pq = PolymorphismQuery(_db_path)
        _tq = TraverseQuery(_db_path)
        _dq = DocQuery(_db_path)
    return _gq, _cq, _pq, _tq, _dq


# ── Markdown 格式化 ──

def _fmt_class(ci) -> str:
    ns = f"{ci.namespace}::" if ci.namespace else ""
    abstract = " (抽象)" if ci.is_abstract else ""
    tmpl = f"<{', '.join(ci.template_params)}>" if ci.template_params else ""
    return (f"### {ns}{ci.name}{tmpl}{abstract}\n"
            f"- 文件: {ci.file_path}:{ci.start_line}-{ci.end_line}\n")


def _fmt_function(fi) -> str:
    flags = []
    if fi.is_virtual:
        flags.append("virtual")
    if fi.is_pure_virtual:
        flags.append("pure")
    if fi.is_override:
        flags.append("override")
    if fi.is_static:
        flags.append("static")
    if fi.is_const:
        flags.append("const")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    cls = f"{fi.class_name}::" if fi.class_name else ""
    return (f"### {cls}{fi.name}{flag_str}\n"
            f"- 签名: {fi.signature}\n"
            f"- 文件: {fi.file_path}:{fi.start_line}\n")


def _fmt_inheritance(info) -> str:
    virt = " (virtual)" if info.is_virtual else ""
    child_ns = f"{info.child.namespace}::" if info.child.namespace else ""
    parent_ns = f"{info.parent.namespace}::" if info.parent.namespace else ""
    return (f"- {child_ns}{info.child.name} "
            f"--{info.access}{virt}--> "
            f"{parent_ns}{info.parent.name}\n")


def _fmt_call_info(call, *, is_caller: bool) -> str:
    """格式化调用关系信息

    is_caller=True: 展示调用方（谁调用了它）
    is_caller=False: 展示被调用方（它调用了谁）
    """
    if is_caller:
        ns = f"{call.caller_namespace}::" if call.caller_namespace else ""
        cls = f"{call.caller_class}::" if call.caller_class else ""
        return (f"### {ns}{cls}{call.caller_name}\n"
                f"- 文件: {call.caller_file}:{call.caller_line}\n"
                f"- 调用类型: {call.call_type}\n")
    else:
        ns = f"{call.callee_namespace}::" if call.callee_namespace else ""
        cls = f"{call.callee_class}::" if call.callee_class else ""
        return (f"### {ns}{cls}{call.callee_name}\n"
                f"- 文件: {call.callee_file}\n"
                f"- 调用类型: {call.call_type}\n")


def _fmt_override(oi) -> str:
    ns = f"{oi.namespace}::" if oi.namespace else ""
    return (f"### {ns}{oi.class_name}::{oi.function_name}\n"
            f"- 签名: {oi.signature}\n"
            f"- 文件: {oi.file_path}:{oi.line_number}\n"
            f"- 重写基类: {oi.base_class}\n")


def _fmt_symbol(si) -> str:
    ns = f"{si.namespace}::" if si.namespace else ""
    return (f"### [{si.node_type}] {ns}{si.name}\n"
            f"- 文件: {si.file_path}:{si.start_line}-{si.end_line}\n")


def _fmt_doc_result(dw) -> str:
    lines = [f"### {dw.doc.title}\n"]
    if dw.doc.doc_title:
        lines.append(f"- 文档: {dw.doc.doc_title}\n")
    lines.append(f"- 文件: {dw.doc.file_path}:{dw.doc.start_line}-{dw.doc.end_line}\n")
    lines.append(f"- 字数: {dw.doc.word_count}\n")
    if dw.doc.tags:
        lines.append(f"- 标签: {', '.join(dw.doc.tags)}\n")
    if dw.doc.content_preview:
        preview = dw.doc.content_preview[:300]
        if len(dw.doc.content_preview) > 300:
            preview += "..."
        lines.append(f"\n> {preview}\n")
    if dw.related_code:
        lines.append("\n关联代码:\n")
        for rc in dw.related_code[:5]:
            lines.append(f"  - [{rc['type']}] {rc.get('namespace', '')}::{rc['name']}"
                         f" ({rc.get('file_path', '')})"
                         f" confidence={rc.get('confidence', 0):.2f}\n")
    return "".join(lines)


def _fmt_traverse_node(node: dict) -> str:
    name = node.get("name", "")
    ns = node.get("namespace", "")
    fp = node.get("file_path", "")
    ntype = node.get("type", "")
    ns_prefix = f"{ns}::" if ns else ""
    return f"  - [{ntype}] {ns_prefix}{name} ({fp})\n"


# ── MCP 工具定义 ──

@mcp.tool()
def cpp_search_class(name: str, exact: bool = False) -> str:
    """按类名搜索 C++ 类定义。用于：找类在哪定义、查类的基本信息（命名空间、文件位置、是否抽象）。不适合：查继承关系（用 cpp_get_inheritance）、查函数（用 cpp_search_function）。

    Args:
        name: 类名（支持模糊匹配，如 "MyClass" 或 "Update"）
        exact: 是否精确匹配（默认模糊匹配）
    """
    try:
        gq, _, _, _, _ = _get_queries()
        results = gq.search_class(name, exact=exact)
    except FileNotFoundError as e:
        return str(e)

    if not results:
        return f'未找到匹配 "{name}" 的类。'

    lines = [f'## 搜索结果：类 "{name}"（{len(results)} 个）\n\n']
    for i, ci in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_class(ci)}\n")
    return "".join(lines)


@mcp.tool()
def cpp_search_function(name: str, class_name: str = "") -> str:
    """按函数名搜索 C++ 函数定义。用于：找函数定义位置、查看函数签名和所属类。不适合：查调用关系（用 cpp_get_callers/cpp_get_callees）。

    Args:
        name: 函数名（如 "doWork"）
        class_name: 限定所属类名（可选，如 "MyClass"）
    """
    try:
        gq, _, _, _, _ = _get_queries()
        results = gq.search_function(name, class_name=class_name or None)
    except FileNotFoundError as e:
        return str(e)

    if not results:
        return f'未找到匹配 "{name}" 的函数。'

    lines = [f'## 搜索结果：函数 "{name}"（{len(results)} 个）\n\n']
    for i, fi in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_function(fi)}\n")
    return "".join(lines)


@mcp.tool()
def cpp_get_inheritance(class_name: str, direction: str = "down",
                        depth: int = 1) -> str:
    """查询类的继承关系（支持多级）。用于：查父类/子类、理解类层次结构。direction="down" 查子类，"up" 查父类。

    Args:
        class_name: 类名（如 "MyBaseClass"）
        direction: 查询方向，"down" 查子类，"up" 查父类
        depth: 递归深度（1=直接，-1=全部）
    """
    try:
        gq, _, _, _, _ = _get_queries()
        results = gq.get_inheritance(class_name, direction=direction, depth=depth)
    except FileNotFoundError as e:
        return str(e)

    if not results:
        dir_text = "子类" if direction == "down" else "父类"
        return f'未找到 {class_name} 的{dir_text}。'

    dir_text = "子类" if direction == "down" else "父类"
    lines = [f'## {class_name} 的{dir_text}（{len(results)} 条）\n\n']
    for info in results:
        lines.append(_fmt_inheritance(info))
    return "".join(lines)


@mcp.tool()
def cpp_get_callers(function_name: str, class_name: str = "") -> str:
    """查询谁调用了指定函数（影响面分析）。用于：修改函数前评估影响范围、理解函数被谁依赖。不适合：查函数调用了谁（用 cpp_get_callees）。

    Args:
        function_name: 被调用方函数名（如 "getValue"）
        class_name: 限定所属类名（可选）
    """
    try:
        _, cq, _, _, _ = _get_queries()
        results = cq.get_callers(function_name, class_name=class_name or None)
    except FileNotFoundError as e:
        return str(e)

    if not results:
        return f'未找到调用 "{function_name}" 的代码。'

    lines = [f'## 调用 "{function_name}" 的代码（{len(results)} 个）\n\n']
    for i, call in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_call_info(call, is_caller=True)}\n")
    return "".join(lines)


@mcp.tool()
def cpp_get_callees(function_name: str, class_name: str = "") -> str:
    """查询指定函数调用了谁（调用链分析）。用于：理解函数内部逻辑、追踪依赖路径。不适合：查谁调用了此函数（用 cpp_get_callers）。

    Args:
        function_name: 调用方函数名（如 "doWork"）
        class_name: 限定所属类名（可选）
    """
    try:
        _, cq, _, _, _ = _get_queries()
        results = cq.get_callees(function_name, class_name=class_name or None)
    except FileNotFoundError as e:
        return str(e)

    if not results:
        return f'未找到 "{function_name}" 调用的代码。'

    lines = [f'## "{function_name}" 调用的代码（{len(results)} 个）\n\n']
    for i, call in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_call_info(call, is_caller=False)}\n")
    return "".join(lines)


@mcp.tool()
def cpp_get_overrides(function_name: str, class_name: str) -> str:
    """查询虚函数的所有重写实现。用于：查接口的所有实现、理解多态调度。适合分析 override 和纯虚函数的具体实现。

    Args:
        function_name: 虚函数名（如 "doWork"）
        class_name: 声明该虚函数的基类名（必填，如 "MyBaseClass"）
    """
    try:
        _, _, pq, _, _ = _get_queries()
        results = pq.get_all_overrides(function_name, class_name=class_name)
    except FileNotFoundError as e:
        return str(e)

    if not results:
        return f'未找到 "{function_name}" 的重写实现。'

    lines = [f'## "{function_name}" 的重写实现（{len(results)} 个）\n\n']
    for i, oi in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_override(oi)}\n")
    return "".join(lines)


@mcp.tool()
def cpp_get_file_symbols(file_path: str) -> str:
    """查询文件内的所有类和函数符号。用于：快速了解文件内容、确认文件包含哪些定义。

    Args:
        file_path: 文件路径（部分匹配即可，如 "my_module.h"）
    """
    try:
        gq, _, _, _, _ = _get_queries()
        results = gq.get_file_symbols(file_path)
    except FileNotFoundError as e:
        return str(e)

    if not results:
        return f'未找到文件 "{file_path}" 中的符号。'

    # 按类型分组
    classes = [s for s in results if s.node_type in ("class", "struct")]
    functions = [s for s in results if s.node_type == "function"]
    others = [s for s in results if s.node_type not in ("class", "struct", "function")]

    lines = [f'## 文件符号：{file_path}（共 {len(results)} 个）\n\n']

    if classes:
        lines.append(f"### 类/结构体（{len(classes)} 个）\n\n")
        for i, s in enumerate(classes, 1):
            lines.append(f"{i}. {_fmt_symbol(s)}\n")

    if functions:
        lines.append(f"### 函数（{len(functions)} 个）\n\n")
        for i, s in enumerate(functions, 1):
            lines.append(f"{i}. {_fmt_symbol(s)}\n")

    if others:
        lines.append(f"### 其他（{len(others)} 个）\n\n")
        for i, s in enumerate(others, 1):
            lines.append(f"{i}. {_fmt_symbol(s)}\n")

    return "".join(lines)


@mcp.tool()
def cpp_traverse_graph(start: str, relation_types: list[str] | None = None,
                        direction: str = "outgoing", depth: int = 3,
                        max_results: int = 50) -> str:
    """多跳遍历图谱，沿指定关系类型查询关联节点。用于：影响面分析（修改 X 会影响什么）、跨模块关联查询、复杂依赖链追踪。这是最灵活的查询，支持多种关系类型组合。

    常用关系类型: inherits_public, inherits_protected, overrides, belongs_to,
    calls_direct, calls_virtual, calls_callback, doc_describes_code, code_refers_to_doc

    Args:
        start: 起始节点名称（如 "MyClass"）
        relation_types: 遍历的关系类型列表（None=所有类型）
        direction: 遍历方向，"outgoing" 或 "incoming"
        depth: 最大遍历深度（默认 3）
        max_results: 最大返回节点数（默认 50）
    """
    try:
        _, _, _, tq, _ = _get_queries()
        result = tq.traverse_graph(
            start, relation_types=relation_types, direction=direction,
            depth=depth, max_results=max_results,
        )
    except FileNotFoundError as e:
        return str(e)

    if not result.nodes:
        return f'从 "{start}" 出发未找到关联节点。'

    lines = [f'## 遍历结果：从 "{start}" 出发（{len(result.nodes)} 个节点）\n\n']
    lines.append(f"深度: {result.stats.max_depth_reached}, "
                 f"遍历边数: {result.stats.total_edges_traversed}")
    if result.stats.truncated:
        lines.append(f" (已截断，共 {max_results} 上限)")
    lines.append("\n\n### 关联节点\n\n")

    for node in result.nodes:
        lines.append(_fmt_traverse_node(node))

    if result.edges:
        lines.append(f"\n### 遍历边（{len(result.edges)} 条）\n\n")
        for edge in result.edges[:20]:
            rt = edge.get("relation_type", "")
            from_id = edge.get("from_id", "")
            to_id = edge.get("to_id", "")
            lines.append(f"  - {rt}: {from_id} → {to_id}\n")
        if len(result.edges) > 20:
            lines.append(f"  ... 共 {len(result.edges)} 条\n")

    return "".join(lines)


@mcp.tool()
def cpp_search_docs(keyword: str, tag: str = "", max_results: int = 10,
                     min_confidence: float = 0.0) -> str:
    """搜索项目文档，返回文档切片+关联代码。用于：查设计说明、找任务文档、理解架构决策。搜索文档标题和内容，同时定位到相关代码实现。

    Args:
        keyword: 搜索关键词（如 "升级"、"OTA"、"刷写"）
        tag: 按标签过滤（可选，如 "架构设计"）
        max_results: 最大返回数（默认 10）
        min_confidence: 关联代码最低置信度（0=不过滤，0.6=过滤低质量关联）
    """
    try:
        _, _, _, _, dq = _get_queries()
        results = dq.search_documentation(
            keyword, tag=tag or None, max_results=max_results,
            min_confidence=min_confidence,
        )
    except FileNotFoundError as e:
        return str(e)

    if not results:
        return f'未找到关键词 "{keyword}" 相关的文档。'

    lines = [f'## 文档搜索："{keyword}"（{len(results)} 个结果）\n\n']
    for i, dw in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_doc_result(dw)}\n")
    return "".join(lines)


# ── 启动入口 ──

def _infer_project_name(db_path: str) -> str:
    """从 DB 路径推断项目名称

    策略：按常见项目目录结构从 DB 路径中提取项目名。
    例如: .../app/hq_ota_service/_tools/... → hq_ota_service
          .../app/my_project/_tools/... → my_project
    """
    import re
    # 匹配 /app/<project>/ 或 /src/<project>/ 模式
    m = re.search(r'/(?:app|src)/([^/_][^/]*)/', db_path)
    if m:
        return m.group(1)
    return ""


def main():
    global _db_path, _PROJECT_NAME

    # DB 路径：环境变量 > 命令行参数 > 默认
    _db_path = os.environ.get("CPP_GRAPH_DB", "")

    if not _db_path:
        # 默认：同目录下的 semantic_graph_full.db
        default_db = _HERE / "semantic_graph_full.db"
        if default_db.exists():
            _db_path = str(default_db)
        else:
            _db_path = str(_HERE / "semantic_graph.db")

    # 命令行参数覆盖
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--db" and i < len(sys.argv) - 1:
            _db_path = sys.argv[i + 1]
            break

    if not Path(_db_path).exists():
        print(f"错误: 图谱数据库不存在: {_db_path}", file=sys.stderr)
        print("请设置 CPP_GRAPH_DB 环境变量或使用 --db 参数指定数据库路径",
              file=sys.stderr)
        sys.exit(1)

    # 推断项目名（环境变量优先，否则从 DB 路径推断）
    if not _PROJECT_NAME:
        _PROJECT_NAME = _infer_project_name(_db_path)

    # 项目名确定后，重新构建 instructions 并覆写到 FastMCP 实例。
    # 导入期的占位 instructions 不含项目名，必须在此刷新，否则
    # "从 DB 路径推断项目名"永远不会出现在发给 AI 的 instructions 里。
    _update_instructions()

    logger.info("C++ 语义图谱 MCP Server 启动, DB=%s, project=%s",
                _db_path, _PROJECT_NAME or "(未知)")
    mcp.run()


if __name__ == "__main__":
    main()
