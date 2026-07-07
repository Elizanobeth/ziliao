from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from math import comb
from typing import Any

from .common import (
    DieAllocationError,
    item_sort_key,
    load_json,
    normalize_reuse_rule,
    parse_nonnegative_int,
    parse_positive_int,
    save_json,
)


SIDES = ("A", "B")


def _params(
    loss_cap: str | int,
    unit_cap: str | int,
    lot_cap: str | int,
    reuse_rule: str,
    time_limit: int,
    max_batches: int | None,
    candidate_limit: int,
    max_combo_lots: int,
    max_combo_items: int,
    max_side_items: int,
    node_limit: int,
    large_item_threshold: int,
    exact_max_combinations: int,
    exact_side_sum_limit: int,
) -> dict[str, Any]:
    return {
        "loss_cap": parse_nonnegative_int(loss_cap, "单母批最大 Die 损耗"),
        "unit_cap": parse_positive_int(unit_cap, "单母批最大 Unit 数"),
        "lot_cap": parse_positive_int(lot_cap, "单母批最大 Lot 数"),
        "reuse_rule": normalize_reuse_rule(reuse_rule),
        "time_limit_seconds": int(time_limit),
        "max_batches": max_batches,
        "candidate_limit": int(candidate_limit),
        "max_combo_lots": int(max_combo_lots),
        "max_combo_items": int(max_combo_items),
        "max_side_items": int(max_side_items),
        "node_limit": int(node_limit),
        "large_item_threshold": int(large_item_threshold),
        "exact_max_combinations": int(exact_max_combinations),
        "exact_side_sum_limit": int(exact_side_sum_limit),
    }


