# Die Lot Planner Input Contract

## Rule Sheet

Default sheet name: `配 Die 规则表`

Required logical columns:

| Column | Meaning | Example |
|---|---|---|
| `PACKAGE` | Product/package key | `PKG001` |
| `供应商` | Supplier/vendor key | `VendorA` |
| `层数配比` | Two-part layer ratio | `2:6` |

The ratio must contain exactly two positive integers separated by `:` or a similar separator. A ratio of `2:6` means one Unit consumes 2 die from bucket 1 and 6 die from bucket 2.

## Raw Inventory Sheet

The raw sheet may have any name. If `--data-sheet` is not provided, the script uses the first sheet other than the rule sheet.

Required logical columns:

| Logical field | Preferred name | Common aliases |
|---|---|---|
| Product type | `PACKAGE` | `Package`, `产品`, `封装` |
| Supplier name | `供应商` | `Vendor`, `Supplier`, `厂商` |
| Batch ID | `Fab LotID` | `FabLotID`, `LotID`, `Fab Lot`, `批次` |
| Die quality grade | `Bin Grade` | `BinGrade`, `Grade`, `等级` |
| Bin quantity | `Bin Quanity` | `Bin Quantity`, `BinQuanity`, `BinQuantity`, `Qty`, `数量`, `颗数` |
| Wafer unique ID | `T7 Code` | `T7Code`, `T7` |

The script treats each raw row as one indivisible `T7 Code + Bin Grade` inventory item. It never splits one row across buckets or mother lots.

`Bin Grade` is a quality rank where `1` is the best. By default all grades are eligible and lower grade numbers are considered first. Use `--max-bin-grade` when the user only wants grades up to a threshold, such as only grade 1.

## Optimization Semantics

For each `PACKAGE + 供应商` group:

1. Read the two-number ratio from `配 Die 规则表`.
2. Check the necessary total-quantity condition: `target_units * (ratio_1 + ratio_2) <= sum(Bin Quanity)` for the group's currently eligible rows.
3. Assign each available `T7 Code + Bin Grade` row wholly to bucket 1 or bucket 2.
4. Completed Units equal `min(floor(bucket1_die / ratio_1), floor(bucket2_die / ratio_2))`.
5. Required die equal `completed_units * (ratio_1 + ratio_2)`.
6. Waste equals `selected_die - required_die`.
7. Valid mother lots must satisfy:
   - `completed_units <= target_units`
   - `waste <= max_waste`
8. Select the next mother lot by maximizing Units first, then minimizing waste.
9. Repeat until no valid mother lot can be formed.

When `--allow-lot-reuse` is absent, after a mother lot uses any row from a Fab LotID, all remaining rows with that Fab LotID become unavailable for later mother lots. When `--allow-lot-reuse` is present, only rows already selected are removed; other rows with the same Fab LotID may be selected later.

## When Targets Are Not Met

If no valid mother lot can be formed, or if a group's best result is below the requested Unit target, the output should include optimization suggestions. Suggestions should be operational, not generic. Common examples:

- Lower `--target-units` when available inventory cannot reach the target without exceeding the waste cap.
- Lower `--target-units` or add inventory when `sum(Bin Quanity)` is less than `target_units * (ratio_1 + ratio_2)`.
- Increase `--max-waste` when near-target combinations fail only because waste is above the cap.
- Add inventory for the limiting bucket implied by the ratio.
- Enable `--allow-lot-reuse` when many rows are blocked only because the same Fab LotID was already used.
- Add or fix `配 Die 规则表` rows when raw `PACKAGE + 供应商` combinations have no matching rule.
