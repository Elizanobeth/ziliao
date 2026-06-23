---
name: die-lot-planner-fast
description: Plan semiconductor Die mother lots quickly from Excel or CSV data. Use when a user needs to group records with PACKAGE, 供应商, Fab LotID, Bin Grade, Bin Quanity, and T7 Code into mother lots by PACKAGE, supplier, and a two-part layer ratio from a sheet named "配 Die 规则表"; enforce per-mother-lot waste limits, optional Fab LotID reuse, produce each mother lot's Lot list, and provide best-effort alternatives plus a summary when targets are not met.
---

# Die Lot Planner Fast

## Overview

Use this skill to create mother-lot allocation outputs from raw bin inventory and a simple rule table. Prefer the bundled Python script over rewriting the algorithm.

## Required Input Collection Before Running

When this skill is triggered, do not run the script immediately. First check whether the user has provided all required inputs. Ask follow-up questions for missing required items, and only run the script after the required choices are explicit.

Collect or confirm these inputs:

| Input | Required | Notes |
|---|---:|---|
| Input file path | Yes | Excel/CSV/TSV path; Excel is recommended. |
| Raw data sheet name | Yes | For example `原始数据`. Do not guess unless the user explicitly allows auto-detection. |
| Rule sheet name | Yes | Usually `配 Die 规则表`, but still confirm. |
| Total Unit demand | Yes | Maps to `--total-units`; this is the cumulative Unit demand for the full run, not the target for each mother lot. |
| Maximum waste die per mother lot | Yes | Maps to `--max-waste`; do not silently use the default. |
| Whether Fab LotID reuse is allowed | Yes | User must choose allowed or not allowed. |
| Bin Grade eligibility | Yes | For example all grades, or only `Bin Grade <= 1/2`. |
| Output file path | Yes | Excel/JSON/CSV; recommend Excel, but confirm the path. |
| Output detail requirement | Yes | Confirm whether to output each mother lot's Lot list; normally yes. |
| Failure handling | Yes | Confirm best-effort alternative, optimization suggestions, and summary; this skill recommends all three. |
| Raw column names or mapping | Conditional | If fields are not exactly `PACKAGE`, `供应商`, `Fab LotID`, `Bin Grade`, `Bin Quanity`, `T7 Code`, ask for mappings. |
| Rule column names or mapping | Conditional | If fields are not exactly `PACKAGE`, `供应商`, `层数配比`, ask for mappings. |
| `beam_width` | Optional | Ask only when the user cares about speed/search quality; otherwise use the script default. |

Use this concise prompt when the user has not supplied enough information:

```text
请补齐/确认以下信息后我再运行 die-lot-planner-fast：

1. 输入文件路径：
2. 原始数据 sheet 名称：
3. 配 Die 规则表 sheet 名称：
4. 总 Unit 需求：
5. 每个母批最大浪费 Die 数：
6. Fab LotID 是否允许复用：允许 / 不允许
7. Bin Grade 使用范围：全部 / 只用 <= ?
8. 输出文件路径：
9. 是否输出每个母批 Lot 清单：是 / 否
10. 若不满足目标，是否输出最佳替代方案 + 优化建议 + summary：是 / 否
11. 字段名是否就是 PACKAGE、供应商、Fab LotID、Bin Grade、Bin Quanity、T7 Code：是 / 否；如否请给字段映射
12. 规则表字段名是否就是 PACKAGE、供应商、层数配比：是 / 否；如否请给字段映射
```

If the user has already supplied some items, ask only for the missing ones.

