"""
C++ 语义图谱 CLI 工具

提供 9 个查询命令（与 MCP 工具一一对应）:
  search-class   按类名搜索
  inheritance    查询继承关系
  search-func    按函数名搜索
  file-symbols   查文件内符号
  callers        查谁调用了某函数
  callees        查某函数调用了谁
  overrides      查虚函数的所有重写
  traverse       多跳遍历图谱
  search-docs    搜索项目文档

另有:
  import         批量导入 JSON 到 SQLite
  stats          查看数据库统计

用法:
  python -m cpp_semantic_graph search-class "MyClass"
  python -m cpp_semantic_graph inheritance "MyBaseClass" --direction down
  python -m cpp_semantic_graph callers "doWork"
  python -m cpp_semantic_graph overrides "doWork" --class-name MyBaseClass
  python -m cpp_semantic_graph traverse "MyClass" --depth 2
"""

import argparse
import json
import sys
import time
from pathlib import Path

# 确保包可导入
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from cpp_semantic_graph.query import GraphQuery, ClassInfo, InheritanceInfo, FunctionInfo, SymbolInfo
from cpp_semantic_graph.query.call_query import CallQuery, CallInfo
from cpp_semantic_graph.query.polymorphism_query import PolymorphismQuery, OverrideInfo
from cpp_semantic_graph.query.traverse import TraverseQuery, TraverseResult
from cpp_semantic_graph.query.doc_query import DocQuery, DocWithCode


def _fmt_class(ci: ClassInfo) -> str:
    ns = f"{ci.namespace}::" if ci.namespace else ""
    abstract = " (abstract)" if ci.is_abstract else ""
    tmpl = f"<{', '.join(ci.template_params)}>" if ci.template_params else ""
    return f"  {ns}{ci.name}{tmpl}{abstract}  [{ci.file_path}:{ci.start_line}-{ci.end_line}]"


def _fmt_inheritance(info: InheritanceInfo) -> str:
    virt = " (virtual)" if info.is_virtual else ""
    return (f"  {info.child.namespace}::{info.child.name} "
            f"--{info.access}{virt}--> "
            f"{info.parent.namespace}::{info.parent.name}")


def _fmt_function(fi: FunctionInfo) -> str:
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
    return (f"  {cls}{fi.name}{flag_str}\n"
            f"    signature: {fi.signature}\n"
            f"    location:  {fi.file_path}:{fi.start_line}")


def _fmt_symbol(si: SymbolInfo) -> str:
    ns = f"{si.namespace}::" if si.namespace else ""
    return f"  [{si.node_type}] {ns}{si.name}  ({si.file_path}:{si.start_line}-{si.end_line})"


def _fmt_call_info(ci: CallInfo, is_caller: bool) -> str:
    """格式化调用关系信息

    Args:
        is_caller: True=展示调用方（callers 场景，给 caller_file:line）
                   False=展示被调用方（callees 场景，给 callee_file）
    """
    virt = " [virtual]" if ci.is_virtual_dispatch else ""
    if is_caller:
        cls = f"{ci.caller_class}::" if ci.caller_class else ""
        ns = f"{ci.caller_namespace}::" if ci.caller_namespace else ""
        return (f"  {ns}{cls}{ci.caller_name}  →  {ci.callee_name}{virt}\n"
                f"    [{ci.call_type}]  {ci.caller_file}:{ci.caller_line}")
    # callees
    cls = f"{ci.callee_class}::" if ci.callee_class else ""
    ns = f"{ci.callee_namespace}::" if ci.callee_namespace else ""
    return (f"  {ci.caller_name}  →  {ns}{cls}{ci.callee_name}{virt}\n"
            f"    [{ci.call_type}]  {ci.callee_file}")


def _fmt_override(oi: OverrideInfo) -> str:
    ns = f"{oi.namespace}::" if oi.namespace else ""
    cls = f"{oi.class_name}::" if oi.class_name else ""
    return (f"  {ns}{cls}{oi.function_name}  ({oi.file_path}:{oi.line_number})\n"
            f"    signature: {oi.signature}\n"
            f"    overrides: {oi.base_class}::{oi.function_name}")


