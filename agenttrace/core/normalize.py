"""参数归一化与工具调用指纹(fingerprint)。

同一工具 + 等价参数 → 相同 fingerprint。用于所有 duplicate/retry detector 复用。

归一化处理:
- JSON key 排序(语义相同、顺序不同 → 同指纹)
- 空白规范化
- 数字类型规范化(1 vs 1.0)
- null / None
- 字符串 trim
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _normalize_value(v: Any) -> Any:
    """递归归一化一个 JSON 值。"""
    if isinstance(v, dict):
        return {k: _normalize_value(v[k]) for k in sorted(v.keys())}
    if isinstance(v, list):
        return [_normalize_value(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        # 折叠连续空白(与 JSON 解析器宽容度一致)
        s = re.sub(r"\s+", " ", s)
        return s
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        # 数字归一化:1 和 1.0 等价
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return v
    return v


def normalize_arguments(arguments: str) -> dict:
    """把原始 arguments 字符串(JSON)解析并归一化为 canonical dict。

    解析失败时返回一个带原始串的包装,避免丢信息。
    """
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return {"__raw__": arguments.strip()}
    return _normalize_value(parsed) if isinstance(parsed, dict) else {"__value__": _normalize_value(parsed)}


def call_fingerprint(tool_name: str, arguments: str) -> str:
    """计算工具调用指纹:hash(tool_name + canonical arguments)。

    所有 duplicate/retry 检测复用此函数。
    """
    canonical = normalize_arguments(arguments)
    payload = json.dumps(
        {"tool": tool_name, "args": canonical}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
