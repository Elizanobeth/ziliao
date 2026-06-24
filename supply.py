from __future__ import annotations

from collections import defaultdict
from typing import Any

from .common import (
    DieAllocationError,
    load_json,
    normalize_text,
    parse_grade_list,
    parse_positive_int,
    save_json,
)
from .matcher import select_package_row


def _select_source_packages(
    source_rows: list[dict[str, Any]],
    target_package: str,
    supplier: str,
) -> tuple[list[str], dict[str, Any]]:
    candidates = [row for row in source_rows if normalize_text(row.get("供应商")) == supplier]
    if not candidates:
        raise DieAllocationError(f"原始数据中找不到供应商：{supplier}")
    pseudo_rows = []
    seen: set[str] = set()
    for row in candidates:
        package = normalize_text(row.get("PACKAGE"))
        if package not in seen:
            pseudo_rows.append({"PACKAGE": package, "_excel_row": row.get("_excel_row", 10**9), "层数配比": "1:1"})
            seen.add(package)
    selected = select_package_row(pseudo_rows, target_package)
    selected_package = normalize_text(selected["PACKAGE"])
    return [selected_package], {
        "method": selected.get("_match_reason"),
        "source_package": selected_package,
        "source_excel_row": selected.get("_excel_row"),
    }


def build_supply(
    validated: dict[str, Any],
    matched_rule: dict[str, Any],
    grades: str | list[str],
    target_units: str | int,
) -> dict[str, Any]:
    selected_grades = parse_grade_list(grades)
    target = parse_positive_int(target_units, "目标 Unit 数")
    supplier = matched_rule["supplier"]
    target_package = matched_rule["target_package"]
    source_packages, source_match = _select_source_packages(
        validated["source_rows"], target_package, supplier
    )

    aggregate: dict[tuple[str, str, str], int] = defaultdict(int)
    source_rows_used = 0
    for row in validated["source_rows"]:
        if normalize_text(row.get("供应商")) != supplier:
            continue
        if normalize_text(row.get("PACKAGE")) not in source_packages:
            continue
        if normalize_text(row.get("Bin Grade")) not in selected_grades:
            continue
        key = (
            normalize_text(row.get("Fab LotID")),
            normalize_text(row.get("T7 Code")),
            normalize_text(row.get("Bin Grade")),
        )
        aggregate[key] += int(row.get("Bin Quanity", 0))
        source_rows_used += 1

    items: list[dict[str, Any]] = []
    for idx, ((lot, wafer, grade), qty) in enumerate(sorted(aggregate.items()), start=1):
        if qty <= 0:
            continue
        items.append(
            {
                "id": f"I{idx:06d}",
                "lot": lot,
                "wafer": wafer,
                "grade": grade,
                "qty": qty,
            }
        )

    if not items:
        raise DieAllocationError("按目标 PACKAGE/供应商/Bin Grade 过滤后，没有可用 Die")

    lot_totals: dict[str, int] = defaultdict(int)
    wafer_totals: dict[str, int] = defaultdict(int)
    grade_totals: dict[str, int] = defaultdict(int)
    for item in items:
        lot_totals[item["lot"]] += item["qty"]
        wafer_totals[item["wafer"]] += item["qty"]
        grade_totals[item["grade"]] += item["qty"]

    r_a = int(matched_rule["ratio"]["A"])
    r_b = int(matched_rule["ratio"]["B"])
    total_qty = sum(item["qty"] for item in items)
    required_qty = target * (r_a + r_b)
    return {
        "config": {
            "target_package": target_package,
            "supplier": supplier,
            "source_packages": source_packages,
            "target_units": target,
            "selected_grades": selected_grades,
            "ratio": matched_rule["ratio"],
            "rule_match": matched_rule["match"],
            "source_package_match": source_match,
        },
        "items": items,
        "stats": {
            "source_rows_used": source_rows_used,
            "atomic_item_count": len(items),
            "lot_count": len(lot_totals),
            "wafer_count": len(wafer_totals),
            "total_selected_qty": total_qty,
            "required_qty_for_target": required_qty,
            "quantity_can_meet_target": total_qty >= required_qty,
            "lot_totals": dict(sorted(lot_totals.items())),
            "wafer_totals": dict(sorted(wafer_totals.items())),
            "grade_totals": dict(sorted(grade_totals.items())),
        },
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Step 03: 过滤 Bin Grade 并构建最小供应单元。")
    parser.add_argument("--validated", required=True, help="Step 01 输出的 validated.json")
    parser.add_argument("--matched-rule", required=True, help="Step 02 输出的 matched_rule.json")
    parser.add_argument("--grades", required=True, help="用户选择 Bin Grade，例如 1,2,3 或 1,2,X")
    parser.add_argument("--target-units", required=True, help="目标 Unit 数，例如 40000 或 40k")
    parser.add_argument("--out", required=True, help="输出 supply.json")
    args = parser.parse_args()
    data = build_supply(
        load_json(args.validated),
        load_json(args.matched_rule),
        args.grades,
        args.target_units,
    )
    save_json(data, args.out)
    print(
        "OK: 已构建供应单元 "
        f"{data['stats']['atomic_item_count']} 个，总 Die {data['stats']['total_selected_qty']}"
    )


if __name__ == "__main__":
    main()
