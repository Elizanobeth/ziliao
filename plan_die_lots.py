#!/usr/bin/env python3
"""Plan Die mother lots from raw inventory and a 配 Die 规则表 sheet."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover
    raise SystemExit("This script requires pandas and openpyxl for Excel files.") from exc


ALIASES = {
    "package": ["PACKAGE", "Package", "package", "产品", "封装"],
    "supplier": ["供应商", "Supplier", "supplier", "Vendor", "vendor", "厂商"],
    "lot": ["Fab LotID", "FabLotID", "Fab Lot ID", "LotID", "Lot ID", "Fab Lot", "批次"],
    "t7": ["T7 Code", "T7Code", "T7"],
    "bin_grade": ["Bin Grade", "BinGrade", "Grade", "等级"],
    "bin_quantity": ["Bin Quanity", "Bin Quantity", "BinQuanity", "BinQuantity", "Qty", "QTY", "数量", "颗数"],
    "ratio": ["层数配比", "Ratio", "ratio", "配比"],
}


@dataclass(frozen=True)
class Item:
    row_id: int
    package: str
    supplier: str
    lot_id: str
    t7_code: str
    bin_grade: int
    bin_quantity: int
    raw: dict[str, Any]


@dataclass
class SearchState:
    bucket1: int
    bucket2: int
    picks: tuple[tuple[int, int], ...]  # (item index, bucket number)


def norm_name(value: Any) -> str:
    return re.sub(r"\s+", "", str(value).strip().lower())


def choose_col(columns: list[str], explicit: str | None, logical: str, required: bool = True) -> str | None:
    if explicit:
        if explicit not in columns:
            raise ValueError(f"Column '{explicit}' was requested for {logical}, but it is not present.")
        return explicit
    by_norm = {norm_name(c): c for c in columns}
    for alias in ALIASES[logical]:
        if norm_name(alias) in by_norm:
            return by_norm[norm_name(alias)]
    if required:
        raise ValueError(f"Could not find required {logical} column. Available columns: {columns}")
    return None


def read_table(path: Path, sheet_name: str | None = None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in [".xlsx", ".xlsm", ".xls"]:
        return pd.read_excel(path, sheet_name=sheet_name)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t")
    raise ValueError(f"Unsupported file type: {path.suffix}")


def read_workbook(input_path: Path, rules_sheet: str, data_sheet: str | None) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    if input_path.suffix.lower() in [".xlsx", ".xlsm", ".xls"]:
        xls = pd.ExcelFile(input_path)
        if rules_sheet not in xls.sheet_names:
            raise ValueError(f"Rules sheet '{rules_sheet}' not found. Sheets: {xls.sheet_names}")
        selected_data_sheet = data_sheet
        if selected_data_sheet is None:
            candidates = [s for s in xls.sheet_names if s != rules_sheet]
            if not candidates:
                raise ValueError("No data sheet found besides the rules sheet.")
            selected_data_sheet = candidates[0]
        if selected_data_sheet not in xls.sheet_names:
            raise ValueError(f"Data sheet '{selected_data_sheet}' not found. Sheets: {xls.sheet_names}")
        return (
            pd.read_excel(input_path, sheet_name=selected_data_sheet),
            pd.read_excel(input_path, sheet_name=rules_sheet),
            selected_data_sheet,
        )
    if not data_sheet:
        selected_data_sheet = input_path.stem
    else:
        selected_data_sheet = data_sheet
    return read_table(input_path), pd.DataFrame(), selected_data_sheet


def parse_ratio(value: Any) -> tuple[int, int]:
    parts = re.findall(r"\d+", str(value))
    if len(parts) != 2:
        raise ValueError(f"Ratio '{value}' must contain exactly two positive integers, such as 2:6.")
    first, second = int(parts[0]), int(parts[1])
    if first <= 0 or second <= 0:
        raise ValueError(f"Ratio '{value}' must contain positive integers.")
    return first, second


def clean_str(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def clean_positive_int(value: Any, row_id: int, field_name: str) -> int:
    if pd.isna(value):
        raise ValueError(f"Missing {field_name} at raw row {row_id}.")
    qty = int(float(value))
    if qty <= 0:
        raise ValueError(f"{field_name} must be positive at raw row {row_id}; got {value!r}.")
    return qty


def build_rules(
    rules_df: pd.DataFrame,
    package_col: str | None,
    supplier_col: str | None,
    ratio_col: str | None,
) -> dict[tuple[str, str], tuple[int, int, str]]:
    cols = list(rules_df.columns)
    pkg = choose_col(cols, package_col, "package")
    sup = choose_col(cols, supplier_col, "supplier")
    ratio = choose_col(cols, ratio_col, "ratio")
    rules: dict[tuple[str, str], tuple[int, int, str]] = {}
    for _, row in rules_df.iterrows():
        package = clean_str(row[pkg])
        supplier = clean_str(row[sup])
        if not package or not supplier:
            continue
        r1, r2 = parse_ratio(row[ratio])
        rules[(package, supplier)] = (r1, r2, f"{r1}:{r2}")
    if not rules:
        raise ValueError("No valid rules were found in the rule sheet.")
    return rules


def build_items(
    raw_df: pd.DataFrame,
    package_col: str | None,
    supplier_col: str | None,
    lot_col: str | None,
    t7_col: str | None,
    bin_grade_col: str | None,
    bin_quantity_col: str | None,
    max_bin_grade: int | None,
) -> list[Item]:
    cols = list(raw_df.columns)
    pkg = choose_col(cols, package_col, "package")
    sup = choose_col(cols, supplier_col, "supplier")
    lot = choose_col(cols, lot_col, "lot")
    t7 = choose_col(cols, t7_col, "t7")
    bin_grade = choose_col(cols, bin_grade_col, "bin_grade")
    bin_quantity = choose_col(cols, bin_quantity_col, "bin_quantity")
    items: list[Item] = []
    for idx, row in raw_df.reset_index(drop=True).iterrows():
        row_id = int(idx) + 2
        package = clean_str(row[pkg])
        supplier = clean_str(row[sup])
        lot_id = clean_str(row[lot])
        t7_code = clean_str(row[t7])
        if not package or not supplier or not lot_id or not t7_code:
            continue
        grade = clean_positive_int(row[bin_grade], row_id, "Bin Grade")
        if max_bin_grade is not None and grade > max_bin_grade:
            continue
        quantity = clean_positive_int(row[bin_quantity], row_id, "Bin Quanity")
        items.append(
            Item(
                row_id=row_id,
                package=package,
                supplier=supplier,
                lot_id=lot_id,
                t7_code=t7_code,
                bin_grade=grade,
                bin_quantity=quantity,
                raw={str(c): row[c] for c in raw_df.columns},
            )
        )
    if not items:
        raise ValueError("No valid raw inventory rows were found.")
    return items


def state_metrics(bucket1: int, bucket2: int, ratio: tuple[int, int]) -> tuple[int, int, int]:
    r1, r2 = ratio
    units = min(bucket1 // r1, bucket2 // r2)
    required = units * (r1 + r2)
    waste = bucket1 + bucket2 - required
    return units, required, waste


def state_rank(state: SearchState, ratio: tuple[int, int], target_units: int, max_waste: int) -> tuple[int, int, int, int]:
    units, _, waste = state_metrics(state.bucket1, state.bucket2, ratio)
    valid_waste_bonus = 1 if waste <= max_waste else 0
    return (
        min(units, target_units),
        valid_waste_bonus,
        -waste,
        -len(state.picks),
    )


def find_best_mother_lot(
    items: list[Item],
    ratio: tuple[int, int],
    target_units: int,
    max_waste: int,
    beam_width: int,
    enforce_waste: bool = True,
) -> SearchState | None:
    cap = target_units * sum(ratio) + max_waste if enforce_waste else sum(item.bin_quantity for item in items)
    sorted_pairs = sorted(
        enumerate(items),
        key=lambda p: (p[1].bin_grade, -p[1].bin_quantity, p[1].lot_id, p[1].t7_code, p[1].row_id),
    )
    states: dict[tuple[int, int], SearchState] = {(0, 0): SearchState(0, 0, tuple())}

    for item_index, item in sorted_pairs:
        additions: dict[tuple[int, int], SearchState] = {}
        for state in states.values():
            for bucket in (1, 2):
                nb1 = state.bucket1 + item.bin_quantity if bucket == 1 else state.bucket1
                nb2 = state.bucket2 + item.bin_quantity if bucket == 2 else state.bucket2
                if nb1 + nb2 > cap:
                    continue
                key = (nb1, nb2)
                new_state = SearchState(nb1, nb2, state.picks + ((item_index, bucket),))
                old_state = states.get(key) or additions.get(key)
                if old_state is None or len(new_state.picks) < len(old_state.picks):
                    additions[key] = new_state
        states.update(additions)
        if len(states) > beam_width:
            ranked = sorted(states.values(), key=lambda s: state_rank(s, ratio, target_units, max_waste), reverse=True)
            states = {(s.bucket1, s.bucket2): s for s in ranked[:beam_width]}

    candidates = []
    for state in states.values():
        units, _, waste = state_metrics(state.bucket1, state.bucket2, ratio)
        if state.picks and 0 < units <= target_units and (waste <= max_waste or not enforce_waste):
            candidates.append(state)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda s: (
            state_metrics(s.bucket1, s.bucket2, ratio)[0],
            -state_metrics(s.bucket1, s.bucket2, ratio)[2],
            -len(s.picks),
        ),
        reverse=True,
    )[0]


def make_group_suggestions(
    package: str,
    supplier: str,
    available: list[Item],
    ratio: tuple[int, int],
    target_units: int,
    max_waste: int,
    beam_width: int,
    blocked_by_reuse_count: int,
    best_valid: SearchState | None,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    if not available:
        return suggestions

    best_without_waste = find_best_mother_lot(available, ratio, target_units, max_waste, beam_width, enforce_waste=False)
    total_die = sum(item.bin_quantity for item in available)
    r1, r2 = ratio
    theoretical_units = total_die // (r1 + r2)
    best_units = state_metrics(best_valid.bucket1, best_valid.bucket2, ratio)[0] if best_valid else 0

    if best_units < target_units:
        target_required_die = target_units * (r1 + r2)
        if total_die < target_required_die:
            suggestions.append(
                {
                    "PACKAGE": package,
                    "供应商": supplier,
                    "Issue": "总 Bin Quanity 不足",
                    "Suggestion": f"目标 {target_units} Unit 至少需要 {target_required_die} 颗，但当前可用 sum(Bin Quanity) 只有 {total_die}；请降低 target_units 或补充该 PACKAGE+供应商 的库存。",
                    "Detail": f"ratio={r1}:{r2}, 理论上限约={theoretical_units} Unit",
                }
            )
        suggestions.append(
            {
                "PACKAGE": package,
                "供应商": supplier,
                "Issue": "未达到目标 Unit",
                    "Suggestion": f"当前可行最高 Unit 为 {best_units}，低于目标 {target_units}；可降低 target_units，或补充同 PACKAGE+供应商 的 Bin 库存。",
                "Detail": f"剩余可用 Die={total_die}, 理论上限约={theoretical_units}",
            }
        )

    if best_without_waste is not None:
        units, _, waste = state_metrics(best_without_waste.bucket1, best_without_waste.bucket2, ratio)
        if waste > max_waste and units >= max(1, best_units):
            suggestions.append(
                {
                    "PACKAGE": package,
                    "供应商": supplier,
                    "Issue": "浪费上限限制组合",
                    "Suggestion": f"若业务允许，可把 max_waste 从 {max_waste} 提高到至少 {waste}，可获得约 {units} Unit 的候选母批。",
                    "Detail": f"候选 Bucket1Die={best_without_waste.bucket1}, Bucket2Die={best_without_waste.bucket2}",
                }
            )

    if blocked_by_reuse_count > 0:
        suggestions.append(
            {
                "PACKAGE": package,
                "供应商": supplier,
                "Issue": "Fab LotID 不复用限制",
                "Suggestion": "如果同一个 Fab LotID 允许进入不同母批，可重跑并增加 --allow-lot-reuse。",
                "Detail": f"被不复用规则排除的剩余行数={blocked_by_reuse_count}",
            }
        )

    if not suggestions and best_without_waste is None:
        suggestions.append(
            {
                "PACKAGE": package,
                "供应商": supplier,
                "Issue": "无有效组合",
                "Suggestion": "检查 Bin Quanity 粒度是否过大、ratio 是否正确，或补充更小数量的 T7 Code/Bin Grade 行以便组合。",
                "Detail": f"ratio={r1}:{r2}, 剩余行数={len(available)}",
            }
        )
    return suggestions


def plan(items: list[Item], rules: dict[tuple[str, str], tuple[int, int, str]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    mother_lots: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    unused_notes: dict[int, str] = {}
    diagnostics: list[str] = []
    suggestions: list[dict[str, Any]] = []
    missing_rule_keys: set[tuple[str, str]] = set()

    groups: dict[tuple[str, str], list[Item]] = {}
    for item in items:
        key = (item.package, item.supplier)
        if key not in rules:
            unused_notes[item.row_id] = "no matching PACKAGE+供应商 rule"
            missing_rule_keys.add(key)
            continue
        groups.setdefault(key, []).append(item)

    for package, supplier in sorted(missing_rule_keys):
        suggestions.append(
            {
                "PACKAGE": package,
                "供应商": supplier,
                "Issue": "缺少配 Die 规则",
                "Suggestion": "在 配 Die 规则表 中补充该 PACKAGE+供应商 的 层数配比 后重跑。",
                "Detail": "原始数据存在该组合，但规则表没有匹配行。",
            }
        )

    sequence = 1
    for key in sorted(groups):
        package, supplier = key
        ratio1, ratio2, ratio_text = rules[key]
        available = sorted(groups[key], key=lambda x: (x.bin_grade, -x.bin_quantity, x.lot_id, x.t7_code, x.row_id))
        group_total_die = sum(item.bin_quantity for item in available)
        target_required_die = args.target_units * (ratio1 + ratio2)
        diagnostics.append(
            f"group={package}|{supplier}; sum_bin_quantity={group_total_die}; target_required_die={target_required_die}; theoretical_unit_upper_bound={group_total_die // (ratio1 + ratio2)}"
        )
        blocked_by_reuse_count = 0
        best_units_seen = 0
        best_valid_seen: SearchState | None = None
        while available:
            best = find_best_mother_lot(available, (ratio1, ratio2), args.target_units, args.max_waste, args.beam_width)
            if best is None:
                suggestions.extend(
                    make_group_suggestions(
                        package,
                        supplier,
                        available,
                        (ratio1, ratio2),
                        args.target_units,
                        args.max_waste,
                        args.beam_width,
                        blocked_by_reuse_count,
                        best_valid_seen,
                    )
                )
                for item in available:
                    unused_notes.setdefault(item.row_id, "no valid mother lot combination")
                break

            picked_indexes = {idx for idx, _ in best.picks}
            picked_by_index = {idx: bucket for idx, bucket in best.picks}
            picked_items = [available[idx] for idx in sorted(picked_indexes)]
            units, required_die, waste = state_metrics(best.bucket1, best.bucket2, (ratio1, ratio2))
            if units > best_units_seen:
                best_units_seen = units
                best_valid_seen = best
            mother_lot_id = f"ML{sequence:04d}"
            lots = sorted({item.lot_id for item in picked_items})

            mother_lots.append(
                {
                    "MotherLotID": mother_lot_id,
                    "PACKAGE": package,
                    "供应商": supplier,
                    "层数配比": ratio_text,
                    "Unit": units,
                    "RequiredDie": required_die,
                    "SelectedDie": best.bucket1 + best.bucket2,
                    "WasteDie": waste,
                    "Bucket1Die": best.bucket1,
                    "Bucket2Die": best.bucket2,
                    "LotCount": len(lots),
                    "FabLotIDList": ", ".join(lots),
                }
            )
            for idx, item in enumerate(available):
                if idx not in picked_indexes:
                    continue
                assignments.append(
                    {
                        "MotherLotID": mother_lot_id,
                        "PACKAGE": package,
                        "供应商": supplier,
                        "Fab LotID": item.lot_id,
                        "T7 Code": item.t7_code,
                        "Bin Grade": item.bin_grade,
                        "AssignedBucket": picked_by_index[idx],
                        "Bin Quanity": item.bin_quantity,
                        "SourceRow": item.row_id,
                    }
                )

            used_lots = {item.lot_id for item in picked_items}
            used_rows = {item.row_id for item in picked_items}
            next_available = []
            for item in available:
                if item.row_id in used_rows:
                    continue
                if not args.allow_lot_reuse and item.lot_id in used_lots:
                    unused_notes[item.row_id] = f"blocked by no-reuse rule after {mother_lot_id}"
                    blocked_by_reuse_count += 1
                    continue
                next_available.append(item)
            available = next_available
            sequence += 1
        if best_units_seen and best_units_seen < args.target_units:
            suggestions.extend(
                make_group_suggestions(
                    package,
                    supplier,
                    available or groups[key],
                    (ratio1, ratio2),
                    args.target_units,
                    args.max_waste,
                    args.beam_width,
                    blocked_by_reuse_count,
                    best_valid_seen,
                )
            )

    used_assignment_rows = {row["SourceRow"] for row in assignments}
    unused_inventory = []
    for item in sorted(items, key=lambda x: x.row_id):
        if item.row_id in used_assignment_rows:
            continue
        unused_inventory.append(
            {
                "PACKAGE": item.package,
                "供应商": item.supplier,
                "Fab LotID": item.lot_id,
                "T7 Code": item.t7_code,
                "Bin Grade": item.bin_grade,
                "Bin Quanity": item.bin_quantity,
                "SourceRow": item.row_id,
                "Reason": unused_notes.get(item.row_id, "not selected"),
            }
        )

    diagnostics.append(f"target_units={args.target_units}")
    diagnostics.append(f"max_waste={args.max_waste}")
    diagnostics.append(f"allow_lot_reuse={args.allow_lot_reuse}")
    diagnostics.append(f"max_bin_grade={args.max_bin_grade}")
    diagnostics.append(f"beam_width={args.beam_width}")
    diagnostics.append(f"mother_lot_count={len(mother_lots)}")
    diagnostics.append(f"assignment_row_count={len(assignments)}")
    diagnostics.append(f"unused_row_count={len(unused_inventory)}")
    return mother_lots, assignments, unused_inventory, diagnostics, suggestions


def write_outputs(output_path: Path, mother_lots: list[dict[str, Any]], assignments: list[dict[str, Any]], unused: list[dict[str, Any]], diagnostics: list[str], suggestions: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower()
    diag_rows = [{"Diagnostic": line} for line in diagnostics]
    if suffix == ".xlsx":
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            pd.DataFrame(mother_lots).to_excel(writer, sheet_name="mother_lots", index=False)
            pd.DataFrame(assignments).to_excel(writer, sheet_name="lot_assignments", index=False)
            pd.DataFrame(unused).to_excel(writer, sheet_name="unused_inventory", index=False)
            pd.DataFrame(diag_rows).to_excel(writer, sheet_name="diagnostics", index=False)
            pd.DataFrame(suggestions).to_excel(writer, sheet_name="optimization_suggestions", index=False)
        return
    if suffix == ".json":
        output_path.write_text(
            json.dumps(
                {
                    "mother_lots": mother_lots,
                    "lot_assignments": assignments,
                    "unused_inventory": unused,
                    "diagnostics": diagnostics,
                    "optimization_suggestions": suggestions,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return
    if suffix == ".csv":
        stem = output_path.with_suffix("")
        for name, rows in [
            ("mother_lots", mother_lots),
            ("lot_assignments", assignments),
            ("unused_inventory", unused),
            ("diagnostics", diag_rows),
            ("optimization_suggestions", suggestions),
        ]:
            path = stem.parent / f"{stem.name}_{name}.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as fh:
                if rows:
                    writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
                else:
                    fh.write("")
        return
    raise ValueError("Output must end in .xlsx, .json, or .csv")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan Die mother lots from inventory and 配 Die 规则表.")
    parser.add_argument("--input", required=True, type=Path, help="Input .xlsx/.xls/.csv/.tsv file.")
    parser.add_argument("--output", required=True, type=Path, help="Output .xlsx, .json, or .csv path.")
    parser.add_argument("--data-sheet", help="Raw data sheet name. Defaults to first non-rule sheet.")
    parser.add_argument("--rules-sheet", default="配 Die 规则表", help="Rules sheet name.")
    parser.add_argument("--target-units", required=True, type=int, help="Maximum target Unit count per mother lot.")
    parser.add_argument("--max-waste", default=30, type=int, help="Maximum waste die per mother lot.")
    parser.add_argument("--allow-lot-reuse", action="store_true", help="Allow one Fab LotID to appear in multiple mother lots.")
    parser.add_argument("--beam-width", default=5000, type=int, help="Search breadth. Increase for harder data.")
    parser.add_argument("--package-col", help="PACKAGE column override for both raw and rules sheets.")
    parser.add_argument("--supplier-col", help="Supplier column override for both raw and rules sheets.")
    parser.add_argument("--lot-col", help="Fab LotID column override for raw sheet.")
    parser.add_argument("--t7-col", help="T7 Code column override for raw sheet.")
    parser.add_argument("--bin-grade-col", help="Bin Grade column override for raw sheet.")
    parser.add_argument("--bin-quantity-col", help="Bin Quanity column override for raw sheet.")
    parser.add_argument("--max-bin-grade", type=int, help="Use only rows with Bin Grade <= this value. By default all grades are eligible.")
    parser.add_argument("--ratio-col", help="Ratio column override for rule sheet.")
    args = parser.parse_args(argv)
    if args.target_units <= 0:
        raise ValueError("--target-units must be positive.")
    if args.max_waste < 0:
        raise ValueError("--max-waste must be non-negative.")
    if args.beam_width < 100:
        raise ValueError("--beam-width must be at least 100.")
    if args.max_bin_grade is not None and args.max_bin_grade <= 0:
        raise ValueError("--max-bin-grade must be positive when provided.")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv or sys.argv[1:])
        raw_df, rules_df, selected_data_sheet = read_workbook(args.input, args.rules_sheet, args.data_sheet)
        if rules_df.empty:
            raise ValueError("CSV/TSV inputs require a separate Excel workbook with 配 Die 规则表; use .xlsx for normal runs.")
        rules = build_rules(rules_df, args.package_col, args.supplier_col, args.ratio_col)
        items = build_items(
            raw_df,
            args.package_col,
            args.supplier_col,
            args.lot_col,
            args.t7_col,
            args.bin_grade_col,
            args.bin_quantity_col,
            args.max_bin_grade,
        )
        mother_lots, assignments, unused, diagnostics, suggestions = plan(items, rules, args)
        diagnostics.insert(0, f"data_sheet={selected_data_sheet}")
        diagnostics.insert(1, f"rules_sheet={args.rules_sheet}")
        diagnostics.append(f"optimization_suggestion_count={len(suggestions)}")
        write_outputs(args.output, mother_lots, assignments, unused, diagnostics, suggestions)
        print(f"Wrote {args.output}")
        print(f"Mother lots: {len(mother_lots)}")
        print(f"Assignment rows: {len(assignments)}")
        print(f"Unused rows: {len(unused)}")
        print(f"Optimization suggestions: {len(suggestions)}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
