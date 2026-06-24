"""准确性验证主逻辑

用 clangd ground truth（baseline.json）对比图谱查询结果，计算 Precision/Recall。

4 个对比维度（clangd 工具能力决定可用维度）:
1. 类定义    — search_class vs baseline.classes
2. 继承关系  — get_inheritance vs baseline.classes[].subtypes/supertypes
3. 函数签名  — search_function vs baseline.functions（归一化签名 + 属性）
4. 调用关系  — 图谱 call 边涉及的文件 vs baseline.call_refs[].references

对比指标:
  Precision = TP / (TP + FP)   图谱返回中正确的比例
  Recall    = TP / (TP + FN)   ground truth 中图谱覆盖的比例
"""

import re
from dataclasses import dataclass, field

from .clangd_baseline import ClangdBaseline, BaselineClass, BaselineFunction
from ..query import GraphQuery


@dataclass
class MatchDetail:
    """单条对比详情"""
    sample: str               # 样本标识
    expected: list[str]       # ground truth 集合
    actual: list[str]         # 图谱返回集合
    tp: list[str] = field(default_factory=list)
    fp: list[str] = field(default_factory=list)
    fn: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class DimensionResult:
    """单个维度的汇总结果"""
    name: str
    details: list[MatchDetail] = field(default_factory=list)
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 1.0

    @property
    def pass_(self) -> bool:
        return self.precision >= self._p_min and self.recall >= self._r_min

    # 门限由 validator 注入
    _p_min: float = 0.0
    _r_min: float = 0.0


