#!/usr/bin/env python3
"""Validate and allocate semiconductor wafer Die data.

The input is a normalized JSON payload described in ../references/input_schema.md.
The default backend is a deterministic pure-Python heuristic and needs no
third-party solver. OR-Tools CP-SAT remains an optional exact backend.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import difflib
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ALIASES = {
    "package": ["PACKAGE", "Package", "package", "产品类型"],
    "supplier": ["供应商", "Supplier", "supplier"],
    "lot": ["Fab LotID", "Fab Lot Id", "FabLotID", "LotID", "批次id", "批次ID"],
    "grade": ["Bin Grade", "BinGrade", "grade", "等级"],
    "quantity": ["Bin Quanity", "Bin Quantity", "BinQuantity", "quantity", "数量"],
    "wafer": ["T7 Code", "T7Code", "Wafer ID", "WaferID", "wafer_id", "晶圆ID"],
    "lot_wafer_qty": ["Lot Wafer QTY", "Lot Wafer Qty", "LotWaferQTY", "Wafer QTY"],
    "date": ["Create Date", "CreateDate", "create_date", "生产时间", "生产日期"],
    "sale": ["Wafer Sale", "WaferSale", "wafer_sale"],
    "ratio": ["层数配比", "配比", "Ratio", "ratio"],
}


def norm_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[\s_\-–—/\\().,:：]+", "", str(value).strip().casefold())


def first_value(row: Dict[str, Any], logical_name: str, default: Any = None) -> Any:
    for key in ALIASES[logical_name]:
        if key in row:
            return row[key]
    wanted = norm_text(logical_name)
    for key, value in row.items():
        if norm_text(key) == wanted:
            return value
    return default


def number(value: Any, field_name: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"{field_name} is empty")
    try:
        result = float(str(value).replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} is not numeric: {value!r}") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number: {value!r}")
    if result.is_integer():
        return int(result)
    return result


def integer_number(value: Any, field_name: str) -> int:
    parsed = number(value, field_name)
    if isinstance(parsed, float) and not parsed.is_integer():
        raise ValueError(f"{field_name} must be an integer count: {value!r}")
    return int(parsed)


def grade(value: Any) -> str:
    text = str(value).strip().upper()
    if text.endswith(".0"):
        text = text[:-2]
    if text == "X":
        return text
    if text.isdigit() and 1 <= int(text) <= 9:
        return text
    raise ValueError(f"invalid Bin Grade: {value!r}; expected 1-9 or X")


def date_key(value: Any) -> Tuple[int, str]:
    text = str(value or "").strip()
    if not text:
        return (1, "9999-12-31")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y"):
        try:
            return (0, dt.datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass
    try:
        return (0, dt.datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat())
    except ValueError:
        return (1, text)


def parse_ratio(value: Any) -> Tuple[int, int]:
    text = str(value or "").strip().replace("：", ":")
    match = re.fullmatch(r"\s*(\d+)\s*[:：]\s*(\d+)\s*", text)
    if not match:
        raise ValueError(f"invalid ratio {value!r}; expected e.g. 2:6")
    a, b = int(match.group(1)), int(match.group(2))
    if a <= 0 or b <= 0:
        raise ValueError("ratio values must be positive")
    return a, b


@dataclass
class Wafer:
    wafer_id: str
    lot_id: str
    package: str
    supplier: str
    create_date: str
    sale: str
    grades: Dict[str, float] = field(default_factory=dict)

    def selected_quantity(self, selected_grades: Sequence[str]) -> float:
        return sum(self.grades.get(g, 0) for g in selected_grades)


@dataclass
class Item:
    item_id: str
    lot_id: str
    wafer_ids: List[str]
    process_quantities: Dict[str, float]
    sale: str
    create_date: str
    priority_date: Tuple[int, str]
    wafer_details: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def quantity(self) -> float:
        return sum(self.process_quantities.values())


def ratio_match(package: str, supplier: str, table3: List[Dict[str, Any]], threshold: float = 0.80) -> Tuple[Optional[Tuple[int, int]], List[str], Optional[Dict[str, Any]]]:
    supplier_norm = norm_text(supplier)
    candidates = []
    for row in table3:
        if norm_text(first_value(row, "supplier")) != supplier_norm:
            continue
        package_value = first_value(row, "package")
        score = difflib.SequenceMatcher(None, norm_text(package), norm_text(package_value)).ratio()
        if score >= threshold:
            candidates.append((score, row))
    if not candidates:
        return None, [f"no PACKAGE/Supplier ratio match for package={package!r}, supplier={supplier!r}"], None
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score = candidates[0][0]
    tied = [row for score, row in candidates if best_score - score < 0.03]
    ratios = set()
    errors = []
    for row in tied:
        try:
            ratios.add(parse_ratio(first_value(row, "ratio")))
        except ValueError as exc:
            errors.append(str(exc))
    if len(ratios) != 1:
        errors.append("ambiguous PACKAGE/Supplier match maps to different ratios")
        return None, errors, tied[0] if tied else None
    return next(iter(ratios)), errors, tied[0]


def read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    return payload


def normalize_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Wafer], Dict[str, Any], List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    params = dict(payload.get("parameters") or {})
    table1 = payload.get("table1") or []
    table2 = payload.get("table2") or []
    table3 = payload.get("table3") or []
    if not isinstance(table1, list) or not isinstance(table2, list) or not isinstance(table3, list):
        return params, [], {}, ["table1, table2, and table3 must be arrays"], warnings
    required = ["target_units", "bin_grades", "max_waste_per_custom_lot", "max_lots_per_custom_lot"]
    for key in required:
        if key not in params:
            errors.append(f"missing parameter: {key}")
    if errors:
        return params, [], {}, errors, warnings

    try:
        target = integer_number(params["target_units"], "target_units")
        tolerance = float(params.get("tolerance", 0.20))
        max_waste = integer_number(params["max_waste_per_custom_lot"], "max_waste_per_custom_lot")
        max_units = integer_number(params.get("max_units_per_custom_lot", 20000), "max_units_per_custom_lot")
        max_lots = integer_number(params["max_lots_per_custom_lot"], "max_lots_per_custom_lot")
        selected_grades = [grade(g) for g in params["bin_grades"]]
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
        return params, [], {}, errors, warnings
    if target <= 0 or tolerance < 0 or max_units <= 0 or max_lots <= 0:
        errors.append("target_units, max_units_per_custom_lot, and max_lots_per_custom_lot must be positive; tolerance must be non-negative")
    # A and B are internal thickness-role labels, never user inputs.
    process_a = "A"
    process_b = "B"

    packages = {str(first_value(row, "package", "")).strip() for row in table1 if first_value(row, "package") is not None}
    suppliers = {str(first_value(row, "supplier", "")).strip() for row in table1 if first_value(row, "supplier") is not None}
    package = str(params.get("package") or (next(iter(packages)) if len(packages) == 1 else "")).strip()
    supplier = str(params.get("supplier") or (next(iter(suppliers)) if len(suppliers) == 1 else "")).strip()
    if not package or not supplier:
        errors.append("package and supplier are required when the input is not unambiguous")
    ratio, ratio_errors, ratio_row = ratio_match(package, supplier, table3)
    errors.extend(ratio_errors)
    if errors:
        return params, [], {"package": package, "supplier": supplier, "ratio": ratio}, errors, warnings

    sale_by_lot: Dict[str, str] = defaultdict(lambda: "")
    for row in table2:
        lot_id = str(first_value(row, "lot", "")).strip()
        sale = str(first_value(row, "sale", "")).strip().upper()
        if not lot_id:
            warnings.append("table2 contains a row without Fab LotID")
            continue
        if sale not in {"N", "Y", ""}:
            warnings.append(f"unexpected Wafer Sale={sale!r} for lot {lot_id}; treating as unknown")
        if sale == "N" or not sale_by_lot[lot_id]:
            sale_by_lot[lot_id] = sale

    by_wafer: Dict[str, Wafer] = {}
    for idx, row in enumerate(table1, start=1):
        try:
            wafer_id = str(first_value(row, "wafer", "")).strip()
            lot_id = str(first_value(row, "lot", "")).strip()
            row_package = str(first_value(row, "package", "")).strip()
            row_supplier = str(first_value(row, "supplier", "")).strip()
            if not wafer_id or not lot_id:
                raise ValueError("missing T7 Code or Fab LotID")
            if norm_text(row_supplier) != norm_text(supplier) or norm_text(row_package) != norm_text(package):
                continue
            g = grade(first_value(row, "grade"))
            q = integer_number(first_value(row, "quantity"), f"table1 row {idx} Bin Quantity")
            date = str(first_value(row, "date", "")).strip()
            if wafer_id not in by_wafer:
                by_wafer[wafer_id] = Wafer(wafer_id, lot_id, row_package, row_supplier, date, sale_by_lot.get(lot_id, "UNKNOWN"))
            wafer = by_wafer[wafer_id]
            metadata = (wafer.lot_id, norm_text(wafer.package), norm_text(wafer.supplier))
            incoming = (lot_id, norm_text(row_package), norm_text(row_supplier))
            if metadata != incoming:
                raise ValueError(f"conflicting metadata for T7 Code {wafer_id}")
            wafer.grades[g] = wafer.grades.get(g, 0) + q
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))

    missing_sale = sorted({w.lot_id for w in by_wafer.values() if w.sale == "UNKNOWN"})
    if missing_sale:
        warnings.append(f"missing Wafer Sale for Lot IDs: {', '.join(missing_sale[:20])}")
    wafers = [w for w in by_wafer.values() if w.selected_quantity(selected_grades) > 0]
    if not wafers:
        errors.append("no wafers contain any selected Bin Grade after filtering")
    metadata = {
        "package": package,
        "supplier": supplier,
        "ratio": {"process_a": process_a, "process_b": process_b, "a": ratio[0], "b": ratio[1]},
        "matched_table3_row": ratio_row,
        "selected_grades": selected_grades,
        "target_units": target,
        "tolerance": tolerance,
        "max_waste_per_custom_lot": max_waste,
        "max_units_per_custom_lot": max_units,
        "max_units_defaulted": "max_units_per_custom_lot" not in params,
        "max_lots_per_custom_lot": max_lots,
        "allow_lot_reuse": bool(params.get("allow_lot_reuse", False)),
    }
    return params, wafers, metadata, errors, warnings


def make_items(wafers: Sequence[Wafer], metadata: Dict[str, Any], allow_lot_reuse: bool) -> List[Item]:
    """Create one decision item per wafer.

    A/B is deliberately not read from the input. The solver assigns the
    whole wafer to one of the two thickness roles later.
    """
    items = []
    for w in wafers:
        quantity = w.selected_quantity(metadata["selected_grades"])
        detail = {
            "t7_code": w.wafer_id,
            "fab_lot_id": w.lot_id,
            "selected_grade_quantities": {g: w.grades.get(g, 0) for g in metadata["selected_grades"]},
            "selected_die_quantity": quantity,
        }
        items.append(Item(w.wafer_id, w.lot_id, [w.wafer_id], {"UNASSIGNED": quantity}, w.sale, w.create_date, date_key(w.create_date), [detail]))
    return items


def assign_roles(item_list: Sequence[Item], ratio: Tuple[int, int], pa: str, pb: str) -> Dict[str, str]:
    """Assign each whole wafer to A or B for the fallback solver.

    For small Custom Lots, enumerate the two-way partition and minimize
    waste. For larger Lots, use a ratio-targeted greedy partition.
    """
    items = list(item_list)
    if not items:
        return {}
    total = sum(i.quantity for i in items)
    target_a = total * ratio[0] / (ratio[0] + ratio[1])
    best_roles: Dict[str, str] = {}
    best_score = None
    if len(items) <= 20:
        for mask in range(1 << len(items)):
            a = sum(items[index].quantity for index in range(len(items)) if mask & (1 << index))
            b = total - a
            units = min(math.floor(a / ratio[0]), math.floor(b / ratio[1]))
            waste = total - units * (ratio[0] + ratio[1])
            score = (waste, -units, abs(a - target_a))
            if best_score is None or score < best_score:
                best_score = score
                best_roles = {item.item_id: (pa if mask & (1 << index) else pb) for index, item in enumerate(items)}
        return best_roles
    a = 0
    for item in sorted(items, key=lambda i: (-i.quantity, preference_key(i))):
        if abs((a + item.quantity) - target_a) < abs(a - target_a):
            best_roles[item.item_id] = pa
            a += item.quantity
        else:
            best_roles[item.item_id] = pb
    return best_roles


def calc_lot(item_list: Sequence[Item], ratio: Tuple[int, int], pa: str, pb: str, roles: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    roles = roles or assign_roles(item_list, ratio, pa, pb)
    a = sum(i.quantity for i in item_list if roles.get(i.item_id) == pa)
    b = sum(i.quantity for i in item_list if roles.get(i.item_id) == pb)
    units = min(math.floor(a / ratio[0]), math.floor(b / ratio[1]))
    waste = a + b - units * (ratio[0] + ratio[1])
    return {"units": int(units), "a_die": a, "b_die": b, "required_a": units * ratio[0], "required_b": units * ratio[1], "waste": waste}


def details_with_roles(item_list: Sequence[Item], roles: Dict[str, str]) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    for item in item_list:
        for detail in item.wafer_details:
            enriched = dict(detail)
            enriched["assigned_process"] = roles.get(item.item_id)
            details.append(enriched)
    return details


def wafer_multiple_metrics(item_list: Sequence[Item], ratio: Tuple[int, int]) -> Dict[str, Any]:
    """Expose the manual wafer-count heuristic without making it a constraint."""
    count = len({wafer_id for item in item_list for wafer_id in item.wafer_ids})
    base = ratio[0] + ratio[1]
    return {
        "wafer_count": count,
        "wafer_count_multiple_base": base,
        "wafer_count_remainder": count % base,
        "wafer_count_is_multiple": count % base == 0,
    }


def preference_key(item: Item) -> Tuple[int, Tuple[int, str], str]:
    sale_rank = 0 if item.sale == "N" else 1 if item.sale == "Y" else 2
    return sale_rank, item.priority_date, item.item_id


def greedy_allocate(items: List[Item], metadata: Dict[str, Any], relax_lot_limit: bool = False) -> Tuple[List[Dict[str, Any]], List[Item], Optional[int]]:
    pa = metadata["ratio"]["process_a"]
    pb = metadata["ratio"]["process_b"]
    ratio = (metadata["ratio"]["a"], metadata["ratio"]["b"])
    max_waste = metadata["max_waste_per_custom_lot"]
    max_units = metadata["max_units_per_custom_lot"]
    max_lots = len({i.lot_id for i in items}) if relax_lot_limit else metadata["max_lots_per_custom_lot"]
    active_lots: Optional[set] = None
    if metadata["allow_lot_reuse"]:
        # Lot reuse permits a Lot's wafers to be distributed across Custom Lots,
        # but a participating Lot is still all-or-nothing. Select whole Lots
        # first, then pack their individual wafers.
        by_lot: Dict[str, List[Item]] = defaultdict(list)
        for item in items:
            by_lot[item.lot_id].append(item)
        ordered_lots = sorted(by_lot, key=lambda lot: min(preference_key(i) for i in by_lot[lot]))
        active_lots = set()
        supply_total = 0
        lower = math.ceil(metadata["target_units"] * (1 - metadata["tolerance"]))
        while ordered_lots and supply_total < lower * (ratio[0] + ratio[1]):
            lot_id = ordered_lots.pop(0)
            active_lots.add(lot_id)
            supply_total += sum(i.quantity for i in by_lot[lot_id])
        if not active_lots and by_lot:
            active_lots.add(next(iter(by_lot)))
        metadata["_greedy_active_lots"] = sorted(active_lots)
        remaining = sorted([i for i in items if i.lot_id in active_lots], key=preference_key)
    else:
        remaining = sorted(items, key=preference_key)
    result: List[Dict[str, Any]] = []
    custom_id = 1
    while remaining:
        chosen: List[Item] = []
        pool = list(remaining)
        # Seed with a preferred wafer. Without Lot reuse, take the whole Lot
        # together so that one Lot cannot be split across Custom Lots.
        seed = pool[0]
        seed_bundle = [i for i in pool if i.lot_id == seed.lot_id] if not metadata["allow_lot_reuse"] else [seed]
        chosen.extend(seed_bundle)
        seed_ids = {i.item_id for i in seed_bundle}
        pool = [i for i in pool if i.item_id not in seed_ids]
        while pool:
            current_roles = assign_roles(chosen, ratio, pa, pb)
            current = calc_lot(chosen, ratio, pa, pb, current_roles)
            candidates = []
            used_lots = {i.lot_id for i in chosen}
            for idx, item in enumerate(pool):
                if item.lot_id not in used_lots and len(used_lots) >= max_lots:
                    continue
                bundle = [i for i in pool if i.lot_id == item.lot_id] if not metadata["allow_lot_reuse"] else [item]
                projected_roles = assign_roles(chosen + bundle, ratio, pa, pb)
                projected = calc_lot(chosen + bundle, ratio, pa, pb, projected_roles)
                if projected["units"] > max_units:
                    continue
                valid_bonus = 0 if projected["waste"] <= max_waste else 1
                wafer_multiple_penalty = 0 if len({w for x in chosen + bundle for w in x.wafer_ids}) % (ratio[0] + ratio[1]) == 0 else 1
                candidates.append((valid_bonus, wafer_multiple_penalty, abs(projected["waste"] - max_waste / 2), -projected["units"], preference_key(item), idx, bundle, projected))
            if not candidates:
                break
            candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
            best = candidates[0]
            projected = best[-1]
            # Once the bin is valid, only add an item if it improves Unit count or waste.
            if chosen and current["a_die"] > 0 and current["b_die"] > 0 and current["waste"] <= max_waste:
                if projected["units"] < current["units"] and projected["waste"] >= current["waste"]:
                    break
            bundle_ids = {i.item_id for i in best[-2]}
            chosen.extend(best[-2])
            pool = [i for i in pool if i.item_id not in bundle_ids]
        roles = assign_roles(chosen, ratio, pa, pb)
        stats = calc_lot(chosen, ratio, pa, pb, roles)
        if stats["units"] <= 0 or stats["waste"] > max_waste or len({i.lot_id for i in chosen}) > max_lots:
            # Try to find a valid pair from the remaining pool before giving up.
            found = None
            for i, left in enumerate(remaining):
                left_bundle = [x for x in remaining if x.lot_id == left.lot_id] if not metadata["allow_lot_reuse"] else [left]
                for j in range(i + 1, min(len(remaining), i + 80)):
                    right = remaining[j]
                    if right.item_id in {x.item_id for x in left_bundle}:
                        continue
                    right_bundle = [x for x in remaining if x.lot_id == right.lot_id] if not metadata["allow_lot_reuse"] else [right]
                    if not metadata["allow_lot_reuse"] and left.lot_id == right.lot_id:
                        continue
                    trial = left_bundle + right_bundle
                    trial_roles = assign_roles(trial, ratio, pa, pb)
                    s = calc_lot(trial, ratio, pa, pb, trial_roles)
                    if s["units"] > 0 and s["units"] <= max_units and s["waste"] <= max_waste:
                        found = (i, j, trial, s, trial_roles)
                        break
                if found:
                    break
            if not found:
                break
            i, j, chosen, stats, roles = found
        chosen_ids = {i.item_id for i in chosen}
        remaining = [item for item in remaining if item.item_id not in chosen_ids]
        result.append({
            "custom_lot_id": f"CL-{custom_id:03d}",
            **stats,
            **wafer_multiple_metrics(chosen, ratio),
            "fab_lot_ids": sorted({i.lot_id for i in chosen}),
            "t7_codes": [w for i in chosen for w in i.wafer_ids],
            "wafer_details": details_with_roles(chosen, roles),
            "wafer_sale": sorted({i.sale for i in chosen}),
            "create_dates": sorted({i.create_date for i in chosen if i.create_date}),
        })
        custom_id += 1
    if active_lots is not None:
        used_lots = {lot_id for custom in result for lot_id in custom["fab_lot_ids"]}
        incomplete = {item.lot_id for item in remaining if item.lot_id in active_lots}
        incomplete.update(active_lots - used_lots if result else active_lots)
        metadata["_greedy_incomplete_lots"] = sorted(incomplete)
    relaxed = max_lots if relax_lot_limit else None
    return result, remaining, relaxed


def cp_sat_allocate(items: List[Item], metadata: Dict[str, Any], relax_lot_limit: bool = False) -> Tuple[List[Dict[str, Any]], List[Item], Optional[int]]:
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise RuntimeError("OR-Tools is not installed") from exc
    pa = metadata["ratio"]["process_a"]
    pb = metadata["ratio"]["process_b"]
    ratio = (int(metadata["ratio"]["a"]), int(metadata["ratio"]["b"]))
    target = int(metadata["target_units"])
    tolerance = float(metadata["tolerance"])
    lower = math.ceil(target * (1 - tolerance))
    upper = math.floor(target * (1 + tolerance))
    max_units = int(metadata["max_units_per_custom_lot"])
    max_waste = int(metadata["max_waste_per_custom_lot"])
    max_lots = len({i.lot_id for i in items}) if relax_lot_limit else int(metadata["max_lots_per_custom_lot"])
    # The cap can be overridden in the payload for large or highly fragmented plans.
    c_count = int(metadata.get("max_custom_lots", max(1, min(len(items), math.ceil(max(upper, 1) / max(max_units, 1)) + 10))))
    c_count = max(1, min(c_count, max(1, len(items))))
    model = cp_model.CpModel()
    # x[i,c,p] means wafer i is assigned to Custom Lot c and role p.
    x = {(i, c, p): model.NewBoolVar(f"x_{i}_{c}_{p}") for i in range(len(items)) for c in range(c_count) for p in (pa, pb)}
    selected = [model.NewBoolVar(f"selected_{c}") for c in range(c_count)]
    units = [model.NewIntVar(0, max_units, f"units_{c}") for c in range(c_count)]
    waste = [model.NewIntVar(0, max_waste, f"waste_{c}") for c in range(c_count)]
    for i in range(len(items)):
        model.Add(sum(x[i, c, p] for c in range(c_count) for p in (pa, pb)) <= 1)
    for c in range(c_count):
        assigned = {i: sum(x[i, c, p] for p in (pa, pb)) for i in range(len(items))}
        model.Add(sum(assigned[i] for i in range(len(items))) >= selected[c])
        for i in range(len(items)):
            model.Add(assigned[i] <= selected[c])
        a_total = sum(int(items[i].quantity) * x[i, c, pa] for i in range(len(items)))
        b_total = sum(int(items[i].quantity) * x[i, c, pb] for i in range(len(items)))
        model.Add(a_total >= ratio[0] * units[c])
        model.Add(b_total >= ratio[1] * units[c])
        model.Add(waste[c] == a_total + b_total - (ratio[0] + ratio[1]) * units[c])
        model.Add(waste[c] <= max_waste * selected[c])
        lot_flags = []
        for lot_id in sorted({item.lot_id for item in items}):
            indexes = [i for i, item in enumerate(items) if item.lot_id == lot_id]
            flag = model.NewBoolVar(f"lot_{lot_id}_{c}")
            lot_flags.append(flag)
            for i in indexes:
                model.Add(assigned[i] <= flag)
            model.Add(flag <= sum(assigned[i] for i in indexes))
        model.Add(sum(lot_flags) <= max_lots)
    # A selected Lot is fully allocated. In reuse mode its wafers may land in different Custom Lots.
    for lot_id in sorted({item.lot_id for item in items}):
        indexes = [i for i, item in enumerate(items) if item.lot_id == lot_id]
        lot_used = model.NewBoolVar(f"lot_used_{lot_id}")
        for i in indexes:
            model.Add(sum(x[i, c, p] for c in range(c_count) for p in (pa, pb)) == lot_used)
        if not metadata["allow_lot_reuse"]:
            for c in range(c_count):
                for i in indexes[1:]:
                    model.Add(sum(x[i, c, p] for p in (pa, pb)) == sum(x[indexes[0], c, p] for p in (pa, pb)))
    total_units = model.NewIntVar(lower, upper, "total_units")
    model.Add(total_units == sum(units))
    deviation = model.NewIntVar(0, max(target, upper) + target + 1, "deviation")
    model.AddAbsEquality(deviation, total_units - target)
    model.Minimize(deviation * 1000000 + sum(waste) * 1000 + sum((0 if item.sale == "N" else 1) * int(item.quantity) * x[i, c, p] for i, item in enumerate(items) for c in range(c_count) for p in (pa, pb)))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(metadata.get("time_limit_seconds", 60))
    solver.parameters.num_search_workers = int(metadata.get("num_search_workers", 8))
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return [], items, max_lots if relax_lot_limit else None
    result = []
    used_ids = set()
    for c in range(c_count):
        chosen = [items[i] for i in range(len(items)) if solver.Value(x[i, c, pa]) or solver.Value(x[i, c, pb])]
        if not chosen:
            continue
        used_ids.update(item.item_id for item in chosen)
        roles = {items[i].item_id: (pa if solver.Value(x[i, c, pa]) else pb) for i in range(len(items)) if solver.Value(x[i, c, pa]) or solver.Value(x[i, c, pb])}
        stats = calc_lot(chosen, ratio, pa, pb, roles)
        result.append({
            "custom_lot_id": f"CL-{len(result)+1:03d}",
            **stats,
            **wafer_multiple_metrics(chosen, ratio),
            "fab_lot_ids": sorted({i.lot_id for i in chosen}),
            "t7_codes": [w for i in chosen for w in i.wafer_ids],
            "wafer_details": details_with_roles(chosen, roles),
            "wafer_sale": sorted({i.sale for i in chosen}),
            "create_dates": sorted({i.create_date for i in chosen if i.create_date}),
        })
    remaining = [item for item in items if item.item_id not in used_ids]
    return result, remaining, max_lots if relax_lot_limit else None


def result_summary(custom_lots: List[Dict[str, Any]], metadata: Dict[str, Any], solver_name: str, warnings: List[str], errors: List[str], unused: List[Item], relaxed_max_lots: Optional[int]) -> Dict[str, Any]:
    total_units = sum(int(c["units"]) for c in custom_lots)
    total_waste = sum(c["waste"] for c in custom_lots)
    target = metadata.get("target_units", 0)
    tolerance = metadata.get("tolerance", 0.2)
    lower = math.ceil(target * (1 - tolerance))
    upper = math.floor(target * (1 + tolerance))
    incomplete_lots = metadata.get("_greedy_incomplete_lots", [])
    if incomplete_lots:
        warnings = list(warnings) + [f"greedy fallback did not fully allocate participating Lot IDs: {', '.join(incomplete_lots)}"]
    feasible = not errors and not incomplete_lots and lower <= total_units <= upper and all(c["units"] > 0 and c["waste"] <= metadata["max_waste_per_custom_lot"] for c in custom_lots)
    status = "feasible" if feasible else "infeasible"
    if solver_name == "greedy" and feasible:
        status = "heuristic_feasible"
    return {
        "status": status,
        "solver": solver_name,
        "summary": {
            "target_units": target,
            "allowed_unit_range": [lower, upper],
            "achieved_units": total_units,
            "deviation": total_units - target,
            "custom_lot_count": len(custom_lots),
            "total_waste": total_waste,
            "relaxed_max_lots_per_custom_lot": relaxed_max_lots,
        },
        "metadata": metadata,
        "custom_lots": custom_lots,
        "unused_items": [{"item_id": i.item_id, "fab_lot_id": i.lot_id, "t7_codes": i.wafer_ids} for i in unused],
        "warnings": warnings,
        "errors": errors,
    }


def solve(payload: Dict[str, Any], requested_solver: str = "auto") -> Dict[str, Any]:
    params, wafers, metadata, errors, warnings = normalize_payload(payload)
    if errors:
        return {"status": "validation_error", "solver": None, "metadata": metadata, "errors": errors, "warnings": warnings, "custom_lots": [], "unused_items": []}
    items = make_items(wafers, metadata, bool(metadata["allow_lot_reuse"]))
    pa = metadata["ratio"]["process_a"]
    pb = metadata["ratio"]["process_b"]
    ratio = (metadata["ratio"]["a"], metadata["ratio"]["b"])
    total_supply = sum(i.quantity for i in items)
    lower = math.ceil(metadata["target_units"] * (1 - metadata["tolerance"]))
    total_required = lower * (ratio[0] + ratio[1])
    if total_supply < total_required:
        warnings.append(f"selected-grade Die supply {total_supply} is below minimum required {total_required}")
    metadata["selected_grade_die_supply"] = total_supply
    metadata["minimum_required_die_supply"] = total_required
    solver_name = "cp-sat"
    if requested_solver == "greedy":
        solver_name = "greedy"
        lots, unused, relaxed = greedy_allocate(items, metadata, False)
    else:
        try:
            lots, unused, relaxed = cp_sat_allocate(items, metadata, False)
        except RuntimeError as exc:
            if requested_solver == "cp-sat":
                return {"status": "solver_unavailable", "solver": "cp-sat", "metadata": metadata, "errors": [str(exc)], "warnings": warnings, "custom_lots": [], "unused_items": []}
            solver_name = "greedy"
            warnings.append(str(exc) + "; using the dependency-free pure-Python heuristic backend")
            lots, unused, relaxed = greedy_allocate(items, metadata, False)
    preliminary = result_summary(lots, metadata, solver_name, warnings, errors, unused, relaxed)
    if preliminary["status"] in {"infeasible", "validation_error"} and not metadata["allow_lot_reuse"]:
        # Only relax b after trying the original model, preserving the user's priority on a.
        if requested_solver == "cp-sat" and solver_name == "cp-sat":
            try:
                lots2, unused2, relaxed2 = cp_sat_allocate(items, metadata, True)
            except RuntimeError:
                lots2, unused2, relaxed2 = [], items, len({i.lot_id for i in items})
        else:
            lots2, unused2, relaxed2 = greedy_allocate(items, metadata, True)
        relaxed_result = result_summary(lots2, metadata, solver_name, warnings, errors, unused2, relaxed2)
        if relaxed_result["status"] in {"feasible", "heuristic_feasible"}:
            relaxed_result["warnings"].append("original max_lots_per_custom_lot was relaxed after no solution under the hard Lot-count limit")
            return relaxed_result
    return preliminary


def demo_payload() -> Dict[str, Any]:
    rows = []
    def add(wafer: str, lot: str, process: str, a: int, b: int, date: str):
        rows.extend([
            {"PACKAGE": "P-100", "供应商": "Supplier-A", "Fab LotID": lot, "Bin Grade": "1", "Bin Quanity": a if process == "A" else b, "T7 Code": wafer, "Lot Wafer QTY": 2, "Create Date": date},
        ])
    add("W-A1", "L-001", "A", 20, 0, "2026-01-01")
    add("W-B1", "L-002", "B", 0, 60, "2026-01-02")
    add("W-A2", "L-003", "A", 20, 0, "2026-01-03")
    add("W-B2", "L-004", "B", 0, 60, "2026-01-04")
    return {"table1": rows, "table2": [{"Fab LotID": f"L-00{i}", "Wafer Sale": "N" if i < 3 else "Y"} for i in range(1, 5)], "table3": [{"PACKAGE": "P100", "供应商": "Supplier-A", "层数配比": "2:6"}], "parameters": {"target_units": 20, "tolerance": 0.2, "bin_grades": ["1"], "max_waste_per_custom_lot": 4, "allow_lot_reuse": False, "max_units_per_custom_lot": 20, "max_lots_per_custom_lot": 5, "package": "P-100", "supplier": "Supplier-A"}}


def main() -> int:
    parser = argparse.ArgumentParser(description="Allocate whole wafers into Custom Lots")
    parser.add_argument("--input", help="normalized JSON payload")
    parser.add_argument("--output", help="output JSON path; defaults to stdout")
    parser.add_argument("--solver", choices=["auto", "cp-sat", "greedy"], default="auto")
    parser.add_argument("--demo", action="store_true", help="run a small built-in example")
    args = parser.parse_args()
    if args.demo:
        payload = demo_payload()
    elif args.input:
        payload = read_json(args.input)
    else:
        parser.error("provide --input or --demo")
    output = solve(payload, args.solver)
    text = json.dumps(output, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if output.get("status") in {"feasible", "heuristic_feasible"} else 2


if __name__ == "__main__":
    sys.exit(main())
