from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


VALID_GRADES = {str(i) for i in range(1, 10)} | {"X"}


class DieAllocationError(Exception):
    """Business-rule error that should be shown to the user."""


def require_dependency(module_name: str, install_hint: str | None = None) -> Any:
    try:
        return __import__(module_name)
    except ImportError as exc:
        hint = install_hint or f"请先安装 Python 依赖：{module_name}"
        raise DieAllocationError(hint) from exc


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def normalize_package(value: Any) -> str:
    text = normalize_text(value).upper()
    return "".join(ch for ch in text if ch.isalnum())


def normalize_grade(value: Any) -> str:
    grade = normalize_text(value).upper()
    if grade.endswith(".0"):
        grade = grade[:-2]
    if grade not in VALID_GRADES:
        raise DieAllocationError(f"非法 Bin Grade：{value!r}，只允许 1-9 或 X")
    return grade


def parse_grade_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = [str(v) for v in value]
    else:
        text = normalize_text(value)
        raw = [p for p in re.split(r"[,，、\s]+", text) if p]
    grades = [normalize_grade(v) for v in raw]
    if not grades:
        raise DieAllocationError("必须提供至少一个 Bin Grade")
    seen: set[str] = set()
    ordered: list[str] = []
    for grade in grades:
        if grade not in seen:
            ordered.append(grade)
            seen.add(grade)
    return ordered


def parse_positive_int(value: Any, field_name: str) -> int:
    text = normalize_text(value).replace(",", "")
    if not text:
        raise DieAllocationError(f"{field_name} 不能为空")
    multiplier = Decimal(1)
    if text.lower().endswith("k"):
        multiplier = Decimal(1000)
        text = text[:-1]
    try:
        number = Decimal(text) * multiplier
    except InvalidOperation as exc:
        raise DieAllocationError(f"{field_name} 必须是正整数或 k 表达式，例如 40000/40k") from exc
    if number <= 0 or number != number.to_integral_value():
        raise DieAllocationError(f"{field_name} 必须解析为正整数，当前为：{value!r}")
    return int(number)


def parse_nonnegative_int(value: Any, field_name: str) -> int:
    text = normalize_text(value).replace(",", "")
    if not text:
        raise DieAllocationError(f"{field_name} 不能为空")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise DieAllocationError(f"{field_name} 必须是非负整数") from exc
    if number < 0 or number != number.to_integral_value():
        raise DieAllocationError(f"{field_name} 必须是非负整数，当前为：{value!r}")
    return int(number)


def parse_ratio(value: Any) -> tuple[int, int]:
    text = normalize_text(value).replace("：", ":")
    parts = [p.strip() for p in text.split(":")]
    if len(parts) != 2:
        raise DieAllocationError(f"层数配比必须是 rA:rB 格式，当前为：{value!r}")
    try:
        left = int(parts[0])
        right = int(parts[1])
    except ValueError as exc:
        raise DieAllocationError(f"层数配比必须包含两个正整数，当前为：{value!r}") from exc
    if left <= 0 or right <= 0:
        raise DieAllocationError(f"层数配比必须包含两个正整数，当前为：{value!r}")
    return left, right


def normalize_reuse_rule(value: Any) -> str:
    text = normalize_text(value).lower().replace(" ", "")
    allow = {"allow_reuse", "allow", "reuse", "允许复用", "可复用", "是", "yes", "y", "true"}
    no = {"no_reuse", "noreuse", "no", "不允许复用", "不可复用", "否", "n", "false"}
    if text in allow:
        return "allow_reuse"
    if text in no:
        return "no_reuse"
    raise DieAllocationError("复用规则必须是 allow_reuse/no_reuse，或中文“允许复用/不允许复用”")


def package_score(query: str, candidate: str) -> int:
    try:
        from rapidfuzz import fuzz  # type: ignore

        return int(fuzz.WRatio(query, candidate))
    except ImportError:
        from difflib import SequenceMatcher

        return int(round(100 * SequenceMatcher(None, query, candidate).ratio()))


def stable_unique(values: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict[str, Any], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=json_default)


def item_sort_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        normalize_text(item.get("lot")),
        normalize_text(item.get("wafer")),
        normalize_text(item.get("grade")),
        normalize_text(item.get("id")),
    )