def _lot_item_map(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in sorted(items, key=item_sort_key):
        grouped[item["lot"]].append(item)
    return dict(grouped)


def _k_range(supply: dict[str, Any], params: dict[str, Any], target_phase: bool) -> range:
    target = int(supply["config"]["target_units"])
    unit_cap = params["unit_cap"]
    k_min = max(1, (target + unit_cap - 1) // unit_cap) if target_phase else 1
    hard = supply["stats"]["lot_count"] if params["reuse_rule"] == "no_reuse" else len(supply["items"])
    k_cap = min(hard, max(k_min + 10, 30))
    if params.get("max_batches"):
        k_cap = min(k_cap, int(params["max_batches"]))
    return range(k_min, max(k_min, k_cap) + 1)


def _best_side_assignment(
    items: list[dict[str, Any]],
    ratio: dict[str, int],
    unit_cap: int,
    loss_cap: int,
    max_side_items: int,
) -> dict[str, Any] | None:
    r_a = int(ratio["A"])
    r_b = int(ratio["B"])
    ratio_sum = r_a + r_b
    total_qty = sum(int(item["qty"]) for item in items)
    best: dict[str, Any] | None = None

    def consider(assignments: dict[str, str]) -> None:
        nonlocal best
        a_qty = sum(int(item["qty"]) for item in items if assignments[item["id"]] == "A")
        b_qty = total_qty - a_qty
        units = min(a_qty // r_a, b_qty // r_b, unit_cap)
        if units <= 0:
            return
        loss = total_qty - units * ratio_sum
        if loss < 0 or loss > loss_cap:
            return
        candidate = {
            "units": int(units),
            "loss": int(loss),
            "a_qty": int(a_qty),
            "b_qty": int(b_qty),
            "assignments": assignments,
        }
        key = (-candidate["units"], candidate["loss"], abs(a_qty * r_b - b_qty * r_a))
        best_key = (
            -best["units"],
            best["loss"],
            abs(best["a_qty"] * r_b - best["b_qty"] * r_a),
        ) if best else None
        if best is None or key < best_key:
            best = candidate

    if len(items) <= max_side_items:
        n = len(items)
        for mask in range(1, 1 << n):
            if mask == (1 << n) - 1:
                continue
            assignments = {
                item["id"]: ("A" if (mask >> idx) & 1 else "B")
                for idx, item in enumerate(items)
            }
            consider(assignments)
    else:
        target_share = r_a / ratio_sum
        ordered = sorted(items, key=lambda i: (-int(i["qty"]), i["id"]))
        for reverse in (False, True):
            running_a = 0
            assignments: dict[str, str] = {}
            iterable = list(reversed(ordered)) if reverse else ordered
            for item in iterable:
                if (running_a + int(item["qty"])) <= total_qty * target_share:
                    assignments[item["id"]] = "A"
                    running_a += int(item["qty"])
                else:
                    assignments[item["id"]] = "B"
            if "A" in assignments.values() and "B" in assignments.values():
                consider(assignments)
    return best


def _make_candidate(
    candidate_id: str,
    items: list[dict[str, Any]],
    ratio: dict[str, int],
    unit_cap: int,
    loss_cap: int,
    lot_cap: int,
    max_side_items: int,
) -> dict[str, Any] | None:
    side = _best_side_assignment(items, ratio, unit_cap, loss_cap, max_side_items)
    if not side:
        return None
    lots = sorted({item["lot"] for item in items})
    item_ids = sorted(item["id"] for item in items)
    return {
        "id": candidate_id,
        "lots": lots,
        "item_ids": item_ids,
        "assignments": side["assignments"],
        "units": side["units"],
        "loss": side["loss"],
        "a_qty": side["a_qty"],
        "b_qty": side["b_qty"],
        "lot_overflow": max(0, len(lots) - lot_cap),
    }


def _candidate_plan(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"P_{candidate['id']}",
        "candidates": [candidate],
        "lots": set(candidate["lots"]),
        "item_ids": set(candidate["item_ids"]),
        "units": candidate["units"],
        "loss": candidate["loss"],
        "lot_overflow": candidate["lot_overflow"],
        "active_count": 1,
    }


def _large_sort_orders(lot_items: dict[str, list[dict[str, Any]]], ratio_sum: int) -> list[list[str]]:
    lots = list(lot_items)
    total = {lot: sum(int(item["qty"]) for item in items) for lot, items in lot_items.items()}
    orders = [
        sorted(lots, key=lambda lot: (-total[lot], lot)),
        sorted(lots, key=lambda lot: (total[lot], lot)),
        sorted(lots, key=lambda lot: (total[lot] % ratio_sum, -total[lot], lot)),
        sorted(lots),
    ]
    unique_orders: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for order in orders:
        key = tuple(order)
        if key not in seen:
            unique_orders.append(order)
            seen.add(key)
    return unique_orders


def _generate_large_whole_lot_candidates(
    supply: dict[str, Any],
    params: dict[str, Any],
    relaxed_lot_cap: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    ratio = supply["config"]["ratio"]
    ratio_sum = int(ratio["A"]) + int(ratio["B"])
    lot_items = _lot_item_map(supply["items"])
    max_lots = params["max_combo_lots"]
    if not relaxed_lot_cap:
        max_lots = min(max_lots, params["lot_cap"])
    else:
        max_lots = min(max_lots, params["lot_cap"] + 2)
    if max_lots < 1:
        return [], ["大数据候选生成失败：max_lots 小于 1。"]

    candidates: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, ...]] = set()
    source_counts: dict[str, int] = defaultdict(int)
    lot_totals = {lot: sum(int(item["qty"]) for item in items) for lot, items in lot_items.items()}
    max_die = params["unit_cap"] * ratio_sum + params["loss_cap"]
    count = 0

    def candidate_sort_key(group: tuple[str, ...]) -> tuple[int, int, int, int, tuple[str, ...]]:
        total_qty = sum(lot_totals[lot] for lot in group)
        return (
            total_qty % ratio_sum,
            abs(max_die - total_qty),
            -total_qty,
            len(group),
            group,
        )

    def add_group(lot_group: list[str] | tuple[str, ...], source: str) -> bool:
        nonlocal count
        key = tuple(sorted(lot_group))
        if not key or len(set(key)) != len(key) or len(key) > max_lots:
            return False
        if not relaxed_lot_cap and len(key) > params["lot_cap"]:
            return False
        if key in seen_groups:
            return False
        seen_groups.add(key)
        total_qty = sum(lot_totals[lot] for lot in key)
        if total_qty < ratio_sum or total_qty > max_die:
            return False
        count += 1
        combo_items = [item for lot in key for item in lot_items[lot]]
        candidate = _make_candidate(
            f"LC{count:08d}",
            combo_items,
            ratio,
            params["unit_cap"],
            params["loss_cap"],
            params["lot_cap"],
            params["max_side_items"],
        )
        if candidate:
            candidates.append(candidate)
            source_counts[source] += 1
        return len(candidates) >= params["candidate_limit"]

    def hit_limit() -> tuple[list[dict[str, Any]], list[str]]:
        warnings.append("大数据候选母批数量达到上限，已停止继续生成。")
        return candidates, warnings

    orders = _large_sort_orders(lot_items, ratio_sum)
    for order in orders:
        for start in range(len(order)):
            lot_group: list[str] = []
            for offset in range(max_lots):
                pos = start + offset
                if pos >= len(order):
                    break
                lot_group.append(order[pos])
                if add_group(lot_group, "连续窗口"):
                    return hit_limit()

    non_contiguous_max_lots = min(max_lots, 6)
    for order in orders:
        span = min(len(order), max(non_contiguous_max_lots + 2, non_contiguous_max_lots * 3), 18)
        if span <= non_contiguous_max_lots:
            continue
        per_start_budget = max(4, min(80, params["candidate_limit"] // max(1, len(order))))
        for start in range(len(order)):
            pool = order[start : start + span]
            groups: list[tuple[str, ...]] = []
            for size in range(2, min(non_contiguous_max_lots, len(pool)) + 1):
                groups.extend(tuple(combo) for combo in combinations(pool, size))
            groups = sorted(groups, key=candidate_sort_key)
            for group in groups[:per_start_budget]:
                if add_group(group, "非连续近邻"):
                    return hit_limit()

    lots_by_remainder = sorted(
        lot_items,
        key=lambda lot: (lot_totals[lot] % ratio_sum, -lot_totals[lot], lot),
    )
    remainder_pool = lots_by_remainder[: min(len(lots_by_remainder), max(10, non_contiguous_max_lots * 4))]
    remainder_budget = max(20, min(params["candidate_limit"] // 3, 3000))
    remainder_groups: list[tuple[str, ...]] = []
    for size in range(2, min(non_contiguous_max_lots, len(remainder_pool)) + 1):
        remainder_groups.extend(tuple(combo) for combo in combinations(remainder_pool, size))
    remainder_groups = sorted(remainder_groups, key=candidate_sort_key)
    for group in remainder_groups[:remainder_budget]:
        if add_group(group, "余数优化"):
            return hit_limit()

    if any(source != "连续窗口" for source in source_counts):
        warnings.append(
            "大数据候选生成已启用非连续 Lot 扩展："
            f"连续窗口 {source_counts.get('连续窗口', 0)} 个，"
            f"非连续近邻 {source_counts.get('非连续近邻', 0)} 个，"
            f"余数优化 {source_counts.get('余数优化', 0)} 个。"
        )
    if not candidates:
        warnings.append("大数据整 Lot 候选生成未找到可行母批。")
    return candidates, warnings


def _chunk_lot_items_for_large_plan(
    lot: str,
    items: list[dict[str, Any]],
    supply: dict[str, Any],
    params: dict[str, Any],
) -> list[dict[str, Any]] | None:
    ratio = supply["config"]["ratio"]
    ratio_sum = int(ratio["A"]) + int(ratio["B"])
    max_die = params["unit_cap"] * ratio_sum + params["loss_cap"]
    if any(int(item["qty"]) > max_die for item in items):
        return None

    def build_chunks(ordered: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]] | None:
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_qty = 0
        for item in ordered:
            qty = int(item["qty"])
            if current and current_qty + qty > max_die:
                chunks.append(current)
                current = [item]
                current_qty = qty
            else:
                current.append(item)
                current_qty += qty
        if current:
            chunks.append(current)

        candidates: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks, start=1):
            candidate = _make_candidate(
                f"{prefix}_{idx:04d}",
                chunk,
                ratio,
                params["unit_cap"],
                params["loss_cap"],
                params["lot_cap"],
                params["max_side_items"],
            )
            if not candidate:
                return None
            candidates.append(candidate)
        return candidates

    orders = [
        sorted(items, key=lambda item: (-int(item["qty"]), item["wafer"], item["grade"], item["id"])),
        sorted(items, key=lambda item: (int(item["qty"]), item["wafer"], item["grade"], item["id"])),
        sorted(items, key=lambda item: (item["grade"], -int(item["qty"]), item["wafer"], item["id"])),
    ]
    for order_idx, ordered in enumerate(orders, start=1):
        plan = build_chunks(ordered, f"LSP_{lot}_{order_idx}")
        if plan:
            covered = {item_id for candidate in plan for item_id in candidate["item_ids"]}
            expected = {item["id"] for item in items}
            if covered == expected:
                return plan
    return None


def _generate_large_allow_reuse_plans(
    supply: dict[str, Any],
    params: dict[str, Any],
    relaxed_lot_cap: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    candidates, candidate_warnings = _generate_large_whole_lot_candidates(supply, params, relaxed_lot_cap)
    warnings.extend(candidate_warnings)
    plans = [_candidate_plan(candidate) for candidate in candidates]

    lot_items = _lot_item_map(supply["items"])
    for lot, items in lot_items.items():
        whole_lot_already_exists = any(plan["lots"] == {lot} for plan in plans)
        if whole_lot_already_exists:
            continue
        split_candidates = _chunk_lot_items_for_large_plan(lot, items, supply, params)
        if not split_candidates:
            continue
        plans.append(
            {
                "id": f"P_SPLIT_{lot}",
                "candidates": split_candidates,
                "lots": {lot},
                "item_ids": {item_id for c in split_candidates for item_id in c["item_ids"]},
                "units": sum(c["units"] for c in split_candidates),
                "loss": sum(c["loss"] for c in split_candidates),
                "lot_overflow": sum(c["lot_overflow"] for c in split_candidates),
                "active_count": len(split_candidates),
            }
        )
    if len(plans) > params["candidate_limit"]:
        plans = sorted(
            plans,
            key=lambda p: (-p["units"], p["loss"], p["lot_overflow"], p["active_count"], p["id"]),
        )[: params["candidate_limit"]]
        warnings.append("大数据 Lot 计划数量达到上限，已截断。")
    return plans, warnings


def _generate_large_no_reuse_plans(
    supply: dict[str, Any],
    params: dict[str, Any],
    relaxed_lot_cap: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidates, warnings = _generate_large_whole_lot_candidates(supply, params, relaxed_lot_cap)
    return [_candidate_plan(candidate) for candidate in candidates], warnings


def _large_plan_sort_key(plan: dict[str, Any]) -> tuple[float, int, int, int, str]:
    units = max(1, int(plan["units"]))
    return (
        plan["loss"] / units,
        plan["lot_overflow"],
        -plan["units"],
        plan["active_count"],
        plan["id"],
    )


def _closest_target_key(units: int, target: int, loss: int, overflow: int, active: int) -> tuple[int, int, int, int, int]:
    return (abs(units - target), loss, overflow, active, max(0, units - target))


def _solution_objective_key(solution: dict[str, Any]) -> tuple[int, int, int, int, int]:
    totals = solution["totals"]
    return _closest_target_key(
        int(totals["total_units"]),
        int(totals["target_units"]),
        int(totals["total_loss"]),
        int(totals["total_lot_overflow"]),
        int(totals["active_batch_count"]),
    )


def _select_large_plans(
    plans: list[dict[str, Any]],
    supply: dict[str, Any],
    params: dict[str, Any],
    target_phase: bool,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    target = int(supply["config"]["target_units"])
    selected: list[dict[str, Any]] = []
    used_items: set[str] = set()
    used_lots: set[str] = set()
    considered = 0

    def selection_key(selection: list[dict[str, Any]]) -> tuple[int, int, int, int, int]:
        return _closest_target_key(
            sum(p["units"] for p in selection),
            target,
            sum(p["loss"] for p in selection),
            sum(p["lot_overflow"] for p in selection),
            len(selection),
        )

    for plan in sorted(plans, key=_large_plan_sort_key):
        considered += 1
        if plan["item_ids"] & used_items:
            continue
        if params["reuse_rule"] == "no_reuse" and plan["lots"] & used_lots:
            continue
        if target_phase and selected and selection_key(selected + [plan]) >= selection_key(selected):
            continue
        selected.append(plan)
        used_items.update(plan["item_ids"])
        used_lots.update(plan["lots"])
        if target_phase and sum(p["units"] for p in selected) >= target:
            break

    if target_phase:
        changed = True
        while changed:
            changed = False
            for plan in sorted(selected, key=lambda p: (-p["units"], -p["loss"], p["id"])):
                trial = [p for p in selected if p is not plan]
                if trial and selection_key(trial) < selection_key(selected):
                    selected.remove(plan)
                    changed = True
                    break
    if not selected:
        return None, {"considered_plans": considered, "selected_plans": 0}

    candidates = [candidate for plan in selected for candidate in plan["candidates"]]
    return candidates, {
        "considered_plans": considered,
        "selected_plans": len(selected),
        "selected_candidate_batches": len(candidates),
    }


def _build_solution(
    supply: dict[str, Any],
    params: dict[str, Any],
    selected: list[dict[str, Any]],
    phase: str,
    backend: str,
    warnings: list[str],
    search_meta: dict[str, Any],
    status: str = "FEASIBLE",
) -> dict[str, Any]:
    item_by_id = {item["id"]: item for item in supply["items"]}
    batches: list[dict[str, Any]] = []
    for idx, candidate in enumerate(selected, start=1):
        assignments = []
        for item_id in candidate["item_ids"]:
            item = item_by_id[item_id]
            side = candidate["assignments"][item_id]
            assignments.append(
                {
                    "item_id": item_id,
                    "lot": item["lot"],
                    "wafer": item["wafer"],
                    "grade": item["grade"],
                    "qty": item["qty"],
                    "side": side,
                }
            )
        batches.append(
            {
                "mother_batch_id": f"MB{idx:03d}",
                "unit_count": candidate["units"],
                "a_qty": candidate["a_qty"],
                "b_qty": candidate["b_qty"],
                "loss": candidate["loss"],
                "distinct_lot_count": len(candidate["lots"]),
                "lot_overflow": candidate["lot_overflow"],
                "lots": candidate["lots"],
                "wafers": sorted({a["wafer"] for a in assignments}),
                "grades": sorted({a["grade"] for a in assignments}),
                "assignments": assignments,
            }
        )
    solution = {
        "status": status,
        "backend": backend,
        "phase": phase,
        "params": params,
        "input_summary": supply["config"],
        "supply_stats": supply["stats"],
        "batches": batches,
        "totals": {},
        "warnings": warnings,
        "search_meta": search_meta,
    }
    total_units = sum(batch["unit_count"] for batch in batches)
    target_units = int(supply["config"]["target_units"])
    solution["totals"] = {
        "total_units": total_units,
        "target_units": target_units,
        "unit_gap": abs(total_units - target_units),
        "under_target_units": max(0, target_units - total_units),
        "over_target_units": max(0, total_units - target_units),
        "total_loss": sum(batch["loss"] for batch in batches),
        "active_batch_count": len(batches),
        "total_lot_overflow": sum(batch["lot_overflow"] for batch in batches),
    }
    solution["checks"] = validate_solution(solution, supply, raise_on_error=False)
    return solution


def validate_solution(
    solution: dict[str, Any],
    supply: dict[str, Any],
    raise_on_error: bool = True,
) -> list[dict[str, Any]]:
    params = solution["params"]
    item_by_id = {item["id"]: item for item in supply["items"]}
    lot_to_items = {
        lot: {item["id"] for item in items}
        for lot, items in _lot_item_map(supply["items"]).items()
    }
    assigned_item_to_batch: dict[str, str] = {}
    lot_batches: dict[str, set[str]] = defaultdict(set)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    for batch in solution.get("batches", []):
        batch_id = batch["mother_batch_id"]
        unit_count = int(batch["unit_count"])
        loss = int(batch["loss"])
        lots = set(batch["lots"])
        add(f"{batch_id}_unit_cap", unit_count <= params["unit_cap"], f"{unit_count} <= {params['unit_cap']}")
        add(f"{batch_id}_loss_cap", loss <= params["loss_cap"], f"{loss} <= {params['loss_cap']}")
        if "relaxed" not in solution["phase"]:
            add(
                f"{batch_id}_lot_cap",
                len(lots) <= params["lot_cap"],
                f"{len(lots)} <= {params['lot_cap']}",
            )
        for assignment in batch["assignments"]:
            item_id = assignment["item_id"]
            if item_id in assigned_item_to_batch:
                add("no_duplicate_item", False, f"{item_id} 重复分配")
            assigned_item_to_batch[item_id] = batch_id
            item = item_by_id[item_id]
            lot_batches[item["lot"]].add(batch_id)
            add(
                f"{item_id}_grade_selected",
                item["grade"] in supply["config"]["selected_grades"],
                f"{item['grade']} in selected grades",
            )
    touched_lots = set(lot_batches)
    for lot, expected_items in lot_to_items.items():
        assigned = {item_id for item_id in expected_items if item_id in assigned_item_to_batch}
        if lot in touched_lots:
            add(f"lot_{lot}_complete", assigned == expected_items, f"{len(assigned)}/{len(expected_items)} items")
        else:
            add(f"lot_{lot}_unused", not assigned, "unused")
    if params["reuse_rule"] == "no_reuse":
        for lot, batches in lot_batches.items():
            add(f"lot_{lot}_no_reuse", len(batches) == 1, f"{len(batches)} batch(es)")
    if "target" in solution["phase"] or "closest" in solution["phase"]:
        total = solution["totals"]["total_units"]
        target = solution["totals"]["target_units"]
        add("target_unit_gap_reported", solution["totals"].get("unit_gap") == abs(total - target), f"|{total} - {target}|")
    errors = [check for check in checks if not check["ok"]]
    if errors and raise_on_error:
        raise DieAllocationError("求解结果校验失败：\n" + "\n".join(e["detail"] for e in errors[:20]))
    return checks


def _solve_with_large_batch(supply: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    phases: list[tuple[str, bool, bool]] = [
        ("closest_strict_lot_cap_large", True, False),
        ("closest_relaxed_lot_cap_large", True, True),
    ]

    all_warnings = [
        "large_batch 是大数据两阶段启发式：先生成高质量候选母批/计划，再选择非冲突计划；适合大表稳定产出可行解，不声明数学全局最优。"
    ]
    best_solution: dict[str, Any] | None = None
    for phase_name, target_phase, relaxed in phases:
        if params["reuse_rule"] == "allow_reuse":
            plans, warnings = _generate_large_allow_reuse_plans(supply, params, relaxed)
        else:
            plans, warnings = _generate_large_no_reuse_plans(supply, params, relaxed)
        all_warnings.extend([f"{phase_name}: {warning}" for warning in warnings])
        selected_candidates, search_meta = _select_large_plans(plans, supply, params, target_phase)
        search_meta["generated_plans"] = len(plans)
        if selected_candidates:
            solution = _build_solution(
                supply,
                params,
                selected_candidates,
                phase_name,
                "large_batch",
                all_warnings,
                search_meta,
                "FEASIBLE",
            )
            if best_solution is None or _solution_objective_key(solution) < _solution_objective_key(best_solution):
                best_solution = solution
    if best_solution is not None:
        return best_solution

    return {
        "status": "INFEASIBLE",
        "backend": "large_batch",
        "phase": "no_solution",
        "params": params,
        "input_summary": supply["config"],
        "supply_stats": supply["stats"],
        "batches": [],
        "totals": {
            "total_units": 0,
            "target_units": supply["config"]["target_units"],
            "unit_gap": supply["config"]["target_units"],
            "under_target_units": supply["config"]["target_units"],
            "over_target_units": 0,
            "total_loss": 0,
            "active_batch_count": 0,
            "total_lot_overflow": 0,
        },
        "warnings": all_warnings + ["large_batch 未找到可行解；建议提高 candidate_limit/max_combo_lots 或使用 cpsat 后端。"],
        "checks": [],
    }


def _best_side_assignment_exact(
    items: list[dict[str, Any]],
    ratio: dict[str, int],
    unit_cap: int,
    loss_cap: int,
    side_sum_limit: int,
) -> dict[str, Any] | None:
    r_a = int(ratio["A"])
    r_b = int(ratio["B"])
    ratio_sum = r_a + r_b
    total_qty = sum(int(item["qty"]) for item in items)
    max_die = unit_cap * ratio_sum + loss_cap
    if total_qty > max_die or total_qty < ratio_sum:
        return None
    if total_qty > side_sum_limit:
        raise DieAllocationError(
            f"精确 A/B 侧分配的 subset-sum 空间过大：{total_qty} > {side_sum_limit}，"
            "请调大 --exact-side-sum-limit 或改用 large_batch。"
        )

    predecessor: dict[int, tuple[int, str]] = {0: (-1, "")}
    for item in sorted(items, key=item_sort_key):
        qty = int(item["qty"])
        current_sums = list(predecessor)
        for prev_sum in current_sums:
            new_sum = prev_sum + qty
            if new_sum > total_qty or new_sum in predecessor:
                continue
            predecessor[new_sum] = (prev_sum, item["id"])

    best: dict[str, Any] | None = None
    best_key: tuple[int, int, int] | None = None
    for a_qty in predecessor:
        if a_qty <= 0 or a_qty >= total_qty:
            continue
        b_qty = total_qty - a_qty
        units = min(a_qty // r_a, b_qty // r_b, unit_cap)
        if units <= 0:
            continue
        loss = total_qty - ratio_sum * units
        if loss < 0 or loss > loss_cap:
            continue
        key = (-units, loss, abs(a_qty * r_b - b_qty * r_a))
        if best_key is None or key < best_key:
            best_key = key
            best = {"units": int(units), "loss": int(loss), "a_qty": int(a_qty), "b_qty": int(b_qty)}

    if best is None:
        return None

    a_item_ids: set[str] = set()
    cursor = best["a_qty"]
    while cursor:
        prev, item_id = predecessor[cursor]
        a_item_ids.add(item_id)
        cursor = prev
    assignments = {item["id"]: ("A" if item["id"] in a_item_ids else "B") for item in items}
    best["assignments"] = assignments
    return best


def _make_candidate_exact(
    candidate_id: str,
    items: list[dict[str, Any]],
    ratio: dict[str, int],
    unit_cap: int,
    loss_cap: int,
    lot_cap: int,
    side_sum_limit: int,
) -> dict[str, Any] | None:
    side = _best_side_assignment_exact(items, ratio, unit_cap, loss_cap, side_sum_limit)
    if not side:
        return None
    lots = sorted({item["lot"] for item in items})
    item_ids = sorted(item["id"] for item in items)
    return {
        "id": candidate_id,
        "lots": lots,
        "item_ids": item_ids,
        "assignments": side["assignments"],
        "units": side["units"],
        "loss": side["loss"],
        "a_qty": side["a_qty"],
        "b_qty": side["b_qty"],
        "lot_overflow": max(0, len(lots) - lot_cap),
    }


def _combo_count(n: int, max_size: int) -> int:
    return sum(comb(n, r) for r in range(1, max_size + 1))


def _generate_exact_no_reuse_candidates(
    supply: dict[str, Any],
    params: dict[str, Any],
    relaxed_lot_cap: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    ratio = supply["config"]["ratio"]
    ratio_sum = int(ratio["A"]) + int(ratio["B"])
    max_die = params["unit_cap"] * ratio_sum + params["loss_cap"]
    lot_items = _lot_item_map(supply["items"])
    lots = sorted(lot_items)
    max_size = len(lots) if relaxed_lot_cap else min(params["lot_cap"], len(lots))
    combination_count = _combo_count(len(lots), max_size)
    if combination_count > params["exact_max_combinations"]:
        raise DieAllocationError(
            f"cpsat 精确搜索需要枚举 {combination_count} 个 Lot 组合，超过上限 "
            f"{params['exact_max_combinations']}。请调大 --exact-max-combinations，或使用 large_batch。"
        )

    candidates: list[dict[str, Any]] = []
    checked = 0
    for size in range(1, max_size + 1):
        for lot_combo in combinations(lots, size):
            checked += 1
            combo_items = [item for lot in lot_combo for item in lot_items[lot]]
            total_qty = sum(int(item["qty"]) for item in combo_items)
            if total_qty > max_die:
                continue
            candidate = _make_candidate_exact(
                f"EXL{checked:08d}",
                combo_items,
                ratio,
                params["unit_cap"],
                params["loss_cap"],
                params["lot_cap"],
                params["exact_side_sum_limit"],
            )
            if candidate:
                candidates.append(candidate)
    return candidates, [f"完整枚举 Lot 组合 {checked} 个，得到可行母批候选 {len(candidates)} 个。"]


def _count_exact_allow_reuse_combinations(
    items: list[dict[str, Any]],
    params: dict[str, Any],
    max_die: int,
    relaxed_lot_cap: bool,
) -> tuple[int, bool]:
    limit = int(params["exact_max_combinations"])
    count = 0

    def visit(start: int, total_qty: int, lots: set[str]) -> bool:
        nonlocal count
        for idx in range(start, len(items)):
            item = items[idx]
            new_total = total_qty + int(item["qty"])
            if new_total > max_die:
                break
            new_lots = lots | {item["lot"]}
            if not relaxed_lot_cap and len(new_lots) > params["lot_cap"]:
                continue
            count += 1
            if count > limit:
                return False
            if not visit(idx + 1, new_total, new_lots):
                return False
        return True

    completed = visit(0, 0, set())
    return count, not completed


def _generate_exact_allow_reuse_candidates(
    supply: dict[str, Any],
    params: dict[str, Any],
    relaxed_lot_cap: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    ratio = supply["config"]["ratio"]
    ratio_sum = int(ratio["A"]) + int(ratio["B"])
    max_die = params["unit_cap"] * ratio_sum + params["loss_cap"]
    items = sorted(supply["items"], key=lambda item: (int(item["qty"]), item["lot"], item["wafer"], item["grade"], item["id"]))
    prechecked_count, exceeded = _count_exact_allow_reuse_combinations(
        items,
        params,
        max_die,
        relaxed_lot_cap,
    )
    if exceeded:
        raise DieAllocationError(
            f"cpsat 精确搜索需要枚举超过 {params['exact_max_combinations']} 个 item 组合。"
            "请调大 --exact-max-combinations，或使用 large_batch。"
        )

    candidates: list[dict[str, Any]] = []
    checked = 0

    def visit(start: int, chosen: list[dict[str, Any]], total_qty: int, lots: set[str]) -> None:
        nonlocal checked
        for idx in range(start, len(items)):
            item = items[idx]
            qty = int(item["qty"])
            new_total = total_qty + qty
            if new_total > max_die:
                break
            new_lots = lots | {item["lot"]}
            if not relaxed_lot_cap and len(new_lots) > params["lot_cap"]:
                continue
            checked += 1
            if checked > params["exact_max_combinations"]:
                raise DieAllocationError(
                    f"cpsat 精确搜索需要枚举超过 {params['exact_max_combinations']} 个 item 组合。"
                    "请调大 --exact-max-combinations，或使用 large_batch。"
                )
            next_chosen = chosen + [item]
            if new_total >= ratio_sum:
                candidate = _make_candidate_exact(
                    f"EXI{checked:08d}",
                    next_chosen,
                    ratio,
                    params["unit_cap"],
                    params["loss_cap"],
                    params["lot_cap"],
                    params["exact_side_sum_limit"],
                )
                if candidate:
                    candidates.append(candidate)
            visit(idx + 1, next_chosen, new_total, new_lots)

    visit(0, [], 0, set())
    return candidates, [f"完整枚举 item 组合 {prechecked_count} 个，得到可行母批候选 {len(candidates)} 个。"]


def _select_candidates_exact(
    candidates: list[dict[str, Any]],
    supply: dict[str, Any],
    params: dict[str, Any],
    target_phase: bool,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    target = int(supply["config"]["target_units"])
    reuse_rule = params["reuse_rule"]
    lot_to_items = {
        lot: {item["id"] for item in items}
        for lot, items in _lot_item_map(supply["items"]).items()
    }
    ordered = sorted(
        candidates,
        key=lambda c: (-c["units"], c["loss"], c["lot_overflow"], len(c["item_ids"]), c["id"]),
    )
    suffix_units = [0] * (len(ordered) + 1)
    for idx in range(len(ordered) - 1, -1, -1):
        suffix_units[idx] = suffix_units[idx + 1] + ordered[idx]["units"]

    best: list[dict[str, Any]] | None = None
    best_key: tuple[int, int, int, int, int] | None = None
    nodes = 0
    hit_limit = False

    def metrics(selection: list[dict[str, Any]]) -> tuple[int, int, int, int, int]:
        units = sum(c["units"] for c in selection)
        loss = sum(c["loss"] for c in selection)
        overflow = sum(c["lot_overflow"] for c in selection)
        active = len(selection)
        if target_phase:
            return _closest_target_key(units, target, loss, overflow, active)
        return (-units, loss, overflow, active, max(0, units - target))

    def feasible_complete(selection: list[dict[str, Any]]) -> bool:
        if reuse_rule != "allow_reuse":
            return True
        assigned: set[str] = set()
        touched_lots: set[str] = set()
        for candidate in selection:
            assigned.update(candidate["item_ids"])
            touched_lots.update(candidate["lots"])
        return all(lot_to_items[lot].issubset(assigned) for lot in touched_lots)

    def try_update(selection: list[dict[str, Any]]) -> None:
        nonlocal best, best_key
        if not selection:
            return
        units = sum(c["units"] for c in selection)
        if not feasible_complete(selection):
            return
        key = metrics(selection)
        if best_key is None or key < best_key:
            best = list(selection)
            best_key = key

    def search(
        idx: int,
        selection: list[dict[str, Any]],
        used_lots: set[str],
        used_items: set[str],
        current_units: int,
    ) -> None:
        nonlocal nodes, hit_limit
        nodes += 1
        if nodes > params["node_limit"]:
            hit_limit = True
            return
        if target_phase and current_units >= target:
            try_update(selection)
            if reuse_rule != "allow_reuse" or feasible_complete(selection):
                return
        if idx >= len(ordered):
            try_update(selection)
            return
        if target_phase and best_key is not None:
            optimistic_distance = 0 if current_units <= target <= current_units + suffix_units[idx] else min(
                abs(current_units - target),
                abs(current_units + suffix_units[idx] - target),
            )
            if optimistic_distance > best_key[0]:
                return
        if not target_phase and best_key is not None and current_units + suffix_units[idx] <= -best_key[0]:
            return

        candidate = ordered[idx]
        candidate_items = set(candidate["item_ids"])
        candidate_lots = set(candidate["lots"])
        conflict = bool(candidate_items & used_items)
        if reuse_rule == "no_reuse":
            conflict = conflict or bool(candidate_lots & used_lots)
        if not conflict:
            selection.append(candidate)
            search(
                idx + 1,
                selection,
                used_lots | candidate_lots,
                used_items | candidate_items,
                current_units + candidate["units"],
            )
            selection.pop()
        search(idx + 1, selection, used_lots, used_items, current_units)

    search(0, [], set(), set(), 0)
    return best, {
        "search_nodes": nodes,
        "hit_node_limit": hit_limit,
        "candidate_count": len(candidates),
        "best_key": list(best_key) if best_key else None,
    }


def _solve_with_cpsat(supply: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    phases: list[tuple[str, bool, bool]] = [
        ("closest_strict_lot_cap_exact", True, False),
        ("closest_relaxed_lot_cap_exact", True, True),
    ]

    warnings = [
        "backend=cpsat 当前使用纯 Python 精确搜索，不依赖 OR-Tools；若完成搜索，结果是当前业务模型的全局最优。"
    ]
    best_solution: dict[str, Any] | None = None
    for phase_name, target_phase, relaxed in phases:
        if params["reuse_rule"] == "no_reuse":
            candidates, generation_warnings = _generate_exact_no_reuse_candidates(supply, params, relaxed)
        else:
            candidates, generation_warnings = _generate_exact_allow_reuse_candidates(supply, params, relaxed)
        warnings.extend([f"{phase_name}: {warning}" for warning in generation_warnings])
        selected, search_meta = _select_candidates_exact(candidates, supply, params, target_phase)
        if search_meta.get("hit_node_limit"):
            return {
                "status": "SEARCH_LIMIT",
                "backend": "cpsat",
                "phase": phase_name,
                "params": params,
                "input_summary": supply["config"],
                "supply_stats": supply["stats"],
                "batches": [],
                "totals": {
                    "total_units": 0,
                    "target_units": supply["config"]["target_units"],
                    "unit_gap": supply["config"]["target_units"],
                    "under_target_units": supply["config"]["target_units"],
                    "over_target_units": 0,
                    "total_loss": 0,
                    "active_batch_count": 0,
                    "total_lot_overflow": 0,
                },
                "warnings": warnings + ["精确选择达到 node_limit，未能证明最优。"],
                "search_meta": search_meta,
                "checks": [],
            }
        if selected:
            solution = _build_solution(
                supply,
                params,
                selected,
                phase_name,
                "cpsat",
                warnings,
                search_meta,
                "OPTIMAL",
            )
            if best_solution is None or _solution_objective_key(solution) < _solution_objective_key(best_solution):
                best_solution = solution
            if solution["totals"]["unit_gap"] == 0 and solution["totals"]["total_lot_overflow"] == 0:
                return solution

    if best_solution is not None:
        return best_solution

    return {
        "status": "INFEASIBLE",
        "backend": "cpsat",
        "phase": "no_solution",
        "params": params,
        "input_summary": supply["config"],
        "supply_stats": supply["stats"],
        "batches": [],
        "totals": {
            "total_units": 0,
            "target_units": supply["config"]["target_units"],
            "unit_gap": supply["config"]["target_units"],
            "under_target_units": supply["config"]["target_units"],
            "over_target_units": 0,
            "total_loss": 0,
            "active_batch_count": 0,
            "total_lot_overflow": 0,
        },
        "warnings": warnings + ["精确搜索完整结束，未找到可行解。"],
        "checks": [],
    }


def _solve_with_ortools_cpsat_unused(supply: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    try:
        from ortools.sat.python import cp_model  # type: ignore
    except ImportError as exc:
        raise DieAllocationError("当前 Python 环境没有 OR-Tools，无法使用 cpsat 后端。") from exc

    items = sorted(supply["items"], key=item_sort_key)
    lots = sorted({item["lot"] for item in items})
    lot_index = {lot: idx for idx, lot in enumerate(lots)}
    r_a = int(supply["config"]["ratio"]["A"])
    r_b = int(supply["config"]["ratio"]["B"])
    target = int(supply["config"]["target_units"])

    def build_model(k_count: int, target_phase: bool, relaxed: bool) -> dict[str, Any]:
        model = cp_model.CpModel()
        x = {}
        for i in range(len(items)):
            for k in range(k_count):
                for side in SIDES:
                    x[(i, k, side)] = model.NewBoolVar(f"x_{i}_{k}_{side}")
        z = {lot: model.NewBoolVar(f"z_{lot_index[lot]}") for lot in lots}
        active = [model.NewBoolVar(f"active_{k}") for k in range(k_count)]
        u = [model.NewIntVar(0, params["unit_cap"], f"u_{k}") for k in range(k_count)]
        loss = [model.NewIntVar(0, params["loss_cap"], f"loss_{k}") for k in range(k_count)]
        overflow = [model.NewIntVar(0, len(lots), f"overflow_{k}") for k in range(k_count)]
        lot_in_batch = {
            (lot, k): model.NewBoolVar(f"lot_{lot_index[lot]}_{k}")
            for lot in lots
            for k in range(k_count)
        }

        for i, item in enumerate(items):
            model.Add(sum(x[(i, k, side)] for k in range(k_count) for side in SIDES) == z[item["lot"]])

        for k in range(k_count):
            model.Add(u[k] <= params["unit_cap"] * active[k])
            model.Add(u[k] >= active[k])
            model.Add(
                sum(x[(i, k, side)] for i in range(len(items)) for side in SIDES)
                <= len(items) * active[k]
            )
            a_qty = sum(int(items[i]["qty"]) * x[(i, k, "A")] for i in range(len(items)))
            b_qty = sum(int(items[i]["qty"]) * x[(i, k, "B")] for i in range(len(items)))
            model.Add(a_qty >= r_a * u[k])
            model.Add(b_qty >= r_b * u[k])
            model.Add(loss[k] == a_qty + b_qty - (r_a + r_b) * u[k])
            for i, item in enumerate(items):
                lot = item["lot"]
                model.Add(lot_in_batch[(lot, k)] >= x[(i, k, "A")])
                model.Add(lot_in_batch[(lot, k)] >= x[(i, k, "B")])
            for lot in lots:
                item_ids = [i for i, item in enumerate(items) if item["lot"] == lot]
                model.Add(
                    lot_in_batch[(lot, k)]
                    <= sum(x[(i, k, side)] for i in item_ids for side in SIDES)
                )
            lot_count_expr = sum(lot_in_batch[(lot, k)] for lot in lots)
            if relaxed:
                model.Add(lot_count_expr <= params["lot_cap"] + overflow[k])
            else:
                model.Add(lot_count_expr <= params["lot_cap"])
                model.Add(overflow[k] == 0)
        if params["reuse_rule"] == "no_reuse":
            for lot in lots:
                model.Add(sum(lot_in_batch[(lot, k)] for k in range(k_count)) <= 1)
        total_units = sum(u)
        if target_phase:
            model.Add(total_units >= target)
        return {
            "model": model,
            "x": x,
            "u": u,
            "loss": loss,
            "overflow": overflow,
            "active": active,
            "lot_in_batch": lot_in_batch,
            "total_units": total_units,
            "total_loss": sum(loss),
            "total_overflow": sum(overflow),
            "active_count": sum(active),
        }

    def solve_stage(model: Any, objective: Any, maximize: bool) -> tuple[int, Any]:
        if maximize:
            model.Maximize(objective)
        else:
            model.Minimize(objective)
        solver = cp_model.CpSolver()
        solver.parameters.random_seed = 0
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = float(params["time_limit_seconds"])
        status = solver.Solve(model)
        return status, solver

    def extract(built: dict[str, Any], solver: Any, phase: str, status: str) -> dict[str, Any]:
        selected = []
        for k in range(len(built["u"])):
            if solver.Value(built["active"][k]) == 0:
                continue
            item_ids = []
            assignments = {}
            a_qty = 0
            b_qty = 0
            lots_in_batch = set()
            for i, item in enumerate(items):
                for side in SIDES:
                    if solver.Value(built["x"][(i, k, side)]):
                        item_ids.append(item["id"])
                        assignments[item["id"]] = side
                        lots_in_batch.add(item["lot"])
                        if side == "A":
                            a_qty += int(item["qty"])
                        else:
                            b_qty += int(item["qty"])
            selected.append(
                {
                    "id": f"C{k + 1:06d}",
                    "lots": sorted(lots_in_batch),
                    "item_ids": sorted(item_ids),
                    "assignments": assignments,
                    "units": solver.Value(built["u"][k]),
                    "loss": solver.Value(built["loss"][k]),
                    "a_qty": a_qty,
                    "b_qty": b_qty,
                    "lot_overflow": solver.Value(built["overflow"][k]),
                }
            )
        return _build_solution(supply, params, selected, phase, "cpsat", [], {}, status)

    status_names = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }

    phases: list[tuple[str, bool, bool]] = []
    if supply["stats"]["quantity_can_meet_target"]:
        phases.extend(
            [
                ("target_strict_lot_cap", True, False),
                ("target_relaxed_lot_cap", True, True),
            ]
        )
    phases.extend(
        [
            ("fallback_strict_lot_cap", False, False),
            ("fallback_relaxed_lot_cap", False, True),
        ]
    )

    best_solution: dict[str, Any] | None = None
    for phase_name, target_phase, relaxed in phases:
        for k_count in _k_range(supply, params, target_phase):
            built = build_model(k_count, target_phase, relaxed)
            model = built["model"]
            if target_phase:
                objectives = [(built["total_units"] - target, False), (built["total_loss"], False)]
            else:
                objectives = [(built["total_units"], True), (built["total_loss"], False)]
            if relaxed:
                objectives.append((built["total_overflow"], False))
            objectives.append((built["active_count"], False))

            solver = None
            status = cp_model.UNKNOWN
            for objective, maximize in objectives:
                status, solver = solve_stage(model, objective, maximize)
                if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    break
                if status == cp_model.FEASIBLE:
                    break
                value = int(solver.Value(objective))
                model.Add(objective == value)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) and solver is not None:
                solution = extract(built, solver, phase_name, status_names[status])
                if best_solution is None:
                    best_solution = solution
                else:
                    old = best_solution["totals"]
                    new = solution["totals"]
                    if target_phase:
                        old_key = (
                            old["over_target_units"],
                            old["total_loss"],
                            old["total_lot_overflow"],
                            old["active_batch_count"],
                        )
                        new_key = (
                            new["over_target_units"],
                            new["total_loss"],
                            new["total_lot_overflow"],
                            new["active_batch_count"],
                        )
                    else:
                        old_key = (
                            -old["total_units"],
                            old["total_loss"],
                            old["total_lot_overflow"],
                            old["active_batch_count"],
                        )
                        new_key = (
                            -new["total_units"],
                            new["total_loss"],
                            new["total_lot_overflow"],
                            new["active_batch_count"],
                        )
                    if new_key < old_key:
                        best_solution = solution
        if best_solution is not None:
            return best_solution

    return {
        "status": "INFEASIBLE",
        "backend": "cpsat",
        "phase": "no_solution",
        "params": params,
        "input_summary": supply["config"],
        "supply_stats": supply["stats"],
        "batches": [],
        "totals": {
            "total_units": 0,
            "target_units": supply["config"]["target_units"],
            "unit_gap": supply["config"]["target_units"],
            "under_target_units": supply["config"]["target_units"],
            "over_target_units": 0,
            "total_loss": 0,
            "active_batch_count": 0,
            "total_lot_overflow": 0,
        },
        "warnings": ["CP-SAT 未找到可行解。"],
        "checks": [],
    }


def solve_allocation(
    supply: dict[str, Any],
    loss_cap: str | int,
    unit_cap: str | int,
    lot_cap: str | int,
    reuse_rule: str,
    backend: str = "auto",
    time_limit: int = 300,
    max_batches: int | None = None,
    candidate_limit: int = 20000,
    max_combo_lots: int = 5,
    max_combo_items: int = 8,
    max_side_items: int = 18,
    node_limit: int = 200000,
    large_item_threshold: int = 800,
    exact_max_combinations: int = 200000,
    exact_side_sum_limit: int = 200000,
) -> dict[str, Any]:
    params = _params(
        loss_cap,
        unit_cap,
        lot_cap,
        reuse_rule,
        time_limit,
        max_batches,
        candidate_limit,
        max_combo_lots,
        max_combo_items,
        max_side_items,
        node_limit,
        large_item_threshold,
        exact_max_combinations,
        exact_side_sum_limit,
    )
    backend = backend.lower()
    if backend not in {"auto", "cpsat", "large_batch"}:
        raise DieAllocationError("--backend 只允许 auto/cpsat/large_batch")
    if backend == "cpsat":
        return _solve_with_cpsat(supply, params)
    if backend == "large_batch":
        return _solve_with_large_batch(supply, params)

    is_large = (
        int(supply["stats"]["atomic_item_count"]) > params["large_item_threshold"]
        or int(supply["stats"]["lot_count"]) > max(50, params["large_item_threshold"] // 4)
    )
    if is_large:
        return _solve_with_large_batch(supply, params)
    try:
        return _solve_with_cpsat(supply, params)
    except DieAllocationError as exc:
        solution = _solve_with_large_batch(supply, params)
        solution.setdefault("warnings", []).insert(0, f"cpsat 精确搜索未使用，auto 已切换到 large_batch：{exc}")
        return solution


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Step 04: 求解 Die 分配方案。")
    parser.add_argument("--supply", required=True, help="Step 03 输出的 supply.json")
    parser.add_argument("--loss-cap", required=True, help="单母批最大 Die 损耗")
    parser.add_argument("--unit-cap", required=True, help="单母批最大 Unit 数，例如 4500 或 4.5k")
    parser.add_argument("--lot-cap", required=True, help="单母批最大不同 Lot 数")
    parser.add_argument("--reuse-rule", required=True, help="allow_reuse/no_reuse 或 允许复用/不允许复用")
    parser.add_argument("--backend", default="auto", choices=["auto", "cpsat", "large_batch"])
    parser.add_argument("--time-limit", type=int, default=300)
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument("--candidate-limit", type=int, default=20000)
    parser.add_argument("--max-combo-lots", type=int, default=5)
    parser.add_argument("--max-combo-items", type=int, default=8)
    parser.add_argument("--max-side-items", type=int, default=18)
    parser.add_argument("--node-limit", type=int, default=200000)
    parser.add_argument("--large-item-threshold", type=int, default=800)
    parser.add_argument("--exact-max-combinations", type=int, default=200000)
    parser.add_argument("--exact-side-sum-limit", type=int, default=200000)
    parser.add_argument("--out", required=True, help="输出 solution.json")
    args = parser.parse_args()
    solution = solve_allocation(
        load_json(args.supply),
        args.loss_cap,
        args.unit_cap,
        args.lot_cap,
        args.reuse_rule,
        args.backend,
        args.time_limit,
        args.max_batches,
        args.candidate_limit,
        args.max_combo_lots,
        args.max_combo_items,
        args.max_side_items,
        args.node_limit,
        args.large_item_threshold,
        args.exact_max_combinations,
        args.exact_side_sum_limit,
    )
    save_json(solution, args.out)
    print(
        "OK: 求解完成 "
        f"status={solution['status']} phase={solution['phase']} units={solution['totals']['total_units']}"
    )


if __name__ == "__main__":
    main()
