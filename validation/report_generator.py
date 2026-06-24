"""验证报告生成器

将 AccuracyValidator 的结果输出为 Markdown 报告：
- 汇总表（各维度 P/R + 是否达标）
- 逐条对比详情（TP/FP/FN）
- 不达标维度原因分析
"""

from dataclasses import asdict
from pathlib import Path

from .accuracy_validator import DimensionResult


class ReportGenerator:
    """生成 Markdown 验证报告"""

    def __init__(self, results: list[DimensionResult], db_path: str):
        self.results = results
        self.db_path = db_path

    def generate(self) -> str:
        """生成完整 Markdown 报告"""
        lines = []
        lines.append("# 准确性验证报告（clangd Ground Truth 对比）\n")
        lines.append(f"**数据库**: `{self.db_path}`\n")
        lines.append(f"**对比基准**: clangd MCP 采集，固化于 `clangd_baseline.json`\n")
        lines.append("\n## 1. 汇总\n")
        lines.append(self._summary_table())

        lines.append("\n## 2. 逐维度对比详情\n")
        for res in self.results:
            lines.append(self._dimension_section(res))

        lines.append("\n## 3. 不达标维度分析\n")
        lines.append(self._failure_analysis())

        return "\n".join(lines)

    def write(self, out_path: str | Path):
        """写报告到文件"""
        Path(out_path).write_text(self.generate(), encoding="utf-8")

    # ------------------------------------------------------------------

    def _summary_table(self) -> str:
        lines = [
            "| 维度 | TP | FP | FN | Precision | Recall | 门限(P/R) | 结果 |",
            "|------|----|----|----|-----------|--------|-----------|------|",
        ]
        for res in self.results:
            p_min = res._p_min
            r_min = res._r_min
            status = "✅ 达标" if res.pass_ else "❌ 不达标"
            lines.append(
                f"| {res.name} | {res.tp} | {res.fp} | {res.fn} | "
                f"{res.precision:.1%} | {res.recall:.1%} | "
                f"{p_min:.0%}/{r_min:.0%} | {status} |"
            )
        # 总体
        all_pass = all(r.pass_ for r in self.results)
        verdict = "**全部达标，可进入下一阶段**" if all_pass else "**存在不达标维度，需修复**"
        lines.append(f"\n**总体结论**: {verdict}")
        return "\n".join(lines)

    def _dimension_section(self, res: DimensionResult) -> str:
        lines = [f"### {res.name}\n"]
        lines.append(f"- Precision: {res.precision:.1%} (门限 {res._p_min:.0%})")
        lines.append(f"- Recall: {res.recall:.1%} (门限 {res._r_min:.0%})\n")
        lines.append("| 样本 | 期望 | 图谱返回 | TP | FP | FN | 备注 |")
        lines.append("|------|------|---------|----|----|----|------|")
        for d in res.details:
            lines.append(
                f"| {d.sample} | {', '.join(d.expected) or '∅'} | "
                f"{', '.join(d.actual) or '∅'} | "
                f"{', '.join(d.tp) or '∅'} | {', '.join(d.fp) or '∅'} | "
                f"{', '.join(d.fn) or '∅'} | {d.note} |"
            )
        return "\n".join(lines) + "\n"

    def _failure_analysis(self) -> str:
        failed = [r for r in self.results if not r.pass_]
        if not failed:
            return "全部维度达标，无需修复。\n"

        lines = []
        for res in failed:
            lines.append(f"### {res.name}（P={res.precision:.1%} R={res.recall:.1%}）\n")
            # FP：图谱多返回的
            fp_items = [(d.sample, d.fp) for d in res.details if d.fp]
            if fp_items:
                lines.append("**FP（图谱多返回）**:")
                for sample, fp in fp_items:
                    lines.append(f"- `{sample}`: {fp}")
            # FN：图谱漏掉的
            fn_items = [(d.sample, d.fn) for d in res.details if d.fn]
            if fn_items:
                lines.append("\n**FN（图谱漏掉）**:")
                for sample, fn in fn_items:
                    lines.append(f"- `{sample}`: {fn}")
            lines.append("")
        return "\n".join(lines)
