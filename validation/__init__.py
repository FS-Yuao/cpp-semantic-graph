"""准确性验证包

用 clangd MCP 采集的 ground truth（clangd_baseline.json）对比图谱查询结果，
量化各维度的 Precision / Recall。

入口:
  python -m validation.run_accuracy_validation

组件:
  - ClangdBaseline: 加载 baseline.json 的 ground truth 数据模型
  - AccuracyValidator: 4 维度自动对比，计算 TP/FP/FN 与 P/R
  - ReportGenerator: 生成 Markdown 验证报告
"""

from .clangd_baseline import ClangdBaseline, BaselineClass, BaselineFunction, BaselineCallRef
from .accuracy_validator import AccuracyValidator, DimensionResult, MatchDetail
from .report_generator import ReportGenerator

__all__ = [
    "ClangdBaseline", "BaselineClass", "BaselineFunction", "BaselineCallRef",
    "AccuracyValidator", "DimensionResult", "MatchDetail",
    "ReportGenerator",
]
