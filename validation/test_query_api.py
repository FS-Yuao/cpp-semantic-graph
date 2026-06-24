#!/usr/bin/env python3
"""task 1-3 端到端验证：解析 → 导入 → 查询，验证 4 个核心检索 API。

跑核心模块（soc/gnss/switch/mcu update）的 AST 解析，生成中间 JSON，
导入 SQLite，再用 GraphQuery 跑 4 个查询接口，校验验收标准。

用法:
  cd _tools/cpp_semantic_graph
  python -m validation.test_query_api
"""

import json
import sys
import time
from pathlib import Path

# 确保包可导入（脚本位于 cpp_semantic_graph/validation/，需把 _tools 加入 sys.path）
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent.parent))

from cpp_semantic_graph.parser.config import ProjectConfig
from cpp_semantic_graph.parser.compile_db import CompileDB
from cpp_semantic_graph.parser.ast_visitor import SemanticExtractor
from cpp_semantic_graph.db.importer import Importer
from cpp_semantic_graph.query import GraphQuery

ROOT = _HERE.parent
YAML = ROOT / "cpp_semantic_graph.yaml"
OUTPUT_DIR = ROOT / "output"
DB_PATH = ROOT / "semantic_graph.db"

# 核心模块筛选关键词（compile_commands 中 hq_ota_service 的 peri_update + peri_manger）
# peri_manger 含 peri_adapter.cpp（PerformUpgrade 的调用方），验证调用关系维度需要
CORE_FILTERS = ["peri_update/soc", "peri_update/gnss",
                "peri_update/switch", "peri_update/mcu",
                "peri_manger"]


def run_parse(extractor, entries):
    """解析所有编译单元，保存 JSON，返回 ParseResult 列表"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    results = []
    for entry in entries:
        r = extractor.parse(entry)
        data = {
            "source_path": r.source_path,
            "status": r.status,
            "error_message": r.error_message,
            "nodes": [n.to_dict() for n in r.nodes],
            "edges": [e.to_dict() for e in r.edges],
            "includes": [i.to_dict() for i in r.includes],
        }
        out = OUTPUT_DIR / (Path(entry.file).stem + ".json")
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"  parse {Path(entry.file).name}: status={r.status}, "
              f"nodes={r.node_count}, edges={r.edge_count}, includes={r.include_count}")
        results.append(r)
    return results


def timed(fn, label):
    t = time.perf_counter()
    r = fn()
    return r, (time.perf_counter() - t) * 1000


def main():
    config = ProjectConfig.from_yaml(str(YAML))
    cdb = CompileDB(config.compile_commands)
    print(f"compile_commands: {cdb.total_count} entries, "
          f"{cdb.source_count} source, {cdb.generated_count} generated")

    # 筛选核心模块编译单元
    entries = []
    for f in CORE_FILTERS:
        entries.extend(cdb.get_entries(filter_path=f, include_generated=False))
    seen = set()
    entries = [e for e in entries if not (e.file in seen or seen.add(e.file))]
    print(f"\n核心模块编译单元: {len(entries)}")
    for e in entries:
        print(f"  - {e.file}")

    # [1] 解析 AST → JSON
    print("\n[1] 解析 AST → JSON")
    extractor = SemanticExtractor(config)
    results = run_parse(extractor, entries)

    # [2] 导入 JSON → SQLite
    print("\n[2] 导入 → SQLite")
    if DB_PATH.exists():
        DB_PATH.unlink()
    with Importer(str(DB_PATH)) as imp:
        stats = imp.import_results(results)
        print(f"  nodes_new={stats['nodes_new']}, edges_new={stats['edges_new']}, "
              f"includes_new={stats['includes_new']}")

    # [3] 查询验证
    print("\n[3] 查询验证")
    timings = {}
    with GraphQuery(str(DB_PATH)) as q:
        # search_class
        classes, timings["search_class"] = timed(
            lambda: q.search_class("BasePeriUpdate"), "search_class")
        print(f"\n  search_class('BasePeriUpdate') [{timings['search_class']:.2f}ms]: "
              f"{len(classes)} 个")
        for c in classes:
            print(f"    {c.namespace}::{c.name}  abstract={c.is_abstract}  "
                  f"[{c.file_path}:{c.start_line}-{c.end_line}]")

        # get_inheritance down
        inh, timings["inheritance"] = timed(
            lambda: q.get_inheritance("BasePeriUpdate", direction="down", depth=1),
            "inheritance")
        print(f"\n  get_inheritance('BasePeriUpdate', down) "
              f"[{timings['inheritance']:.2f}ms]: {len(inh)} 条")
        for i in inh:
            print(f"    {i.child.name} --{i.access}--> {i.parent.name}  "
                  f"(is_virtual={i.is_virtual})")

        # search_function
        funcs, timings["search_function"] = timed(
            lambda: q.search_function("PerformUpgrade"), "search_function")
        print(f"\n  search_function('PerformUpgrade') "
              f"[{timings['search_function']:.2f}ms]: {len(funcs)} 个")
        for f in funcs:
            print(f"    {f.class_name}::{f.name}  virtual={f.is_virtual} "
                  f"override={f.is_override} pure={f.is_pure_virtual} static={f.is_static}")
            print(f"      sig: {f.signature}")
            print(f"      loc: {f.file_path}:{f.start_line}")

        # get_file_symbols
        syms, timings["file_symbols"] = timed(
            lambda: q.get_file_symbols("soc_update.cpp"), "file_symbols")
        print(f"\n  get_file_symbols('soc_update.cpp') "
              f"[{timings['file_symbols']:.2f}ms]: {len(syms)} 符号")
        for s in syms:
            print(f"    [{s.node_type}] {s.namespace}::{s.name}  "
                  f"{s.file_path}:{s.start_line}-{s.end_line}")

        # 验收断言
        print("\n[4] 验收断言")
        child_names = {i.child.name for i in inh}
        expected = {"SocUpdate", "GnssUpdate", "SwitchUpdate", "McuUpdate"}
        checks = [
            ("search_class 返回 BasePeriUpdate",
             any(c.name == "BasePeriUpdate" for c in classes)),
            ("inheritance down 返回 4 个子类", child_names == expected),
            ("search_function 返回 PerformUpgrade",
             any(f.name == "PerformUpgrade" for f in funcs)),
            ("file_symbols 返回 soc_update 符号", len(syms) > 0),
            ("查询均 < 10ms", all(v < 10 for v in timings.values())),
        ]
        for label, ok in checks:
            print(f"  [{'✓' if ok else '✗'}] {label}")


if __name__ == "__main__":
    main()
