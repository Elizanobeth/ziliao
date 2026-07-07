from __future__ import annotations

from pathlib import Path

from semidie.common import DieAllocationError, save_json
from semidie.matcher import match_rule
from semidie.report import export_report
from semidie.solver import solve_allocation
from semidie.supply import build_supply
from semidie.workbook import validate_workbook


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="端到端运行半导体晶圆 Die 母批分配。")
    parser.add_argument("--workbook", required=True, help="输入 Excel 工作簿路径或可下载 URL")
    parser.add_argument("--package", required=True, help="目标 PACKAGE")
    parser.add_argument("--supplier", required=True, help="目标供应商")
    parser.add_argument("--target-units", required=True, help="目标 Unit 数，例如 40000 或 40k")
    parser.add_argument("--grades", required=True, help="用户选择 Bin Grade，例如 1,2,3")
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
    parser.add_argument("--out-dir", required=True, help="输出目录")
    args = parser.parse_args()

    try:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        validated = validate_workbook(args.workbook)
        save_json(validated, out_dir / "01_validated.json")

        matched = match_rule(validated, args.package, args.supplier)
        save_json(matched, out_dir / "02_matched_rule.json")

        supply = build_supply(validated, matched, args.grades, args.target_units)
        save_json(supply, out_dir / "03_supply.json")

        solution = solve_allocation(
            supply,
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
        save_json(solution, out_dir / "04_solution.json")
        export_report(
            solution,
            out_xlsx=out_dir / "05_allocation_report.xlsx",
            out_json=out_dir / "05_allocation_report.json",
        )
    except DieAllocationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    print(
        "OK: 端到端分配完成 "
        f"status={solution['status']} phase={solution['phase']} total_units={solution['totals']['total_units']}"
    )


if __name__ == "__main__":
    main()
