"""
C++ 语义图谱 CLI 工具

提供 4 个核心查询命令:
  search-class   按类名搜索
  inheritance    查询继承关系
  search-func    按函数名搜索
  file-symbols   查文件内符号

另有:
  import         批量导入 JSON 到 SQLite
  stats          查看数据库统计

用法:
  python -m cpp_semantic_graph search-class "SocUpdate"
  python -m cpp_semantic_graph inheritance "BasePeriUpdate" --direction down
  python -m cpp_semantic_graph search-func "PerformUpgrade"
  python -m cpp_semantic_graph file-symbols "soc_update.cpp"
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
        default="semantic_graph.db",
        help="SQLite 数据库路径 (默认: semantic_graph.db)",
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
