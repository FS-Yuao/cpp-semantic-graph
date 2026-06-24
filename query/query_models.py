"""
查询结果数据模型

将数据库原始行转为结构化结果，提供面向业务的语义字段。
"""

from dataclasses import dataclass, field


@dataclass
class ClassInfo:
    """类搜索结果"""
    name: str
    namespace: str
    file_path: str
    start_line: int
    end_line: int
    is_abstract: bool = False
    template_params: list[str] | None = None
    access: str = "public"
    unique_key: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "is_abstract": self.is_abstract,
            "template_params": self.template_params,
            "access": self.access,
            "unique_key": self.unique_key,
        }


@dataclass
class InheritanceInfo:
    """继承关系查询结果"""
    parent: ClassInfo
    child: ClassInfo
    access: str           # public / protected / private
    is_virtual: bool = False

    def to_dict(self) -> dict:
        return {
            "parent": self.parent.to_dict(),
            "child": self.child.to_dict(),
            "access": self.access,
            "is_virtual": self.is_virtual,
        }


@dataclass
class FunctionInfo:
    """函数搜索结果"""
    name: str
    signature: str
    namespace: str
    class_name: str | None       # 所属类名（从 namespace 提取）
    file_path: str
    start_line: int
    end_line: int = 0
    is_virtual: bool = False
    is_override: bool = False
    is_pure_virtual: bool = False
    is_static: bool = False
    is_const: bool = False
    access: str = "public"
    unique_key: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "signature": self.signature,
            "namespace": self.namespace,
            "class_name": self.class_name,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "is_virtual": self.is_virtual,
            "is_override": self.is_override,
            "is_pure_virtual": self.is_pure_virtual,
            "is_static": self.is_static,
            "is_const": self.is_const,
            "access": self.access,
            "unique_key": self.unique_key,
        }


@dataclass
class SymbolInfo:
    """文件内符号（类或函数）"""
    node_type: str       # class / struct / function
    name: str
    namespace: str
    file_path: str
    start_line: int
    end_line: int
    extra: dict = field(default_factory=dict)
    unique_key: str = ""

    def to_dict(self) -> dict:
        return {
            "node_type": self.node_type,
            "name": self.name,
            "namespace": self.namespace,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "extra": self.extra,
            "unique_key": self.unique_key,
        }
