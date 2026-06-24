"""
关系类型枚举定义

独立模块，方便其他模块按需导入而无需依赖完整的 models.py。
与 models.py 中的 RelationType 保持同步。
"""

from enum import Enum


class RelationType(str, Enum):
    """节点间关系类型枚举

    值同时作为数据库 edge.relation_type 的存储值。
    方向约定:
    - inherits: from=子类, to=父类
    - calls:    from=调用方, to=被调用方
    - belongs_to: from=成员, to=所属类
    - overrides: from=派生类函数, to=基类函数
    """

    # ── 继承关系（区分权限） ──
    INHERITS_PUBLIC = "inherits_public"
    INHERITS_PROTECTED = "inherits_protected"
    INHERITS_PRIVATE = "inherits_private"

    # ── 函数关系 ──
    OVERRIDES = "overrides"           # 虚函数重写
    HIDES = "hides"                   # 同名函数隐藏（非 override）
    BELONGS_TO = "belongs_to"         # 函数/成员属于某个类

    # ── 调用关系 ──
    CALLS_DIRECT = "calls_direct"     # 直接函数调用
    CALLS_VIRTUAL = "calls_virtual"   # 虚函数调用（通过指针/引用）
    CALLS_CALLBACK = "calls_callback" # 回调/函数对象调用

    # ── 模板关系 ──
    INSTANTIATES = "instantiates"     # 模板实例化
    TYPE_ALIAS = "type_alias"         # using / typedef
    USING_DECL = "using_decl"         # using 声明

    # ── 其他 ──
    FRIEND_OF = "friend_of"           # 友元

    # ── 文档关系（阶段 3） ──
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


# 关系类型分组：用于导入时按类别过滤和统计
CATEGORY_INHERIT = "inherit"
CATEGORY_FUNCTION = "function"
CATEGORY_CALL = "call"
CATEGORY_TEMPLATE = "template"
CATEGORY_OTHER = "other"
CATEGORY_DOC = "doc"

RELATION_CATEGORIES: dict[RelationType, str] = {
    RelationType.INHERITS_PUBLIC: CATEGORY_INHERIT,
    RelationType.INHERITS_PROTECTED: CATEGORY_INHERIT,
    RelationType.INHERITS_PRIVATE: CATEGORY_INHERIT,
    RelationType.OVERRIDES: CATEGORY_FUNCTION,
    RelationType.HIDES: CATEGORY_FUNCTION,
    RelationType.BELONGS_TO: CATEGORY_FUNCTION,
    RelationType.CALLS_DIRECT: CATEGORY_CALL,
    RelationType.CALLS_VIRTUAL: CATEGORY_CALL,
    RelationType.CALLS_CALLBACK: CATEGORY_CALL,
    RelationType.INSTANTIATES: CATEGORY_TEMPLATE,
    RelationType.TYPE_ALIAS: CATEGORY_TEMPLATE,
    RelationType.USING_DECL: CATEGORY_TEMPLATE,
    RelationType.FRIEND_OF: CATEGORY_OTHER,
    RelationType.DOC_DESCRIBES_CODE: CATEGORY_DOC,
    RelationType.CODE_REFERS_TO_DOC: CATEGORY_DOC,
}