def _fmt_doc(dc: DocWithCode) -> str:
    d = dc.doc
    tags = f"  [{', '.join(d.tags)}]" if d.tags else ""
    lines = [
        f"  - {d.title}  ({d.file_path}:{d.start_line}-{d.end_line}){tags}",
    ]
    preview = d.content_preview.replace("\n", " ").strip()
    if preview:
        lines.append(f"    预览: {preview[:120]}{'…' if len(preview) > 120 else ''}")
    if dc.related_code:
        codes = ", ".join(
            f"{c.get('name', '?')}({c.get('confidence', 0):.2f})"
            for c in dc.related_code[:5]
        )
        lines.append(f"    关联代码: {codes}")
    return "\n".join(lines)


# ======================================================================
# 子命令处理
# ======================================================================

def cmd_search_class(args):
    with GraphQuery(args.db) as q:
        start = time.perf_counter()
        results = q.search_class(args.name, exact=args.exact)
        elapsed_ms = (time.perf_counter() - start) * 1000

    if not results:
        print(f"未找到类: {args.name}")
        return

    print(f"找到 {len(results)} 个类 ({elapsed_ms:.1f}ms):")
    for ci in results:
        print(_fmt_class(ci))


def cmd_inheritance(args):
    with GraphQuery(args.db) as q:
        start = time.perf_counter()
        results = q.get_inheritance(args.class_name, direction=args.direction, depth=args.depth)
        elapsed_ms = (time.perf_counter() - start) * 1000

    if not results:
        direction_text = "子类" if args.direction == "down" else "父类"
        print(f"未找到 {args.class_name} 的{direction_text}")
        return

    direction_text = "子类" if args.direction == "down" else "父类"
    print(f"{args.class_name} 的{direction_text} ({len(results)} 条, {elapsed_ms:.1f}ms):")
    for info in results:
        print(_fmt_inheritance(info))


def cmd_search_func(args):
    with GraphQuery(args.db) as q:
        start = time.perf_counter()
        results = q.search_function(args.name, class_name=args.class_name)
        elapsed_ms = (time.perf_counter() - start) * 1000

    if not results:
        print(f"未找到函数: {args.name}")
        return

    print(f"找到 {len(results)} 个函数 ({elapsed_ms:.1f}ms):")
    for fi in results:
        print(_fmt_function(fi))


def cmd_file_symbols(args):
    with GraphQuery(args.db) as q:
        start = time.perf_counter()
        results = q.get_file_symbols(args.file_path)
        elapsed_ms = (time.perf_counter() - start) * 1000

    if not results:
        print(f"未找到文件符号: {args.file_path}")
        return

    # 按类型分组
    classes = [s for s in results if s.node_type in ("class", "struct")]
    functions = [s for s in results if s.node_type == "function"]
    others = [s for s in results if s.node_type not in ("class", "struct", "function")]

    print(f"文件 {args.file_path} 共 {len(results)} 个符号 ({elapsed_ms:.1f}ms):")
    if classes:
        print(f"\n  类/结构体 ({len(classes)}):")
        for s in classes:
            print(_fmt_symbol(s))
    if functions:
        print(f"\n  函数 ({len(functions)}):")
        for s in functions:
            print(_fmt_symbol(s))
    if others:
        print(f"\n  其他 ({len(others)}):")
        for s in others:
            print(_fmt_symbol(s))


def cmd_callers(args):
    """查询谁调用了指定函数"""
    with CallQuery(args.db) as cq:
        start = time.perf_counter()
        results = cq.get_callers(args.name, class_name=args.class_name or None)
        elapsed_ms = (time.perf_counter() - start) * 1000

    if not results:
        print(f"未找到调用 {args.name} 的代码")
        return

    print(f"调用 {args.name} 的代码（{len(results)} 个, {elapsed_ms:.1f}ms）:")
    for ci in results:
        print(_fmt_call_info(ci, is_caller=True))