Core rules:
- The rule sheet is named `配 Die 规则表` by default and has exactly these logical columns: `PACKAGE`, `供应商`, `层数配比`.
- `层数配比` is a two-number ratio such as `2:6`; each completed Unit consumes 2 units from bucket 1 and 6 units from bucket 2.
- The raw data must contain `PACKAGE`, `供应商`, `Fab LotID`, `Bin Grade`, `Bin Quanity`, and `T7 Code`.
- `Bin Grade` is the die quality grade; `1` is the best. By default all grades are eligible and better grades are considered first. Use `--max-bin-grade` when only grades up to a threshold are allowed.
- `Bin Quanity` is the bin quantity used as the die count.
- `T7 Code` is the wafer's unique identifier. The algorithm never splits one `T7 Code` row across buckets or mother lots.
- The raw data does not need an A/B process field. Assign each `T7 Code` row as a whole to bucket 1 or bucket 2 during optimization.
- The user provides total Unit demand as `total_units`, not a per-mother-lot target.
- The script creates mother lots one by one, subtracting each mother lot's Unit from the remaining demand. It stops once cumulative Unit reaches `total_units` or no valid mother lot can be formed.
- For each `PACKAGE + 供应商` group, a necessary feasibility condition for the total demand is `total_units * (ratio_1 + ratio_2) <= sum(Bin Quanity)`. If this fails, the total Unit count is impossible from available quantity alone.
- Each mother lot may waste at most `max_waste` die, default `30`.
- Optimize in this order: maximize completed Unit count, then minimize waste.
- If no formal mother lot can be found, output the best-effort alternative with the largest Unit count that still satisfies `WasteDie <= max_waste`. Do not prefer zero waste over a much smaller Unit count.
- Always include a user-readable summary of what was matched or why it was not matched.
- If Fab LotID reuse is disabled, a `Fab LotID` may appear in only one mother lot. If reuse is enabled, different `T7 Code` rows with the same `Fab LotID` may appear in different mother lots.

## Quick Start

Run:

```bash
python scripts/plan_die_lots.py \
  --input input.xlsx \
  --output planned_mother_lots.xlsx \
  --total-units 1000
```

Common options:
- `--data-sheet SHEET`: raw inventory sheet. If omitted, the first sheet that is not `配 Die 规则表` is used.
- `--rules-sheet SHEET`: defaults to `配 Die 规则表`.
- `--total-units N`: cumulative Unit demand for this planning run.
- `--target-units N`: deprecated alias for `--total-units`.
- `--max-waste N`: defaults to `30`.
- `--allow-lot-reuse`: allow the same `Fab LotID` to appear in different mother lots.
- `--max-bin-grade N`: only use rows where `Bin Grade <= N`.
- `--bin-quantity-col COL`, `--bin-grade-col COL`, `--t7-col COL`, `--lot-col COL`: use these only when the raw sheet has non-standard column names.
- `--beam-width N`: breadth for the deep fallback search. The script first uses a faster subset-sum search; beam search runs only when the fast path cannot find a candidate.

## Input Contract

Read `references/input_contract.md` when adapting the script to a user's workbook, mapping unusual column names, or explaining required input fields.

Minimum raw data columns:
- `PACKAGE`
- `供应商`
- `Fab LotID`
- `Bin Grade`
- `Bin Quanity`
- `T7 Code`

## Outputs

The script writes:
- `summary`: Chinese summary of the run conditions, formal match result, best-effort alternative when applicable, and where to inspect details.
- `mother_lots`: one row per generated mother lot, including PACKAGE, supplier, ratio, Unit count, selected die, required die, waste, bucket totals, and lot count.
- `lot_assignments`: the detailed Lot and `T7 Code` list for every mother lot.
- `best_effort_matches`: when no formal match is found for a group, the largest Unit alternative found within the user's waste limit.
- `best_effort_assignments`: detailed Lot and `T7 Code` list for each best-effort alternative.
- `unused_inventory`: rows not allocated to any mother lot, including rows blocked by no-reuse rules.
- `diagnostics`: input choices, warnings, and search parameters.
- `optimization_suggestions`: practical suggestions when the data cannot satisfy the target, such as relaxing max waste, lowering target Units, allowing Fab LotID reuse, adding missing rules, or adding inventory to the limiting bucket.

Always give the user the generated workbook and briefly mention the `summary` result. If `mother_lots` is empty but `best_effort_matches` is present, explain that the formal target was not found and that the alternative maximizes Unit while keeping `WasteDie <= max_waste`.

## Search Strategy

The script uses a layered search to keep normal runs fast:

1. Fast subset-sum search probes the current remaining Unit demand, then 90%, 80%, ..., 10% of that remaining demand. It separately builds bucket-1 and bucket-2 candidates and accepts only combinations with `WasteDie <= max_waste`.
2. Deep beam search is used only as a fallback when the fast path cannot find a valid candidate.
3. Best-effort alternatives also use the fast path first, so a zero-waste low-Unit result is not preferred over a larger Unit result that still respects the waste limit.

## Editing Guidance

Keep business rules in `SKILL.md` and deterministic behavior in `scripts/plan_die_lots.py`. If requirements change, update both the rule summary and the script defaults/options in the same edit.
