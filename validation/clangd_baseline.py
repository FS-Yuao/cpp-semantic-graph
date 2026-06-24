"""clangd ground truth 加载与数据模型

ground truth 由会话内用 clangd MCP 工具人工采集，固化为 clangd_baseline.json。
本模块负责加载 baseline.json 并提供结构化访问接口。

为何不在此处直接调 clangd：
- clangd MCP 是 Claude 会话工具，Python 进程无法直接调用
- 实测 clangd 部分工具不可用：find_implementations（虚函数 override 返回空）、
  get_callees（method not found），调用关系维度改用 find_references 的引用集合
- 固化 JSON 可版本化、可复跑，符合"可持续运行验证机制"的要求
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BaselineClass:
    """类 ground truth"""
    name: str
    namespace: str
    file: str
    start_line: int
    is_abstract: bool
    supertypes: list[dict] = field(default_factory=list)   # 父类
    subtypes: list[dict] = field(default_factory=list)     # 子类


@dataclass
class BaselineFunction:
    """函数 ground truth"""
    name: str
    owner_class: str
    namespace: str
    signature_normalized: str
    return_type: str
    is_virtual: bool
    is_pure_virtual: bool
    is_override: bool
    declaration: str | None        # "file:line"
    definition: str | None         # "file:line" 或 None


@dataclass
class BaselineCallRef:
    """调用引用 ground truth（基于 find_references）"""
    symbol: str
    owner_class: str
    references: list[dict]         # [{file, line, kind}]


@dataclass
class Thresholds:
    """各维度门限"""
    class_definition: dict
    inheritance: dict
    function_signature: dict
    call_relation: dict


@dataclass
class ClangdBaseline:
    """clangd ground truth 集合"""
    classes: list[BaselineClass]
    functions: list[BaselineFunction]
    call_refs: list[BaselineCallRef]
    thresholds: Thresholds

    @classmethod
    def load(cls, json_path: str | Path) -> "ClangdBaseline":
        """从 baseline.json 加载"""
        path = Path(json_path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        classes = [BaselineClass(
            name=c["name"],
            namespace=c["namespace"],
            file=c["file"],
            start_line=c["start_line"],
            is_abstract=c["is_abstract"],
            supertypes=c.get("supertypes", []),
            subtypes=c.get("subtypes", []),
        ) for c in data.get("classes", [])]

        functions = [BaselineFunction(
            name=f["name"],
            owner_class=f["owner_class"],
            namespace=f["namespace"],
            signature_normalized=f["signature_normalized"],
            return_type=f["return_type"],
            is_virtual=f["is_virtual"],
            is_pure_virtual=f["is_pure_virtual"],
            is_override=f["is_override"],
            declaration=f.get("declaration"),
            definition=f.get("definition"),
        ) for f in data.get("functions", [])]

        call_refs = [BaselineCallRef(
            symbol=r["symbol"],
            owner_class=r["owner_class"],
            references=r.get("references", []),
        ) for r in data.get("call_references", [])]

        th = data.get("thresholds", {})
        thresholds = Thresholds(
            class_definition=th.get("class_definition", {}),
            inheritance=th.get("inheritance", {}),
            function_signature=th.get("function_signature", {}),
            call_relation=th.get("call_relation", {}),
        )

        return cls(classes=classes, functions=functions,
                   call_refs=call_refs, thresholds=thresholds)

    def find_class(self, name: str) -> BaselineClass | None:
        for c in self.classes:
            if c.name == name:
                return c
        return None

    def find_functions(self, name: str) -> list[BaselineFunction]:
        return [f for f in self.functions if f.name == name]
