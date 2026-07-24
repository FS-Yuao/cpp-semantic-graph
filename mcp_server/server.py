"""C++ 语义图谱 MCP Server

按 MCP 协议封装查询能力，暴露 11 个工具，让 AI 直接查图谱。

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
from cpp_semantic_graph.query.blast_radius_query import BlastRadiusQuery

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
_bq: BlastRadiusQuery | None = None

# task_4_5: 惰性增量状态（进程内缓存，避免每次查询都读 DB/git）
_lazy_config = None            # ProjectConfig 缓存（首次 from_yaml 后复用）
_lazy_config_loaded = False    # config 是否已尝试加载（None 也算加载过，避免重试）
_last_checked_head = ""        # 上次查询时的 HEAD（同 commit 不重复读 DB）


def _load_config_for_lazy():
    """从 DB 同目录读 cpp_semantic_graph.yaml（复用 _infer_project_name 策略2）

    惰性增量需要 config（compile_commands/source_paths/repo_root 推断）。
    缓存首次加载结果，避免每次查询都 from_yaml。
    """
    global _lazy_config, _lazy_config_loaded
    if _lazy_config_loaded:
        return _lazy_config
    _lazy_config_loaded = True
    try:
        yaml_path = Path(_db_path).parent / "cpp_semantic_graph.yaml"
        if yaml_path.exists():
            from cpp_semantic_graph.parser.config import ProjectConfig
            _lazy_config = ProjectConfig.from_yaml(str(yaml_path))
    except Exception as e:
        logger.warning("加载 config 失败（惰性增量禁用）: %s", e)
    return _lazy_config


def _ensure_fresh() -> None:
    """task_4_5: 惰性增量--查询前检测新合入 commit，有才增量一次，同一 commit no-op

    流程：
    1. lazy_increment_enabled=False / config 缺失 -> return（功能关闭）
    2. git rev-parse HEAD（<1ms）；失败 -> return（降级用旧图谱）
    3. head == _last_checked_head -> return（同 commit 不重复读 DB）
    4. 读 last_incremented_ref；为空（首次）-> 记录 HEAD 不增量，return
    5. head == last_ref -> no-op return
    6. 变更文件数 > threshold -> warning + return（降级提示手动跑）
    7. 跑增量 base_ref=last_ref（record_state=True 更新 last_ref=HEAD）
    8. 刷新查询连接（_gq 等置 None，下次 _get_queries 重建）
    任何异常 -> warning + return（查询用旧图谱，不阻塞）
    """
    global _gq, _cq, _pq, _tq, _dq, _bq, _last_checked_head
    try:
        config = _load_config_for_lazy()
        if config is None or not config.lazy_increment_enabled:
            return
        from cpp_semantic_graph.parser.change_detector import ChangeDetector
        detector = ChangeDetector(None, config)
        head = detector.get_current_ref()
        if not head:
            return  # 非 git 仓库 / repo_root 推断失败，降级用旧图谱
        # 同 commit 不重复读 DB（rev-parse 节流核心）
        if head == _last_checked_head:
            return
        _last_checked_head = head

        from cpp_semantic_graph.db.graph_db import GraphDB
        db = GraphDB(_db_path)
        try:
            last_ref = db.get_last_incremented_ref()
            if not last_ref:
                # 首次：记录当前 HEAD，不增量（full-parse 已是最新）
                db.set_last_incremented_ref(head)
                logger.info("惰性增量首次：记录 last_incremented_ref=%s", head[:12])
                return
            if head == last_ref:
                return  # no-op，同一 commit
        finally:
            db.close()

        # 有新合入 commit：算变更文件数（阈值降级判断）
        changes = detector.detect_from_git(last_ref)
        n = len(changes.all_changed)
        if n == 0:
            # hash 不同但无 diff（空 commit / last_ref 被 reset）：直接记录 HEAD
            db2 = GraphDB(_db_path)
            try:
                db2.set_last_incremented_ref(head)
            finally:
                db2.close()
            return
        if n > config.lazy_increment_threshold:
            logger.warning(
                "检测到 %d 个变更文件（超阈值 %d），跳过同步增量，请手动跑 "
                "`python -m cpp_semantic_graph incremental`。查询使用旧图谱。",
                n, config.lazy_increment_threshold)
            return
        # 变更量可接受：跑增量（record_state=True 会更新 last_ref=HEAD）
        from cpp_semantic_graph.incremental_updater import IncrementalUpdater
        updater = IncrementalUpdater(config.config_path, _db_path)
        report = updater.run(base_ref=last_ref, record_state=True)
        logger.info("惰性增量：%d 文件变更，%d TU 重解析（%d 失败）",
                    report.files_changed, report.tus_reparsed, report.tus_failed)
        # 刷新查询连接（增量后 DB 已变，旧连接指向旧数据）
        _gq = _cq = _pq = _tq = _dq = _bq = None
    except Exception as e:
        logger.warning("惰性增量失败，查询用旧图谱: %s", e)


def _get_queries() -> tuple[GraphQuery, CallQuery, PolymorphismQuery,
                             TraverseQuery, DocQuery]:
    """Lazy init：首次调用时建立 DB 连接"""
    global _gq, _cq, _pq, _tq, _dq
    _ensure_fresh()  # task_4_5: 惰性增量（有新 commit 才增量，同一 commit no-op）
    if _gq is None:
        if not _db_path or not Path(_db_path).exists():
            raise FileNotFoundError(f"图谱数据库不存在: {_db_path}")
        _gq = GraphQuery(_db_path)
        _cq = CallQuery(_db_path)
        _pq = PolymorphismQuery(_db_path)
        _tq = TraverseQuery(_db_path)
        _dq = DocQuery(_db_path)
    return _gq, _cq, _pq, _tq, _dq


def _get_blast() -> BlastRadiusQuery:
    """Lazy init：首次调用时建立 BlastRadiusQuery"""
    global _bq
    _ensure_fresh()
    if _bq is None:
        if not _db_path or not Path(_db_path).exists():
            raise FileNotFoundError(f"图谱数据库不存在: {_db_path}")
        _bq = BlastRadiusQuery(_db_path)
    return _bq


def _query_error(e: Exception) -> str:
    """统一处理查询异常，避免 database is locked 等异常传播到 MCP 框架层（主题D）"""
    if isinstance(e, FileNotFoundError):
        return str(e)
    logger.exception("MCP 查询异常")
    return f"查询失败（{type(e).__name__}）: {e}"


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


def _fmt_doc_section(d) -> str:
    """格式化单个文档切片（用于 cpp_get_code_docs 反向查询，不含关联代码）"""
    lines = [f"### {d.title}\n"]
    if d.doc_title:
        lines.append(f"- 文档: {d.doc_title}\n")
    lines.append(f"- 文件: {d.file_path}:{d.start_line}-{d.end_line}\n")
    lines.append(f"- 字数: {d.word_count}\n")
    if d.tags:
        lines.append(f"- 标签: {', '.join(d.tags)}\n")
    if d.content_preview:
        preview = d.content_preview[:300]
        if len(d.content_preview) > 300:
            preview += "..."
        lines.append(f"\n> {preview}\n")
    return "".join(lines)


def _fmt_traverse_node(node: dict) -> str:
    name = node.get("name", "")
    ns = node.get("namespace", "")
    fp = node.get("file_path", "")
    ntype = node.get("type", "")
    ns_prefix = f"{ns}::" if ns else ""
    return f"  - [{ntype}] {ns_prefix}{name} ({fp})\n"


def _fmt_blast_radius(result, direction: str) -> str:
    """格式化爆炸半径结果

    输出：起点 → 展开的 override/子类 → 按跳数分层的受影响文件清单
    """
    if not result.affected_nodes and not result.expanded_overrides \
            and not result.expanded_subclasses:
        return "## 爆炸半径\n\n未找到受影响代码（起点可能无调用方或为叶子节点）。\n"

    dir_text = "受影响（被谁调用）" if direction == "up" else "依赖（调用了谁）"
    n_files = len(result.affected_files)
    n_nodes = len(result.affected_nodes)

    lines = [f"## 爆炸半径（{dir_text}）\n\n"]
    lines.append(f"共 **{n_files} 个文件**需 review，{n_nodes} 个受影响符号，"
                 f"最大跳数 {result.max_depth_reached}\n\n")

    # 起点符号
    if result.origin_functions:
        lines.append("### 起点函数\n\n")
        for fo in result.origin_functions:
            vflag = " [虚函数]" if fo.get("is_virtual") else ""
            cls = f"{fo['class_name']}::" if fo.get("class_name") else ""
            lines.append(f"- {cls}{fo['name']}{vflag} ({fo['file_path']})\n")
    if result.origin_classes:
        lines.append("\n### 起点类\n\n")
        for co in result.origin_classes:
            lines.append(f"- {co['name']} ({co['file_path']})\n")

    # 展开的 override / 子类
    if result.expanded_overrides:
        lines.append(f"\n### 展开的虚函数 override（{len(result.expanded_overrides)} 个，需同步修改）\n\n")
        for o in result.expanded_overrides:
            lines.append(f"- {o['class_name']}::{o['function_name']} ({o['file_path']})\n")
    if result.expanded_subclasses:
        lines.append(f"\n### 展开的直接子类（{len(result.expanded_subclasses)} 个）\n\n")
        for s in result.expanded_subclasses:
            lines.append(f"- {s['name']} ({s['file_path']})\n")

    # 按跳数分层的受影响文件
    lines.append("\n### 受影响文件（按跳数分层）\n\n")
    # 按 depth 分组文件
    depth_files: dict[int, list[tuple[str, BlastNode]]] = {}
    # 重新按 depth 组织（同一文件可能多跳都有，取该文件最小跳数归层）
    file_min_depth: dict[str, int] = {}
    for fp, nodes in result.affected_files.items():
        file_min_depth[fp] = min(n.depth for n in nodes)

    for fp in sorted(file_min_depth, key=lambda f: (file_min_depth[f], f)):
        d = file_min_depth[fp]
        depth_files.setdefault(d, []).append((fp, result.affected_files[fp]))

    hop_label = {1: "直接受影响", 2: "2 跳", 3: "3 跳", 4: "4 跳", 5: "5 跳"}
    for d in sorted(depth_files):
        label = hop_label.get(d, f"{d} 跳")
        lines.append(f"#### {label}（{len(depth_files[d])} 个文件）\n\n")
        for fp, nodes in depth_files[d]:
            # 该文件受影响的符号明细
            sym_strs = []
            for n in nodes:
                if n.depth == d:
                    cls = f"{n.class_name}::" if n.class_name else ""
                    ct = f" [{n.call_type}]" if n.call_type else ""
                    sym_strs.append(f"{cls}{n.function_name}{ct}")
            lines.append(f"- {fp} ← {', '.join(sym_strs[:5])}")
            if len(sym_strs) > 5:
                lines.append(f" ... 共 {len(sym_strs)} 个符号")
            lines.append("\n")

    if result.truncated:
        lines.append(f"\n> ⚠ 结果已截断至 {BlastRadiusQuery.MAX_NODES} 个节点，"
                     "可能未列全。可缩小 depth 或细化起点。\n")

    return "".join(lines)


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
    except Exception as e:
        return _query_error(e)

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
    except Exception as e:
        return _query_error(e)

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
    # 输入边界校验（主题D）
    if direction not in ("up", "down"):
        return 'direction 必须是 "up" 或 "down"'
    if depth < -1 or depth > 10:
        return "depth 范围 [-1, 10]（-1=全部），过大会导致遍历过深"
    try:
        gq, _, _, _, _ = _get_queries()
        results = gq.get_inheritance(class_name, direction=direction, depth=depth)
    except Exception as e:
        return _query_error(e)

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
    except Exception as e:
        return _query_error(e)

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
    except Exception as e:
        return _query_error(e)

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
    except Exception as e:
        return _query_error(e)

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
    except Exception as e:
        return _query_error(e)

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
    # 输入边界校验（主题D）
    if direction not in ("outgoing", "incoming"):
        return 'direction 必须是 "outgoing" 或 "incoming"'
    if depth < 1 or depth > 6:
        return "depth 范围 [1, 6]，过大会导致遍历指数级膨胀"
    if max_results < 1 or max_results > 500:
        return "max_results 范围 [1, 500]"
    try:
        _, _, _, tq, _ = _get_queries()
        result = tq.traverse_graph(
            start, relation_types=relation_types, direction=direction,
            depth=depth, max_results=max_results,
        )
    except Exception as e:
        return _query_error(e)

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
def cpp_blast_radius(symbols: list[str] | None = None,
                     files: list[str] | None = None,
                     depth: int = 3,
                     include_overrides: bool = True,
                     include_subclasses: bool = True,
                     direction: str = "up") -> str:
    """计算改动爆炸半径：输入改动的符号/文件，返回受影响文件清单 + 分层调用链。用于：改动前评估影响面、确定 review 范围、定位多态调度受影响方。比 cpp_get_callers（一跳）更全：递归追多层 + 虚函数 override 展开 + 文件维度去重 + 按跳数分层。

    方向语义：direction="up"（默认）= 谁受影响（被谁调用，改动向后传播）;
             "down" = 依赖什么（调用了谁，前置依赖）。

    Args:
        symbols: 改动符号名列表（函数名/类名，支持 "Class::func" 形式）
        files: 改动文件路径列表（部分匹配，自动展开为文件内符号）
        depth: 最大递归跳数 [1,5]（1=只看直接受影响，3=默认）
        include_overrides: 是否展开虚函数 override（多态调度方受影响，默认开）
        include_subclasses: 是否展开类的直接子类（默认开）
        direction: "up"=谁受影响（默认）/"down"=依赖什么
    """
    # 输入边界校验（主题D）
    if direction not in ("up", "down"):
        return 'direction 必须是 "up" 或 "down"'
    if depth < 1 or depth > 5:
        return "depth 范围 [1, 5]（1=直接受影响，5=最深，过大易膨胀）"
    if not symbols and not files:
        return "至少提供 symbols 或 files 之一"

    try:
        bq = _get_blast()
        result = bq.compute(
            symbols=symbols, files=files, depth=depth,
            include_overrides=include_overrides,
            include_subclasses=include_subclasses,
            direction=direction,
        )
    except Exception as e:
        return _query_error(e)

    return _fmt_blast_radius(result, direction)


@mcp.tool()
def cpp_search_docs(keyword: str, tag: str = "", max_results: int = 10,
                     min_confidence: float = 0.7) -> str:
    """搜索项目文档，返回文档切片+关联代码。用于：查设计说明、找任务文档、理解架构决策。搜索文档标题和内容，同时定位到相关代码实现。

    注意：min_confidence 默认 0.7，过滤低质量共现关联（confidence=0.6 的占 63%，
    多为关键词共现的泛类，如文档讲"刷写"却关联到 Data/Response 等泛化结构）。
    如需查看全部关联（含低质量），显式传 min_confidence=0.0。

    Args:
        keyword: 搜索关键词（如 "升级"、"OTA"、"刷写"）
        tag: 按标签过滤（可选，如 "架构设计"）
        max_results: 最大返回数（默认 10）
        min_confidence: 关联代码最低置信度（默认 0.7 过滤噪声；0.0=不过滤；1.0=仅高质量）
    """
    try:
        _, _, _, _, dq = _get_queries()
        results = dq.search_documentation(
            keyword, tag=tag or None, max_results=max_results,
            min_confidence=min_confidence,
        )
    except Exception as e:
        return _query_error(e)

    if not results:
        return f'未找到关键词 "{keyword}" 相关的文档。'

    lines = [f'## 文档搜索："{keyword}"（{len(results)} 个结果）\n\n']
    for i, dw in enumerate(results, 1):
        lines.append(f"{i}. {_fmt_doc_result(dw)}\n")
    return "".join(lines)


@mcp.tool()
def cpp_get_code_docs(symbol: str, min_confidence: float = 0.0,
                      max_results: int = 10) -> str:
    """查询描述指定代码符号的文档切片（反向：代码 -> 文档）。用于：改代码前查设计说明、理解某函数/类的设计意图、找架构文档依据。比 cpp_search_docs 反向：给定代码符号，直接返回讲它的文档。

    底层走 code_refers_to_doc + doc_describes_code 双向边，命中讲该符号的设计文档/
    HLD/架构文档。

    min_confidence 默认 0.0（不过滤）：反向关联多为 content_scan（confidence=0.6），
    与正向不同--代码符号名出现在文档里通常就是讲它，0.6 多数有效，故默认全返回，
    靠 max_results 限量。如需只要高质量，传 1.0。

    Args:
        symbol: 代码符号名（函数名或类名，如 "PerformUpgrade" / "SocUpdate"）
        min_confidence: 最低置信度（默认 0.0 不过滤；1.0=仅高质量精确匹配）
        max_results: 最大返回数（默认 10）
    """
    try:
        _, _, _, _, dq = _get_queries()
        # 函数 + 类都查，合并去重（符号可能是函数或类）
        docs = dq.get_docs_for_function(symbol, min_confidence=min_confidence)
        docs += dq.get_docs_for_class(symbol, min_confidence=min_confidence)
    except Exception as e:
        return _query_error(e)

    if not docs:
        return f'未找到描述 "{symbol}" 的文档（可能无关联设计文档，或置信度低于 {min_confidence}）。'

    # 去重（同一文档切片可能被函数和类两条路径命中）
    seen: set[tuple] = set()
    unique: list = []
    for d in docs:
        key = (d.file_path, d.start_line)
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)
    unique = unique[:max_results]

    lines = [f'## 描述 "{symbol}" 的文档（{len(unique)} 个切片）\n\n']
    for i, d in enumerate(unique, 1):
        lines.append(f"{i}. {_fmt_doc_section(d)}\n")
    return "".join(lines)


# ── 启动入口 ──

def _infer_project_name(db_path: str) -> str:
    """从 DB 路径推断项目名称

    策略（按优先级）：
    1. 环境变量 CPP_PROJECT_NAME
    2. DB 同目录 cpp_semantic_graph.yaml 的 project.name（P2-1：迁移后路径正则失效，读配置更可靠）
    3. 路径正则 /app/<project>/ 或 /src/<project>/（fallback）
    """
    import re
    # 1. 环境变量
    env_name = os.environ.get("CPP_PROJECT_NAME", "").strip()
    if env_name:
        return env_name
    # 2. DB 同目录 yaml 的 project.name
    try:
        yaml_path = Path(db_path).parent / "cpp_semantic_graph.yaml"
        if yaml_path.exists():
            import yaml
            with open(yaml_path) as f:
                cfg = yaml.safe_load(f) or {}
            proj = (cfg.get("project") or {}).get("name", "").strip()
            if proj:
                return proj
    except Exception:
        pass
    # 3. 路径正则（fallback）
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
