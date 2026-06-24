from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import DieAllocationError, normalize_grade, normalize_text, save_json


SOURCE_SHEET = "原始数据"
RULE_SHEET = "配die 规则表"
SOURCE_COLUMNS = ["PACKAGE", "供应商", "Fab LotID", "Bin Grade", "Bin Quanity", "T7 Code"]
RULE_COLUMNS = ["PACKAGE", "供应商", "层数配比"]


def _load_pandas() -> Any:
    try:
        import pandas as pd  # type: ignore

        return pd
    except ImportError as exc:
        raise DieAllocationError(
            "读取 Excel 需要 pandas/openpyxl。请使用 Codex 内置 Python，或安装 pandas openpyxl。"
        ) from exc


def _require_columns(df: Any, required: list[str], sheet_name: str) -> None:
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise DieAllocationError(f"Sheet `{sheet_name}` 缺少必要字段：{', '.join(missing)}")


def _clean_source_rows(df: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, row in df.iterrows():
        excel_row = int(idx) + 2
        cleaned = {col: normalize_text(row[col]) for col in SOURCE_COLUMNS}
        for col in ["PACKAGE", "供应商", "Fab LotID", "Bin Grade", "T7 Code"]:
            if not cleaned[col]:
                errors.append(f"原始数据第 {excel_row} 行 `{col}` 为空")
        try:
            cleaned["Bin Grade"] = normalize_grade(cleaned["Bin Grade"])
        except DieAllocationError as exc:
            errors.append(f"原始数据第 {excel_row} 行：{exc}")
        qty_raw = row["Bin Quanity"]
        try:
            qty = float(qty_raw)
            if qty < 0 or not qty.is_integer():
                raise ValueError
            cleaned["Bin Quanity"] = int(qty)
        except (TypeError, ValueError):
            errors.append(f"原始数据第 {excel_row} 行 `Bin Quanity` 必须是非负整数")
        cleaned["_excel_row"] = excel_row
        rows.append(cleaned)
    if errors:
        raise DieAllocationError("\n".join(errors[:50]))
    return rows


def _clean_rule_rows(df: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, row in df.iterrows():
        excel_row = int(idx) + 2
        cleaned = {col: normalize_text(row[col]) for col in RULE_COLUMNS}
        for col in RULE_COLUMNS:
            if not cleaned[col]:
                errors.append(f"配die 规则表第 {excel_row} 行 `{col}` 为空")
        cleaned["_excel_row"] = excel_row
        rows.append(cleaned)
    if errors:
        raise DieAllocationError("\n".join(errors[:50]))
    return rows


def validate_workbook(workbook_path: str | Path) -> dict[str, Any]:
    pd = _load_pandas()
    workbook = Path(workbook_path)
    if not workbook.exists():
        raise DieAllocationError(f"找不到 Excel 文件：{workbook}")
    sheets = pd.read_excel(workbook, sheet_name=None)
    for sheet_name in [SOURCE_SHEET, RULE_SHEET]:
        if sheet_name not in sheets:
            raise DieAllocationError(f"Excel 中缺少 Sheet：{sheet_name}")
    source_df = sheets[SOURCE_SHEET]
    rule_df = sheets[RULE_SHEET]
    _require_columns(source_df, SOURCE_COLUMNS, SOURCE_SHEET)
    _require_columns(rule_df, RULE_COLUMNS, RULE_SHEET)
    source_rows = _clean_source_rows(source_df[SOURCE_COLUMNS])
    rule_rows = _clean_rule_rows(rule_df[RULE_COLUMNS])
    return {
        "workbook": str(workbook),
        "source_sheet": SOURCE_SHEET,
        "rule_sheet": RULE_SHEET,
        "source_rows": source_rows,
        "rule_rows": rule_rows,
        "row_counts": {"source": len(source_rows), "rules": len(rule_rows)},
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Step 01: 校验 Excel 工作簿并输出标准 JSON。")
    parser.add_argument("--workbook", required=True, help="输入 Excel 工作簿路径")
    parser.add_argument("--out", required=True, help="输出 validated.json 路径")
    args = parser.parse_args()
    data = validate_workbook(args.workbook)
    save_json(data, args.out)
    print(f"OK: 已校验工作簿，原始数据 {data['row_counts']['source']} 行，规则 {data['row_counts']['rules']} 行")


if __name__ == "__main__":
    main()
