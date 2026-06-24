from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import DieAllocationError, load_json, save_json


def _load_pandas() -> Any:
    try:
        import pandas as pd  # type: ignore

        return pd
    except ImportError as exc:
        raise DieAllocationError("导出 Excel 报告需要 pandas/xlsxwriter。") from exc


def flatten_solution(solution: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    summary = {
        "status": solution.get("status"),
        "backend": solution.get("backend"),
        "phase": solution.get("phase"),
        "target_package": solution.get("input_summary", {}).get("target_package"),
        "supplier": solution.get("input_summary", {}).get("supplier"),
        "ratio": solution.get("input_summary", {}).get("ratio", {}).get("text"),
        "target_units": solution.get("totals", {}).get("target_units"),
        "total_units": solution.get("totals", {}).get("total_units"),
        "over_target_units": solution.get("totals", {}).get("over_target_units"),
        "total_loss": solution.get("totals", {}).get("total_loss"),
        "active_batch_count": solution.get("totals", {}).get("active_batch_count"),
        "total_lot_overflow": solution.get("totals", {}).get("total_lot_overflow"),
        "reuse_rule": solution.get("params", {}).get("reuse_rule"),
        "loss_cap": solution.get("params", {}).get("loss_cap"),
        "unit_cap": solution.get("params", {}).get("unit_cap"),
        "lot_cap": solution.get("params", {}).get("lot_cap"),
    }
    batches = []
    assignments = []
    for batch in solution.get("batches", []):
        batches.append(
            {
                "mother_batch_id": batch["mother_batch_id"],
                "unit_count": batch["unit_count"],
                "a_qty": batch["a_qty"],
                "b_qty": batch["b_qty"],
                "loss": batch["loss"],
                "distinct_lot_count": batch["distinct_lot_count"],
                "lot_overflow": batch["lot_overflow"],
                "lots": ",".join(batch["lots"]),
                "wafers": ",".join(batch["wafers"]),
                "grades": ",".join(batch["grades"]),
            }
        )
        for row in batch.get("assignments", []):
            out = {"mother_batch_id": batch["mother_batch_id"], **row}
            assignments.append(out)
    checks = solution.get("checks", [])
    warnings = [{"warning": warning} for warning in solution.get("warnings", [])]
    return {
        "summary": [summary],
        "batches": batches,
        "assignments": assignments,
        "checks": checks,
        "warnings": warnings,
    }


def export_report(
    solution: dict[str, Any],
    out_xlsx: str | Path | None = None,
    out_json: str | Path | None = None,
) -> dict[str, Any]:
    flattened = flatten_solution(solution)
    if out_json:
        save_json({"solution": solution, "tables": flattened}, out_json)
    if out_xlsx:
        pd = _load_pandas()
        path = Path(out_xlsx)
        path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
            for sheet_name, rows in flattened.items():
                pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name[:31], index=False)
    return flattened


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Step 05: 导出可审计分配报告。")
    parser.add_argument("--solution", required=True, help="Step 04 输出的 solution.json")
    parser.add_argument("--out-xlsx", required=False, help="输出 Excel 报告路径")
    parser.add_argument("--out-json", required=False, help="输出扁平化 JSON 报告路径")
    args = parser.parse_args()
    export_report(load_json(args.solution), args.out_xlsx, args.out_json)
    outputs = [p for p in [args.out_xlsx, args.out_json] if p]
    print("OK: 已导出报告 " + ", ".join(outputs))


if __name__ == "__main__":
    main()
