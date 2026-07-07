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


def make_func_sig_suffix(param_types: list[str] | None, is_const: bool = False) -> str:
    """构造 function unique_key 的签名后缀，用于区分重载。

    格式: "|<param1,param2,...>[|c]"（const 方法加 c 标记）
    非函数节点不调用此函数（key 无此后缀）。
    节点侧（models.NodeInfo）与提取侧（ast_visitor._make_function_key /
    friend/alias extractor）必须调用此同一函数，保证 caller key 与 node key
    逐字节一致，否则调用边 from/to 解析失配。
    """
    params = param_types or []
    suffix = f"|{','.join(params)}"
    if is_const:
        suffix += "|c"
    return suffix


class RelationType(str, Enum):
    """节点间关系类型枚举（本模块为单一权威来源）

    值同时作为数据库 edge.relation_type 的存储值。
    db/relation_types.py 从此处重导出，勿在两处各自定义（P2-2：消除人工同步）。
    方向约定:
    - inherits: from=子类, to=父类
    - calls:    from=调用方, to=被调用方
    - belongs_to: from=成员, to=所属类
    - overrides: from=派生类函数, to=基类函数
    """
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

    @classmethod
    def inherits_types(cls) -> list["RelationType"]:
        """返回所有继承关系类型"""
        return [cls.INHERITS_PUBLIC, cls.INHERITS_PROTECTED, cls.INHERITS_PRIVATE]

    @classmethod
    def call_types(cls) -> list["RelationType"]:
        """返回所有调用关系类型"""
        return [cls.CALLS_DIRECT, cls.CALLS_VIRTUAL, cls.CALLS_CALLBACK]

    @classmethod
    def doc_types(cls) -> list["RelationType"]:
        """返回所有文档关系类型"""
        return [cls.DOC_DESCRIBES_CODE, cls.CODE_REFERS_TO_DOC]

    @classmethod
    def from_str(cls, value: str) -> "RelationType | None":
        """从字符串值获取枚举，未知值返回 None"""
        try:
            return cls(value)
        except ValueError:
            return None


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
            # function 节点 key 追加参数签名后缀，区分重载（type|ns|name|file|params[|c]）
            # class/struct/doc_section 无后缀，key 格式向后兼容
            sig_suffix = ""
            type_val = self.type.value if isinstance(self.type, NodeType) else self.type
            if type_val == NodeType.FUNCTION.value:
                info = self.extra_info or {}
                sig_suffix = make_func_sig_suffix(
                    info.get("param_types", []), info.get("is_const", False)
                )
            self.unique_key = f"{type_val}|{self.namespace}|{self.name}|{self.file_path}{sig_suffix}"

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
