"""
关系类型枚举 — 从 parser.models 重导出（单一权威来源）

P2-2 修复：原本此处与 models.py 各自定义一份 RelationType，靠人工同步，
漏改一边会静默产生不一致。现统一从 parser.models 重导出，只在本模块保留
数据库入库用的分类映射（RELATION_CATEGORIES）。

parser.models 仅依赖 stdlib（dataclasses/enum），此处反向导入不构成循环。
"""

from ..parser.models import RelationType


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

__all__ = ["RelationType", "RELATION_CATEGORIES",
           "CATEGORY_INHERIT", "CATEGORY_FUNCTION", "CATEGORY_CALL",
           "CATEGORY_TEMPLATE", "CATEGORY_OTHER", "CATEGORY_DOC"]
