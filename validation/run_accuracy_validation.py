#!/usr/bin/env python3
"""task 1-4 准确性验证入口：跑 4 维度对比，生成报告。

用法:
  cd _tools/cpp_semantic_graph
  python -m validation.run_accuracy_validation

依赖:
  - semantic_graph.db（task 1-3 已生成）
  - clangd_baseline.json（本目录，clangd 采集的 ground truth）
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent.parent))

from cpp_semantic_graph.validation.clangd_baseline import ClangdBaseline
from cpp_semantic_graph.validation.accuracy_validator import AccuracyValidator
from cpp_semantic_graph.validation.report_generator import ReportGenerator

ROOT = _HERE.parent
DB_PATH = ROOT / "semantic_graph.db"
BASELINE = _HERE / "clangd_baseline.json"
REPORT = _HERE / "accuracy_report.md"


def main():
    if not DB_PATH.exists():
        print(f"错误: 数据库不存在: {DB_PATH}", file=sys.stderr)
        print("请先运行 task 1-3 生成图谱: python validation/test_query_api.py", file=sys.stderr)
        sys.exit(1)
    if not BASELINE.exists():
        print(f"错误: baseline 不存在: {BASELINE}", file=sys.stderr)
        sys.exit(1)

    baseline = ClangdBaseline.load(BASELINE)
    print(f"baseline 加载: {len(baseline.classes)} 类, "
          f"{len(baseline.functions)} 函数, {len(baseline.call_refs)} 调用引用\n")

    with AccuracyValidator(str(DB_PATH), baseline) as v:
        results = v.run_all()

        # 控制台汇总
        print("=" * 64)
        print(f"{'维度':<10} {'TP':>4} {'FP':>4} {'FN':>4} "
              f"{'Precision':>10} {'Recall':>8}  {'门限':>8}  结果")
        print("=" * 64)
        all_pass = True
        for r in results:
            status = "✓" if r.pass_ else "✗"
            if not r.pass_:
                all_pass = False
            print(f"{r.name:<10} {r.tp:>4} {r.fp:>4} {r.fn:>4} "
                  f"{r.precision:>10.1%} {r.recall:>8.1%}  "
                  f"{r._p_min:.0%}/{r._r_min:.0%}  {status}")
        print("=" * 64)
        verdict = "全部达标 ✅" if all_pass else "存在不达标 ❌"
        print(f"总体结论: {verdict}\n")

        # 生成报告
        ReportGenerator(results, str(DB_PATH)).write(REPORT)
        print(f"详细报告已生成: {REPORT}")

    sys.exit(0 if all_pass else 2)


if __name__ == "__main__":
    main()
