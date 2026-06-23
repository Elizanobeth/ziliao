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


def probe_unit_targets(target_units: int) -> list[int]:
    probes = [target_units]
    for pct in range(90, 0, -10):
        probe = max(1, target_units * pct // 100)
        if probe not in probes:
            probes.append(probe)
    return probes


def item_order(items: list[Item]) -> list[tuple[int, Item]]:
    return sorted(
        enumerate(items),
        key=lambda p: (p[1].bin_grade, -p[1].bin_quantity, p[1].lot_id, p[1].t7_code, p[1].row_id),
    )


def build_subset_index(
    items: list[Item],
    max_sum: int,
    excluded_indexes: set[int] | None = None,
) -> tuple[bytearray, list[int], list[int]]:
    excluded_indexes = excluded_indexes or set()
    reachable = bytearray(max_sum + 1)
    prev_sum = [-1] * (max_sum + 1)
    prev_item = [-1] * (max_sum + 1)
    reachable[0] = 1
    for item_index, item in item_order(items):
        if item_index in excluded_indexes:
            continue
        qty = item.bin_quantity
        if qty > max_sum:
            continue
        for total in range(max_sum - qty, -1, -1):
            next_total = total + qty
            if reachable[total] and not reachable[next_total]:
                reachable[next_total] = 1
                prev_sum[next_total] = total
                prev_item[next_total] = item_index
    return reachable, prev_sum, prev_item


def reconstruct_subset(prev_sum: list[int], prev_item: list[int], total: int) -> tuple[int, ...]:
    picks: list[int] = []
    current = total
    while current > 0:
        item_index = prev_item[current]
        if item_index < 0:
            return tuple()
        picks.append(item_index)
        current = prev_sum[current]
    return tuple(reversed(picks))


def find_subset_for_sum_range(
    items: list[Item],
    min_sum: int,
    max_sum: int,
    excluded_indexes: set[int] | None = None,
) -> tuple[int, tuple[int, ...]] | None:
    if min_sum > max_sum:
        return None
    max_possible = sum(item.bin_quantity for idx, item in enumerate(items) if not excluded_indexes or idx not in excluded_indexes)
    if max_possible < min_sum:
        return None
    capped_max = min(max_sum, max_possible)
    reachable, prev_sum, prev_item = build_subset_index(items, capped_max, excluded_indexes)
    for total in range(min_sum, capped_max + 1):
        if reachable[total]:
            picks = reconstruct_subset(prev_sum, prev_item, total)
            if picks:
                return total, picks
    return None


def find_fast_target_mother_lot(
    items: list[Item],
    ratio: tuple[int, int],
    target_units: int,
    max_waste: int,
) -> SearchState | None:
    r1, r2 = ratio
    required1 = target_units * r1
    required2 = target_units * r2
    if sum(item.bin_quantity for item in items) < required1 + required2:
        return None

    max_bucket1 = required1 + max_waste
    reachable1, prev_sum1, prev_item1 = build_subset_index(items, max_bucket1)
    for bucket1 in range(required1, max_bucket1 + 1):
        if not reachable1[bucket1]:
            continue
        bucket1_picks = reconstruct_subset(prev_sum1, prev_item1, bucket1)
        if not bucket1_picks:
            continue
        remaining_waste = max_waste - (bucket1 - required1)
        bucket2_result = find_subset_for_sum_range(
            items,
            required2,
            required2 + remaining_waste,
            set(bucket1_picks),
        )
        if bucket2_result is None:
            continue
        bucket2, bucket2_picks = bucket2_result
        state = SearchState(
            bucket1=bucket1,
            bucket2=bucket2,
            picks=tuple((idx, 1) for idx in bucket1_picks) + tuple((idx, 2) for idx in bucket2_picks),
        )
        units, _, waste = state_metrics(state.bucket1, state.bucket2, ratio)
        if 0 < units <= target_units and waste <= max_waste:
            return state
    return None


def find_best_mother_lot(
    items: list[Item],
    ratio: tuple[int, int],
    target_units: int,
    max_waste: int,
    beam_width: int,
    enforce_waste: bool = True,
) -> SearchState | None:
    if enforce_waste:
        for probe_target in probe_unit_targets(target_units):
            fast = find_fast_target_mother_lot(items, ratio, probe_target, max_waste)
            if fast is not None:
                return fast

    cap = target_units * sum(ratio) + max_waste if enforce_waste else sum(item.bin_quantity for item in items)
    sorted_pairs = item_order(items)
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


def find_best_effort_mother_lot(
    items: list[Item],
    ratio: tuple[int, int],
    target_units: int,
    max_waste: int,
    beam_width: int,
) -> SearchState | None:
    """Find the largest under-target Unit candidate that still obeys max_waste."""
    best: SearchState | None = None
    for probe_target in probe_unit_targets(target_units)[1:]:
        candidate = find_fast_target_mother_lot(items, ratio, probe_target, max_waste)
        if candidate is None:
            continue
        if best is None:
            best = candidate
            continue
        candidate_units, _, candidate_waste = state_metrics(candidate.bucket1, candidate.bucket2, ratio)
        best_units, _, best_waste = state_metrics(best.bucket1, best.bucket2, ratio)
        if (candidate_units, -candidate_waste, -len(candidate.picks)) > (best_units, -best_waste, -len(best.picks)):
            best = candidate

    if best is None:
        best = find_best_mother_lot(items, ratio, target_units, max_waste, beam_width)
    return best


def state_to_summary_row(
    state: SearchState,
    available: list[Item],
    ratio: tuple[int, int],
    package: str,
    supplier: str,
    ratio_text: str,
    match_id: str,
) -> dict[str, Any]:
    picked_items = [available[idx] for idx, _ in state.picks]
    units, required_die, waste = state_metrics(state.bucket1, state.bucket2, ratio)
    lots = sorted({item.lot_id for item in picked_items})
    return {
        "MatchID": match_id,
        "PACKAGE": package,
        "供应商": supplier,
        "层数配比": ratio_text,
        "Unit": units,
        "RequiredDie": required_die,
        "SelectedDie": state.bucket1 + state.bucket2,
        "WasteDie": waste,
        "Bucket1Die": state.bucket1,
        "Bucket2Die": state.bucket2,
        "LotCount": len(lots),
        "FabLotIDList": ", ".join(lots),
        "MatchType": "best_effort_under_waste_limit",
    }


def state_to_assignment_rows(
    state: SearchState,
    available: list[Item],
    match_id: str,
    package: str,
    supplier: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    picked_by_index = {idx: bucket for idx, bucket in state.picks}
    for idx in sorted(picked_by_index):
        item = available[idx]
        rows.append(
            {
                "MatchID": match_id,
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
    return rows


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
    best_effort: SearchState | None = None,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    if not available:
        return suggestions

    best_without_waste = find_best_mother_lot(available, ratio, target_units, max_waste, beam_width, enforce_waste=False)
    total_die = sum(item.bin_quantity for item in available)
    r1, r2 = ratio
    theoretical_units = total_die // (r1 + r2)
    best_units = state_metrics(best_valid.bucket1, best_valid.bucket2, ratio)[0] if best_valid else 0
    if best_effort is not None:
        effort_units, _, effort_waste = state_metrics(best_effort.bucket1, best_effort.bucket2, ratio)
        best_units = max(best_units, effort_units)
        suggestions.append(
            {
                "PACKAGE": package,
                "供应商": supplier,
                "Issue": "未找到目标 Unit 的正式母批，已给出替代方案",
                "Suggestion": f"在 max_waste <= {max_waste} 的限制内，当前替代方案最高可做到 {effort_units} Unit，WasteDie={effort_waste}；如业务接受，可把剩余 Unit 需求调整为 {effort_units} 后重跑，或补充库存继续冲击剩余目标 {target_units}。",
                "Detail": f"替代 Bucket1Die={best_effort.bucket1}, Bucket2Die={best_effort.bucket2}, Lot行数={len(best_effort.picks)}",
            }
        )

    if best_units < target_units:
        target_required_die = target_units * (r1 + r2)
        if total_die < target_required_die:
            suggestions.append(
                {
                    "PACKAGE": package,
                    "供应商": supplier,
                    "Issue": "总 Bin Quanity 不足",
                    "Suggestion": f"剩余目标 {target_units} Unit 至少需要 {target_required_die} 颗，但当前可用 sum(Bin Quanity) 只有 {total_die}；请降低总 Unit 需求或补充该 PACKAGE+供应商 的库存。",
                    "Detail": f"ratio={r1}:{r2}, 理论上限约={theoretical_units} Unit",
                }
            )
        suggestions.append(
            {
                "PACKAGE": package,
                "供应商": supplier,
                "Issue": "未达到目标 Unit",
                "Suggestion": f"当前可行最高 Unit 为 {best_units}，低于剩余目标 {target_units}；可降低总 Unit 需求，或补充同 PACKAGE+供应商 的 Bin 库存。",
                "Detail": f"剩余可用 Bin Quanity={total_die}, 理论上限约={theoretical_units} Unit",
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


def build_summary_rows(
    mother_lots: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    unused: list[dict[str, Any]],
    diagnostics: list[str],
    suggestions: list[dict[str, Any]],
    best_effort_matches: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "Section": "运行条件",
            "Summary": f"total_units={args.total_units}; max_waste={args.max_waste}; Fab LotID 允许复用={args.allow_lot_reuse}; max_bin_grade={args.max_bin_grade if args.max_bin_grade is not None else '全部'}。",
        }
    ]
    requested_units = int(args.total_units)
    if mother_lots:
        total_units = sum(int(row["Unit"]) for row in mother_lots)
        total_waste = sum(int(row["WasteDie"]) for row in mother_lots)
        status = "已满足" if total_units >= requested_units else "未完全满足"
        rows.append(
            {
                "Section": "正式匹配结果",
                "Summary": f"总需求 Unit={requested_units}，{status}；找到 {len(mother_lots)} 个正式母批，累计 Unit={total_units}，总 WasteDie={total_waste}；每个母批均满足 WasteDie <= {args.max_waste}。Lot 清单见 lot_assignments。",
            }
        )
        for row in mother_lots:
            rows.append(
                {
                    "Section": "正式母批明细",
                    "Summary": f"{row['MotherLotID']}：PACKAGE={row['PACKAGE']}，供应商={row['供应商']}，Unit={row['Unit']}，WasteDie={row['WasteDie']}，Bucket1Die={row['Bucket1Die']}，Bucket2Die={row['Bucket2Die']}，LotCount={row['LotCount']}。",
                }
            )
    else:
        rows.append(
            {
                "Section": "正式匹配结果",
                "Summary": f"没有找到满足正式约束的母批。总需求 Unit={requested_units}；正式约束为 Unit > 0、WasteDie <= {args.max_waste}，且每行 T7 Code 不拆分。未使用库存见 unused_inventory。",
            }
        )

    if best_effort_matches:
        for row in best_effort_matches:
            rows.append(
                {
                    "Section": "最佳替代方案",
                    "Summary": f"{row['MatchID']}：在 WasteDie <= {args.max_waste} 内找到 Unit 最大的替代方案，Unit={row['Unit']}，WasteDie={row['WasteDie']}，Bucket1Die={row['Bucket1Die']}，Bucket2Die={row['Bucket2Die']}，LotCount={row['LotCount']}。Lot 清单见 best_effort_assignments。",
                }
            )
    else:
        rows.append(
            {
                "Section": "最佳替代方案",
                "Summary": "未额外输出替代方案；如果正式母批已经找到，优先使用 mother_lots。若正式母批未找到且这里为空，说明当前搜索未找到任何 WasteDie 不超过上限的可行替代组合。",
            }
        )

    if suggestions:
        rows.append(
            {
                "Section": "调整建议",
                "Summary": "存在未满足目标或可优化事项，具体见 optimization_suggestions。",
            }
        )
    else:
        rows.append({"Section": "调整建议", "Summary": "当前没有额外优化建议。"})

    rows.append(
        {
            "Section": "数据覆盖",
            "Summary": f"正式分配明细行数={len(assignments)}；未使用库存行数={len(unused)}；诊断信息行数={len(diagnostics)}。",
        }
    )
    return rows


def plan(items: list[Item], rules: dict[tuple[str, str], tuple[int, int, str]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    mother_lots: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    best_effort_matches: list[dict[str, Any]] = []
    best_effort_assignments: list[dict[str, Any]] = []
    unused_notes: dict[int, str] = {}
    diagnostics: list[str] = []
    suggestions: list[dict[str, Any]] = []
    missing_rule_keys: set[tuple[str, str]] = set()
    remaining_total_units = args.total_units

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
        if remaining_total_units <= 0:
            for item in available:
                unused_notes.setdefault(item.row_id, "not needed after total Unit demand was met")
            continue
        group_total_die = sum(item.bin_quantity for item in available)
        target_required_die = remaining_total_units * (ratio1 + ratio2)
        diagnostics.append(
            f"group={package}|{supplier}; sum_bin_quantity={group_total_die}; target_required_die={target_required_die}; theoretical_unit_upper_bound={group_total_die // (ratio1 + ratio2)}"
        )
        blocked_by_reuse_count = 0
        best_units_seen = 0
        best_valid_seen: SearchState | None = None
        while available and remaining_total_units > 0:
            current_target_units = remaining_total_units
            best = find_best_mother_lot(available, (ratio1, ratio2), current_target_units, args.max_waste, args.beam_width)
            if best is None:
                best_effort = find_best_effort_mother_lot(
                    available,
                    (ratio1, ratio2),
                    current_target_units,
                    args.max_waste,
                    args.beam_width,
                )
                if best_effort is not None:
                    match_id = f"BE{len(best_effort_matches) + 1:04d}"
                    best_effort_matches.append(
                        state_to_summary_row(
                            best_effort,
                            available,
                            (ratio1, ratio2),
                            package,
                            supplier,
                            ratio_text,
                            match_id,
                        )
                    )
                    best_effort_assignments.extend(
                        state_to_assignment_rows(best_effort, available, match_id, package, supplier)
                    )
                suggestions.extend(
                    make_group_suggestions(
                        package,
                        supplier,
                        available,
                        (ratio1, ratio2),
                        current_target_units,
                        args.max_waste,
                        args.beam_width,
                        blocked_by_reuse_count,
                        best_valid_seen,
                        best_effort,
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
            before_remaining_units = remaining_total_units
            after_remaining_units = max(0, remaining_total_units - units)
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
                    "TotalUnitDemand": args.total_units,
                    "RemainingUnitBefore": before_remaining_units,
                    "RemainingUnitAfter": after_remaining_units,
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
            remaining_total_units = after_remaining_units
            if remaining_total_units <= 0:
                for item in available:
                    unused_notes.setdefault(item.row_id, "not needed after total Unit demand was met")
                break
            sequence += 1
        if remaining_total_units > 0 and best_units_seen and best_units_seen < remaining_total_units:
            suggestions.extend(
                make_group_suggestions(
                    package,
                    supplier,
                    available or groups[key],
                    (ratio1, ratio2),
                    remaining_total_units,
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

    diagnostics.append(f"total_units={args.total_units}")
    diagnostics.append(f"remaining_total_units={remaining_total_units}")
    diagnostics.append(f"max_waste={args.max_waste}")
    diagnostics.append(f"allow_lot_reuse={args.allow_lot_reuse}")
    diagnostics.append(f"max_bin_grade={args.max_bin_grade}")
    diagnostics.append(f"beam_width={args.beam_width}")
    diagnostics.append("search_strategy=fast_subset_sum_then_beam_fallback")
    diagnostics.append(f"mother_lot_count={len(mother_lots)}")
    diagnostics.append(f"assignment_row_count={len(assignments)}")
    diagnostics.append(f"best_effort_match_count={len(best_effort_matches)}")
    diagnostics.append(f"best_effort_assignment_row_count={len(best_effort_assignments)}")
    diagnostics.append(f"unused_row_count={len(unused_inventory)}")
    return mother_lots, assignments, unused_inventory, diagnostics, suggestions, best_effort_matches, best_effort_assignments


def write_outputs(
    output_path: Path,
    mother_lots: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    unused: list[dict[str, Any]],
    diagnostics: list[str],
    suggestions: list[dict[str, Any]],
    best_effort_matches: list[dict[str, Any]],
    best_effort_assignments: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower()
    diag_rows = [{"Diagnostic": line} for line in diagnostics]
    if suffix == ".xlsx":
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            pd.DataFrame(summary_rows).to_excel(writer, sheet_name="summary", index=False)
            pd.DataFrame(mother_lots).to_excel(writer, sheet_name="mother_lots", index=False)
            pd.DataFrame(assignments).to_excel(writer, sheet_name="lot_assignments", index=False)
            pd.DataFrame(best_effort_matches).to_excel(writer, sheet_name="best_effort_matches", index=False)
            pd.DataFrame(best_effort_assignments).to_excel(writer, sheet_name="best_effort_assignments", index=False)
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
                    "best_effort_matches": best_effort_matches,
                    "best_effort_assignments": best_effort_assignments,
                    "unused_inventory": unused,
                    "diagnostics": diagnostics,
                    "optimization_suggestions": suggestions,
                    "summary": summary_rows,
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
            ("summary", summary_rows),
            ("mother_lots", mother_lots),
            ("lot_assignments", assignments),
            ("best_effort_matches", best_effort_matches),
            ("best_effort_assignments", best_effort_assignments),
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
    parser.add_argument("--total-units", type=int, help="Total Unit demand for this planning run.")
    parser.add_argument("--target-units", type=int, help="Deprecated alias for --total-units.")
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
    if args.total_units is None:
        args.total_units = args.target_units
    if args.total_units is None:
        raise ValueError("--total-units is required.")
    if args.total_units <= 0:
        raise ValueError("--total-units must be positive.")
    args.target_units = args.total_units
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
        mother_lots, assignments, unused, diagnostics, suggestions, best_effort_matches, best_effort_assignments = plan(items, rules, args)
        diagnostics.insert(0, f"data_sheet={selected_data_sheet}")
        diagnostics.insert(1, f"rules_sheet={args.rules_sheet}")
        diagnostics.append(f"optimization_suggestion_count={len(suggestions)}")
        summary_rows = build_summary_rows(
            mother_lots,
            assignments,
            unused,
            diagnostics,
            suggestions,
            best_effort_matches,
            args,
        )
        write_outputs(
            args.output,
            mother_lots,
            assignments,
            unused,
            diagnostics,
            suggestions,
            best_effort_matches,
            best_effort_assignments,
            summary_rows,
        )
        print(f"Wrote {args.output}")
        print(f"Mother lots: {len(mother_lots)}")
        print(f"Assignment rows: {len(assignments)}")
        print(f"Best-effort matches: {len(best_effort_matches)}")
        print(f"Unused rows: {len(unused)}")
        print(f"Optimization suggestions: {len(suggestions)}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
