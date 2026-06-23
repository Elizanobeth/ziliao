---
name: die-lot-planner
description: Plan semiconductor Die mother lots from Excel or CSV data. Use when a user needs to group records with PACKAGE, 供应商, Fab LotID, Bin Grade, Bin Quanity, and T7 Code into mother lots by PACKAGE, supplier, and a two-part layer ratio from a sheet named "配 Die 规则表"; enforce per-mother-lot waste limits, optional Fab LotID reuse, and produce each mother lot's Lot list.
---

# Die Lot Planner

## Overview

Use this skill to create mother-lot allocation outputs from raw bin inventory and a simple rule table. Prefer the bundled Python script over rewriting the algorithm.

Core rules:
- The rule sheet is named `配 Die 规则表` by default and has exactly these logical columns: `PACKAGE`, `供应商`, `层数配比`.
- `层数配比` is a two-number ratio such as `2:6`; each completed Unit consumes 2 units from bucket 1 and 6 units from bucket 2.
- The raw data must contain `PACKAGE`, `供应商`, `Fab LotID`, `Bin Grade`, `Bin Quanity`, and `T7 Code`.
- `Bin Grade` is the die quality grade; `1` is the best. By default all grades are eligible and better grades are considered first. Use `--max-bin-grade` when only grades up to a threshold are allowed.
- `Bin Quanity` is the bin quantity used as the die count.
- `T7 Code` is the wafer's unique identifier. The algorithm never splits one `T7 Code` row across buckets or mother lots.
- The raw data does not need an A/B process field. Assign each `T7 Code` row as a whole to bucket 1 or bucket 2 during optimization.
- Each mother lot must have `Unit <= target_units`.
- For each `PACKAGE + 供应商` group, a necessary feasibility condition for the target is `target_units * (ratio_1 + ratio_2) <= sum(Bin Quanity)`. If this fails, the target Unit count is impossible from total available quantity alone.
- Each mother lot may waste at most `max_waste` die, default `30`.
- Optimize in this order: maximize completed Unit count, then minimize waste.
- If Fab LotID reuse is disabled, a `Fab LotID` may appear in only one mother lot. If reuse is enabled, different `T7 Code` rows with the same `Fab LotID` may appear in different mother lots.

## Quick Start

Run:

```bash
python scripts/plan_die_lots.py \
  --input input.xlsx \
  --output planned_mother_lots.xlsx \
  --target-units 1000
```

Common options:
- `--data-sheet SHEET`: raw inventory sheet. If omitted, the first sheet that is not `配 Die 规则表` is used.
- `--rules-sheet SHEET`: defaults to `配 Die 规则表`.
- `--target-units N`: required target Unit count per mother lot.
- `--max-waste N`: defaults to `30`.
- `--allow-lot-reuse`: allow the same `Fab LotID` to appear in different mother lots.
- `--max-bin-grade N`: only use rows where `Bin Grade <= N`.
- `--bin-quantity-col COL`, `--bin-grade-col COL`, `--t7-col COL`, `--lot-col COL`: use these only when the raw sheet has non-standard column names.
- `--beam-width N`: increase when a difficult dataset needs better search quality; default is stable for normal use.

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
- `mother_lots`: one row per generated mother lot, including PACKAGE, supplier, ratio, Unit count, selected die, required die, waste, bucket totals, and lot count.
- `lot_assignments`: the detailed Lot and `T7 Code` list for every mother lot.
- `unused_inventory`: rows not allocated to any mother lot, including rows blocked by no-reuse rules.
- `diagnostics`: input choices, warnings, and search parameters.
- `optimization_suggestions`: practical suggestions when the data cannot satisfy the target, such as relaxing max waste, lowering target Units, allowing Fab LotID reuse, adding missing rules, or adding inventory to the limiting bucket.

Always give the user the generated workbook and briefly mention any diagnostics that indicate unmet target, missing columns, or unallocated inventory.

## Editing Guidance

Keep business rules in `SKILL.md` and deterministic behavior in `scripts/plan_die_lots.py`. If requirements change, update both the rule summary and the script defaults/options in the same edit.