class AccuracyValidator:
    """准确性验证器"""

    def __init__(self, db_path: str, baseline: ClangdBaseline):
        self.q = GraphQuery(db_path)
        self.baseline = baseline

    def close(self):
        self.q.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # 维度 1：类定义
    # ------------------------------------------------------------------

    def validate_class_definition(self) -> DimensionResult:
        """对比 search_class 与 baseline 类定义"""
        res = DimensionResult(name="类定义")
        res._p_min = self.baseline.thresholds.class_definition.get("precision_min", 0.98)
        res._r_min = self.baseline.thresholds.class_definition.get("recall_min", 0.95)

        for bc in self.baseline.classes:
            classes = self.q.search_class(bc.name, exact=True)
            # 图谱返回的类名集合（去重，因 search_class 可能返回 struct）
            actual_names = sorted({c.name for c in classes})
            expected_names = [bc.name]

            tp = [n for n in actual_names if n in expected_names]
            fp = [n for n in actual_names if n not in expected_names]
            fn = [n for n in expected_names if n not in actual_names]

            # 文件路径校验（命中类的文件是否与 baseline 一致）
            file_note = ""
            hit = next((c for c in classes if c.name == bc.name), None)
            if hit:
                hit_file = hit.file_path.replace("\\", "/")
                if bc.file not in hit_file and hit_file not in bc.file:
                    file_note = f"文件不一致: 图谱={hit.file_path} baseline={bc.file}"

            d = MatchDetail(
                sample=f"class:{bc.name}",
                expected=expected_names,
                actual=actual_names,
                tp=tp, fp=fp, fn=fn,
                note=file_note + (f" abstract={hit.is_abstract}" if hit else " 未命中"),
            )
            res.details.append(d)
            res.tp += len(tp)
            res.fp += len(fp)
            res.fn += len(fn)

        return res

    # ------------------------------------------------------------------
    # 维度 2：继承关系
    # ------------------------------------------------------------------

    def validate_inheritance(self) -> DimensionResult:
        """对比 get_inheritance 与 baseline 继承关系"""
        res = DimensionResult(name="继承关系")
        res._p_min = self.baseline.thresholds.inheritance.get("precision_min", 0.95)
        res._r_min = self.baseline.thresholds.inheritance.get("recall_min", 0.90)

        for bc in self.baseline.classes:
            # 子类（down）
            if bc.subtypes:
                inh = self.q.get_inheritance(bc.name, direction="down", depth=1)
                actual = sorted({i.child.name for i in inh})
                expected = sorted({s["name"] for s in bc.subtypes})
                self._record(res, f"inherit_down:{bc.name}", actual, expected)

            # 父类（up）
            if bc.supertypes:
                inh = self.q.get_inheritance(bc.name, direction="up", depth=1)
                actual = sorted({i.parent.name for i in inh})
                expected = sorted({s["name"] for s in bc.supertypes})
                self._record(res, f"inherit_up:{bc.name}", actual, expected)

        return res

    # ------------------------------------------------------------------
    # 维度 3：函数签名
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_sig(sig: str) -> str:
        """归一化签名，便于跨工具对比

        clangd: 'bool SocUpdate::PerformUpgrade()'  /  'public: bool SocUpdate::PerformUpgrade()'
        图谱:   'bool PerformUpgrade() override'   /  'bool PerformUpgrade() = 0'
        归一化: 去类名前缀、去 override/=0/修饰符，保留 '返回类型 函数名(参数)'
        """
        if not sig:
            return ""
        s = sig.replace("public:", "").replace("private:", "").replace("protected:", "")
        s = re.sub(r"\b\w+::", "", s)              # 去类名前缀 SocUpdate::
        s = s.replace(" override", "").replace("= 0", "").replace("=0", "")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def validate_function_signature(self) -> DimensionResult:
        """对比 search_function 与 baseline 函数签名"""
        res = DimensionResult(name="函数签名")
        res._p_min = self.baseline.thresholds.function_signature.get("precision_min", 0.95)
        res._r_min = self.baseline.thresholds.function_signature.get("recall_min", 0.90)

        # 按 (owner_class, name) 分组 baseline
        for bf in self.baseline.functions:
            funcs = self.q.search_function(bf.name)
            # 限定所属类
            matched = [f for f in funcs if f.class_name == bf.owner_class]

            # 签名归一化对比
            expected_sig = self._normalize_sig(bf.signature_normalized)
            actual_sigs = sorted({self._normalize_sig(f.signature) for f in matched})

            tp, fp, fn = [], [], []
            for s in actual_sigs:
                (tp if s == expected_sig else fp).append(s)
            if expected_sig and expected_sig not in actual_sigs:
                fn.append(expected_sig)

            # 属性对比（virtual/override/pure）
            attr_note = ""
            if matched:
                f = matched[0]
                attr_mismatch = []
                if f.is_virtual != bf.is_virtual:
                    attr_mismatch.append(f"virtual 图谱={f.is_virtual}/GT={bf.is_virtual}")
                if f.is_override != bf.is_override:
                    attr_mismatch.append(f"override 图谱={f.is_override}/GT={bf.is_override}")
                if f.is_pure_virtual != bf.is_pure_virtual:
                    attr_mismatch.append(f"pure 图谱={f.is_pure_virtual}/GT={bf.is_pure_virtual}")
                attr_note = "; ".join(attr_mismatch) if attr_mismatch else "属性一致"

            res.details.append(MatchDetail(
                sample=f"func:{bf.owner_class}::{bf.name}",
                expected=[expected_sig],
                actual=actual_sigs,
                tp=tp, fp=fp, fn=fn,
                note=attr_note or "未命中",
            ))
            res.tp += len(tp)
            res.fp += len(fp)
            res.fn += len(fn)

        return res

    # ------------------------------------------------------------------
    # 维度 4：调用关系（基于 find_references 引用文件集合）
    # ------------------------------------------------------------------

    def validate_call_relation(self) -> DimensionResult:
        """对比图谱调用边涉及的文件 vs baseline call_refs 引用文件"""
        res = DimensionResult(name="调用关系")
        res._p_min = self.baseline.thresholds.call_relation.get("precision_min", 0.85)
        res._r_min = self.baseline.thresholds.call_relation.get("recall_min", 0.80)

        from ..db.relation_types import RelationType
        call_types = [rt.value for rt in RelationType.call_types()]

        for ref in self.baseline.call_refs:
            # 图谱侧：找该函数的所有节点（声明+定义可能多个），汇总入边涉及的文件
            funcs = self.q.search_function(ref.symbol)
            actual_callers = set()      # 调用方文件
            for f in funcs:
                if f.class_name != ref.owner_class:
                    continue
                # 查指向该函数的调用边（谁调用它）
                # to_id 可能匹配声明节点或定义节点，按 unique_key 精确查
                rows = self.q.db.conn.execute(
                    "SELECT DISTINCT n.file_path FROM edge e "
                    "JOIN node n ON e.from_id=n.id "
                    "WHERE e.to_id=(SELECT id FROM node WHERE unique_key=?) "
                    "AND e.relation_type IN ({})".format(
                        ",".join("?" * len(call_types))),
                    (f.unique_key, *call_types)
                ).fetchall()
                actual_callers.update(self._norm_file(r["file_path"]) for r in rows)

            # baseline 侧：call kind 的引用文件（归一化为文件名+关键路径段）
            expected_callers = sorted({
                self._norm_file(r["file"])
                for r in ref.references if r.get("kind") == "call"
            })
            actual = sorted(actual_callers)

            self._record(res, f"call:{ref.owner_class}::{ref.symbol}",
                         actual, expected_callers,
                         note="对比调用方文件集合（find_references 的 call 引用）")

        return res

    @staticmethod
    def _norm_file(path: str) -> str:
        """归一化文件路径，消除前缀差异

        图谱存相对路径如 'peri_update/soc/soc_update.cpp'
        baseline 存 'hq_ota_service/src/peri_update/soc/soc_update.cpp'
        归一化为最后两段路径，保证可比。
        """
        if not path:
            return ""
        p = path.replace("\\", "/")
        # 用文件名 + 父目录作为归一化键（足以区分 soc/gnss/switch/mcu）
        parts = [x for x in p.split("/") if x]
        if len(parts) >= 2:
            return "/".join(parts[-2:])
        return p

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------

    def run_all(self) -> list[DimensionResult]:
        """运行全部维度"""
        return [
            self.validate_class_definition(),
            self.validate_inheritance(),
            self.validate_function_signature(),
            self.validate_call_relation(),
        ]

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _record(res: DimensionResult, sample: str,
                actual: list[str], expected: list[str], note: str = ""):
        """记录一次集合对比的 TP/FP/FN"""
        tp = [x for x in actual if x in expected]
        fp = [x for x in actual if x not in expected]
        fn = [x for x in expected if x not in actual]
        res.details.append(MatchDetail(
            sample=sample, expected=expected, actual=actual,
            tp=tp, fp=fp, fn=fn, note=note,
        ))
        res.tp += len(tp)
        res.fp += len(fp)
        res.fn += len(fn)