def cmd_callees(args):
    """查询指定函数调用了谁"""
    with CallQuery(args.db) as cq:
        start = time.perf_counter()
        results = cq.get_callees(args.name, class_name=args.class_name or None)
        elapsed_ms = (time.perf_counter() - start) * 1000

    if not results:
        print(f"未找到 {args.name} 调用的代码")
        return

    print(f"{args.name} 调用的代码（{len(results)} 个, {elapsed_ms:.1f}ms）:")
    for ci in results:
        print(_fmt_call_info(ci, is_caller=False))


def cmd_overrides(args):
    """查询虚函数的所有重写实现"""
    if not args.class_name:
        print("错误: overrides 需要 --class-name 指定声明该虚函数的基类名", file=sys.stderr)
        sys.exit(2)
    with PolymorphismQuery(args.db) as pq:
        start = time.perf_counter()
        results = pq.get_all_overrides(args.name, class_name=args.class_name)
        elapsed_ms = (time.perf_counter() - start) * 1000

    if not results:
        print(f"未找到 {args.name}（基类 {args.class_name}）的重写实现")
        return

    print(f"{args.name} 的重写实现（{len(results)} 个, {elapsed_ms:.1f}ms）:")
    for oi in results:
        print(_fmt_override(oi))


def cmd_traverse(args):
    """多跳遍历图谱"""
    rel_types = None
    if args.relation_types:
        rel_types = [r.strip() for r in args.relation_types.split(",") if r.strip()]

    with TraverseQuery(args.db) as tq:
        start = time.perf_counter()
        result = tq.traverse_graph(
            args.start, relation_types=rel_types, direction=args.direction,
            depth=args.depth, mode=args.mode, max_results=args.max_results,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

    if not result.nodes:
        print(f"从 {args.start} 出发未找到关联节点")
        return

    print(f"从 {args.start} 出发（{len(result.nodes)} 个节点, "
          f"{result.stats.total_edges_traversed} 条边, {elapsed_ms:.1f}ms）:")
    if result.stats.truncated:
        print(f"  (结果已截断，上限 {args.max_results})")
    for i, node in enumerate(result.nodes, 1):
        ns = node.get("namespace", "")
        ns = f"{ns}::" if ns else ""
        name = node.get("name", "?")
        ntype = node.get("type", "?")
        fp = node.get("file_path", "")
        print(f"  {i}. [{ntype}] {ns}{name}  ({fp})")


def cmd_search_docs(args):
    """搜索项目文档"""
    with DocQuery(args.db) as dq:
        start = time.perf_counter()
        results = dq.search_documentation(
            args.keyword, tag=args.tag or None,
            max_results=args.max_results, min_confidence=args.min_confidence,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

    if not results:
        print(f"未找到匹配 {args.keyword!r} 的文档")
        return

    print(f"文档搜索 {args.keyword!r}（{len(results)} 个结果, {elapsed_ms:.1f}ms）:")
    for dc in results:
        print(_fmt_doc(dc))


def cmd_import(args):
    """批量导入 JSON"""
    from cpp_semantic_graph.db.importer import Importer

    with Importer(args.db) as importer:
        json_path = Path(args.json_path)
        if json_path.is_file():
            stats = importer.import_json_file(json_path)
        elif json_path.is_dir():
            stats = importer.import_json_dir(json_path, verbose=args.verbose)
        else:
            print(f"错误: 路径不存在: {json_path}", file=sys.stderr)
            sys.exit(1)

        importer.print_summary(stats)


def cmd_stats(args):
    """数据库统计"""
    from cpp_semantic_graph.db.importer import Importer

    with Importer(args.db) as importer:
        db_stats = importer.get_db_stats()
        print(json.dumps(db_stats, indent=2, ensure_ascii=False))


def cmd_full_parse(args):
    """端到端全量解析"""
    from cpp_semantic_graph.pipeline import FullParsePipeline
    import json as _json

    config_path = args.config
    if not Path(config_path).exists():
        print(f"错误: 配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)

    pipeline = FullParsePipeline(config_path)
    report = pipeline.run(
        db_path=args.db,
        filter_path=args.filter,
        include_generated=args.include_generated,
        run_validation=args.validate,
        baseline_path=args.baseline,
        reset_db=not args.no_reset,
    )

    print("\n" + "=" * 60)
    print("全量解析报告")
    print("=" * 60)
    print(f"  翻译单元:  {report.tu_total} (成功 {report.tu_success} / 失败 {report.tu_failed})")
    print(f"  失败率:    {report.failure_rate:.1%}")
    if report.failed_files:
        print(f"  失败文件:")
        for f in report.failed_files[:10]:
            print(f"    - {f}")
        if len(report.failed_files) > 10:
            print(f"    ... 共 {len(report.failed_files)} 个")
    print(f"  入库:      +{report.nodes_new} 节点, +{report.edges_new} 边, "
          f"+{report.includes_new} includes")
    print(f"  数据库:    {report.db_node_count} 节点 / {report.db_edge_count} 边 / "
          f"{report.db_include_count} includes")
    if report.node_type_dist:
        print(f"  节点类型:  {report.node_type_dist}")
    if report.edge_type_dist:
        print(f"  边类型:     {report.edge_type_dist}")
    print(f"  耗时:      解析 {report.parse_seconds:.1f}s + 入库 {report.import_seconds:.1f}s "
          f"= {report.total_seconds:.1f}s")
    if report.validation:
        print("\n  正确性验证:")
        if report.validation.get("skipped"):
            print(f"    跳过: {report.validation['skipped']}")
        else:
            for d in report.validation.get("dimensions", []):
                mark = "✓" if d["pass"] else "✗"
                print(f"    {mark} {d['name']}: P={d['precision']} R={d['recall']}")
            verdict = "全部达标" if report.validation.get("all_pass") else "存在不达标"
            print(f"    结论: {verdict}")
    print("=" * 60)


def cmd_include(args):
    """查询 include 依赖"""
    from cpp_semantic_graph.query import IncludeQuery, render_tree

    with IncludeQuery(args.db) as q:
        if args.mode == "direct":
            result = q.get_direct_includers(args.header)
            print(f"直接 include '{args.header}' 的翻译单元 ({len(result)} 个):")
            for f in result:
                print(f"  - {f}")
        elif args.mode == "all":
            result = q.get_all_includers(args.header)
            print(f"所有受 '{args.header}' 影响的翻译单元 ({len(result)} 个, 含间接):")
            for f in result:
                print(f"  - {f}")
        elif args.mode == "tree":
            tree = q.get_include_tree(args.header, skip_system=args.skip_system)
            print(f"'{tree.file}' 的 include 树:")
            print(render_tree(tree))


def cmd_incremental(args):
    """增量更新语义图谱"""
    from cpp_semantic_graph.incremental_updater import IncrementalUpdater

    config_path = args.config
    if not Path(config_path).exists():
        print(f"错误: 配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)

    updater = IncrementalUpdater(config_path, args.db,
                                 repo_root=args.repo_root)

    files = [f.strip() for f in args.files.split(",")] if args.files else None

    report = updater.run(
        base_ref=args.base if not files else None,
        files=files,
        rebuild_associations=not args.skip_associations,
        rebuild_embeddings=args.embeddings,
        doc_only=args.doc_only,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("\n" + "=" * 60)
        print("Dry Run: 仅检测和分析（不修改数据库）")
        print("=" * 60)
        print(f"  变更文件: {report.files_changed}")
        print(f"  受影响 TU: {report.tus_affected}")
        if report.impact_chain:
            print("\n  影响链:")
            for changed, tus in report.impact_chain.items():
                print(f"    {changed} → {len(tus)} 个 TU")
                for tu in tus[:5]:
                    print(f"      - {tu}")
                if len(tus) > 5:
                    print(f"      ... 共 {len(tus)} 个")
        if report.skipped:
            print(f"\n  跳过: {len(report.skipped)} 个文件")
            for s in report.skipped[:5]:
                print(f"    - {s}")
        print("=" * 60)
        return

    print("\n" + "=" * 60)
    print("增量更新报告")
    print("=" * 60)
    print(f"  变更文件:   {report.files_changed}")
    print(f"  受影响 TU:  {report.tus_affected}")
    print(f"  成功/失败:   {report.tus_reparsed} / {report.tus_failed}")
    if report.failed_files:
        for f in report.failed_files[:10]:
            print(f"    - {f}")
    print(f"  删除旧边:   {report.edges_deleted}")
    print(f"  入库:       +{report.nodes_new} 节点 "
          f"(更新 {report.nodes_updated}), "
          f"+{report.edges_new} 边, +{report.includes_new} includes")
    if report.nodes_removed:
        print(f"  清理残留:   {report.nodes_removed} 节点")
    if report.docs_updated or report.doc_sections_new or report.doc_sections_updated:
        print(f"  文档入库:   {report.docs_updated} 文件 "
              f"(+{report.doc_sections_new} 新切片, "
              f"{report.doc_sections_updated} 更新切片)")
    print(f"  关联重建:   {'是' if report.associations_rebuilt else '否'}")
    print(f"  数据库:     {report.db_node_count} 节点 / "
          f"{report.db_edge_count} 边")
    print(f"  耗时:       {report.elapsed_seconds:.1f}s")
    print("=" * 60)


# ======================================================================
# CLI 定义
# ======================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpp_semantic_graph",
        description="C++ 语义图谱查询工具",
    )
    parser.add_argument(
        "--db", "-d",
        default="semantic_graph_full.db",
        help="SQLite 数据库路径 (默认: semantic_graph_full.db)",
    )

    sub = parser.add_subparsers(dest="command", help="子命令")

    # search-class
    p = sub.add_parser("search-class", help="按类名搜索")
    p.add_argument("name", help="类名")
    p.add_argument("--exact", action="store_true", help="精确匹配（默认模糊匹配）")
    p.set_defaults(func=cmd_search_class)

    # inheritance
    p = sub.add_parser("inheritance", help="查询继承关系")
    p.add_argument("class_name", help="类名")
    p.add_argument("--direction", "-D", default="down", choices=["up", "down"],
                    help="查询方向: up=父类, down=子类 (默认: down)")
    p.add_argument("--depth", type=int, default=1,
                    help="递归深度 (1=直接, -1=全部, 默认: 1)")
    p.set_defaults(func=cmd_inheritance)

    # search-func
    p = sub.add_parser("search-func", help="按函数名搜索")
    p.add_argument("name", help="函数名")
    p.add_argument("--class-name", "-c", help="限定所属类名")
    p.set_defaults(func=cmd_search_func)

    # file-symbols
    p = sub.add_parser("file-symbols", help="查询文件内符号")
    p.add_argument("file_path", help="文件路径（支持部分匹配）")
    p.set_defaults(func=cmd_file_symbols)

    # callers
    p = sub.add_parser("callers", help="查询谁调用了指定函数（影响面分析）")
    p.add_argument("name", help="被调用方函数名")
    p.add_argument("--class-name", "-c", help="限定所属类名")
    p.set_defaults(func=cmd_callers)

    # callees
    p = sub.add_parser("callees", help="查询指定函数调用了谁（调用链分析）")
    p.add_argument("name", help="调用方函数名")
    p.add_argument("--class-name", "-c", help="限定所属类名")
    p.set_defaults(func=cmd_callees)

    # overrides
    p = sub.add_parser("overrides", help="查询虚函数的所有重写实现")
    p.add_argument("name", help="虚函数名")
    p.add_argument("--class-name", "-c", required=True,
                   help="声明该虚函数的基类名（必填）")
    p.set_defaults(func=cmd_overrides)

    # traverse
    p = sub.add_parser("traverse", help="多跳遍历图谱（影响面分析）")
    p.add_argument("start", help="起始节点名称")
    p.add_argument("--relation-types", "-r", default=None,
                   help="逗号分隔的关系类型列表（如 inherits_public,calls_direct），默认全部")
    p.add_argument("--direction", "-D", default="outgoing",
                   choices=["outgoing", "incoming"],
                   help="遍历方向（默认 outgoing）")
    p.add_argument("--depth", type=int, default=3, help="最大遍历深度（默认 3）")
    p.add_argument("--mode", default="bfs", choices=["bfs", "dfs"],
                   help="遍历模式（默认 bfs）")
    p.add_argument("--max-results", type=int, default=50,
                   help="最大返回节点数（默认 50）")
    p.set_defaults(func=cmd_traverse)

    # search-docs
    p = sub.add_parser("search-docs", help="搜索项目文档")
    p.add_argument("keyword", help="搜索关键词")
    p.add_argument("--tag", "-t", help="按标签过滤（可选）")
    p.add_argument("--max-results", type=int, default=10, help="最大返回数（默认 10）")
    p.add_argument("--min-confidence", type=float, default=0.0,
                   help="关联代码最低置信度（0=不过滤，默认 0）")
    p.set_defaults(func=cmd_search_docs)

    # import
    p = sub.add_parser("import", help="导入 JSON 到数据库")
    p.add_argument("json_path", help="JSON 文件或目录路径")
    p.add_argument("--verbose", "-v", action="store_true", help="输出每个文件详情")
    p.set_defaults(func=cmd_import)

    # stats
    p = sub.add_parser("stats", help="数据库统计信息")
    p.set_defaults(func=cmd_stats)

    # full-parse
    p = sub.add_parser("full-parse", help="端到端全量解析（解析→入库→验证）")
    p.add_argument("--config", "-c", default="cpp_semantic_graph.yaml",
                   help="配置文件路径 (默认: cpp_semantic_graph.yaml)")
    p.add_argument("--filter", "-f", default=None,
                   help="只解析路径含此串的翻译单元 (如 hq_ota_service)")
    p.add_argument("--include-generated", action="store_true",
                   help="包含生成代码 (src-gen)")
    p.add_argument("--no-reset", action="store_true",
                   help="不清空已有数据库，增量追加")
    p.add_argument("--validate", action="store_true",
                   help="解析后运行正确性验证")
    p.add_argument("--baseline", default=None,
                   help="ground truth 路径 (--validate 时需要)")
    p.set_defaults(func=cmd_full_parse)

    # include
    p = sub.add_parser("include", help="查询 include 依赖")
    p.add_argument("header", help="头文件路径 (部分匹配)")
    p.add_argument("--mode", "-m", default="direct",
                   choices=["direct", "all", "tree"],
                   help="direct=直接includer, all=递归全部, tree=include树 (默认: direct)")
    p.add_argument("--skip-system", action="store_true", help="tree 模式跳过系统头")
    p.set_defaults(func=cmd_include)

    # incremental
    p = sub.add_parser("incremental", help="增量更新图谱（基于 include 依赖图）")
    p.add_argument("--config", "-c", default="cpp_semantic_graph.yaml",
                   help="配置文件路径 (默认: cpp_semantic_graph.yaml)")
    p.add_argument("--base", default="HEAD~1",
                   help="git diff 基准 ref (默认: HEAD~1)")
    p.add_argument("--files", default=None,
                   help="手动指定变更文件 (逗号分隔，不用 git diff)")
    p.add_argument("--repo-root", default=None,
                   help="git 仓库根目录 (默认: 从配置自动推断)")
    p.add_argument("--skip-associations", action="store_true",
                   help="跳过文档关联边重建")
    p.add_argument("--embeddings", action="store_true",
                   help="重建 embedding 关联 (慢)")
    p.add_argument("--doc-only", action="store_true",
                   help="仅增量入库文档变更（不解析 C++ 代码）")
    p.add_argument("--dry-run", action="store_true",
                   help="仅检测和分析，不执行更新")
    p.set_defaults(func=cmd_incremental)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
