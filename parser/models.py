"""
提取结果数据模型

定义 AST visitor 输出的标准化数据结构
"""

from dataclasses import dataclass, field
from enum import Enum


class NodeType(str, Enum):
    CLASS = "class"
    STRUCT = "struct"
    FUNCTION = "function"
    FILE = "file"
    DOC_SECTION = "doc_section"


class RelationType(str, Enum):
    # 继承关系
    INHERITS_PUBLIC = "inherits_public"
    INHERITS_PROTECTED = "inherits_protected"
    INHERITS_PRIVATE = "inherits_private"

    # 函数关系
    OVERRIDES = "overrides"
    HIDES = "hides"
    BELONGS_TO = "belongs_to"

    # 调用关系
    CALLS_DIRECT = "calls_direct"
    CALLS_VIRTUAL = "calls_virtual"
    CALLS_CALLBACK = "calls_callback"

    # 模板关系
    INSTANTIATES = "instantiates"
    TYPE_ALIAS = "type_alias"
    USING_DECL = "using_decl"

    # 其他
    FRIEND_OF = "friend_of"

    # 文档关系
    DOC_DESCRIBES_CODE = "doc_describes_code"
    CODE_REFERS_TO_DOC = "code_refers_to_doc"


@dataclass
class NodeInfo:
    """图谱节点"""
    type: NodeType
    name: str
    namespace: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    extra_info: dict = field(default_factory=dict)
    unique_key: str = ""

    def __post_init__(self):
        if not self.unique_key:
            self.unique_key = f"{self.type.value}|{self.namespace}|{self.name}|{self.file_path}"

    def to_dict(self) -> dict:
        return {
            "type": self.type.value if isinstance(self.type, NodeType) else self.type,
            "name": self.name,
            "namespace": self.namespace,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "extra_info": self.extra_info,
            "unique_key": self.unique_key,
        }


@dataclass
class EdgeInfo:
    """图谱边"""
    relation_type: RelationType
    from_unique_key: str = ""
    to_unique_key: str = ""
    from_id: int = 0
    to_id: int = 0
    extra_info: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "relation_type": self.relation_type.value if isinstance(self.relation_type, RelationType) else self.relation_type,
            "from_unique_key": self.from_unique_key,
            "to_unique_key": self.to_unique_key,
            "extra_info": self.extra_info,
        }


@dataclass
class IncludeDep:
    """include 依赖"""
    source_file: str
    included_file: str
    is_system: bool = False

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "included_file": self.included_file,
            "is_system": self.is_system,
        }


@dataclass
class ParseResult:
    """单个翻译单元的解析结果"""
    source_path: str
    status: str = "success"  # success / partial / failed
    error_message: str = ""
    nodes: list[NodeInfo] = field(default_factory=list)
    edges: list[EdgeInfo] = field(default_factory=list)
    includes: list[IncludeDep] = field(default_factory=list)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    @property
    def include_count(self) -> int:
        return len(self.includes)

    def merge(self, other: "ParseResult"):
        """合并另一个解析结果（用于多翻译单元聚合）"""
        self.nodes.extend(other.nodes)
        self.edges.extend(other.edges)
        self.includes.extend(other.includes)
        if other.status == "failed":
            self.status = "partial"
