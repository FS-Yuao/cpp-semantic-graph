"""
查询层公共工具函数（P2-3：消除多处重复定义）

parse_extra 原在 call_query / doc_query / traverse / polymorphism_query /
alias_query / fusion_query 各复制一份（逻辑完全一致），统一到此。
"""

import json


def parse_extra(raw) -> dict:
    """安全解析 extra_info（可能是 JSON 字符串、dict 或 None），失败返回空 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
