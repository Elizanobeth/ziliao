from __future__ import annotations

from collections import defaultdict
from typing import Any

from .common import (
    DieAllocationError,
    load_json,
    normalize_package,
    normalize_text,
    package_score,
    parse_ratio,
    save_json,
)


def _choose_unique(rows: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    ratios = {normalize_text(row.get("层数配比")) for row in rows}
    if len(rows) == 1 or len(ratios) == 1:
        chosen = sorted(rows, key=lambda r: int(r.get("_excel_row", 10**9)))[0].copy()
        chosen["_match_reason"] = reason
        if len(rows) > 1:
            chosen["_duplicate_rule_rows"] = [row.get("_excel_row") for row in rows]
        return chosen
    row_ids = [str(row.get("_excel_row")) for row in rows]
    raise DieAllocationError(f"PACKAGE 匹配到多条不同层数配比规则，规则表行号：{', '.join(row_ids)}")


def select_package_row(
    rows: list[dict[str, Any]],
    target_package: str,
    package_field: str = "PACKAGE",
) -> dict[str, Any]:
    if not rows:
        raise DieAllocationError("没有可用于 PACKAGE 匹配的候选行")

    query_norm = normalize_package(target_package)
    if not query_norm:
        raise DieAllocationError("目标 PACKAGE 不能为空")

    exact = [row for row in rows if normalize_package(row.get(package_field)) == query_norm]
    if exact:
        return _choose_unique(exact, "normalized_exact")

    contains = [
        row
        for row in rows
        if query_norm in normalize_package(row.get(package_field))
        or normalize_package(row.get(package_field)) in query_norm
    ]
    package_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in contains:
        package_groups[normalize_package(row.get(package_field))].append(row)
    if len(package_groups) == 1:
        return _choose_unique(next(iter(package_groups.values())), "normalized_containment")
    if len(package_groups) > 1:
        labels = sorted({normalize_text(row.get(package_field)) for row in contains})
        raise DieAllocationError(f"PACKAGE 包含匹配存在歧义：{', '.join(labels)}")

    scored = []
    for row in rows:
        candidate = normalize_text(row.get(package_field))
        scored.append((package_score(target_package, candidate), candidate, row))
    scored.sort(key=lambda t: (-t[0], t[1], int(t[2].get("_excel_row", 10**9))))
    top_score = scored[0][0]
    second_score = scored[1][0] if len(scored) > 1 else -1
    if top_score >= 85 and top_score - second_score >= 5:
        return _choose_unique([scored[0][2]], f"fuzzy_score_{top_score}")
    raise DieAllocationError(
        f"PACKAGE 模糊匹配失败或存在歧义。最高分 {top_score}，第二名 {second_score}。"
    )


def match_rule(validated: dict[str, Any], target_package: str, supplier: str) -> dict[str, Any]:
    supplier_norm = normalize_text(supplier)
    candidates = [
        row
        for row in validated["rule_rows"]
        if normalize_text(row.get("供应商")) == supplier_norm
    ]
    if not candidates:
        raise DieAllocationError(f"配die 规则表中找不到供应商：{supplier}")
    matched = select_package_row(candidates, target_package)
    r_a, r_b = parse_ratio(matched["层数配比"])
    return {
        "target_package": normalize_text(target_package),
        "supplier": supplier_norm,
        "rule_row": matched,
        "ratio": {"A": r_a, "B": r_b, "text": f"{r_a}:{r_b}"},
        "match": {
            "method": matched.get("_match_reason"),
            "rule_excel_row": matched.get("_excel_row"),
            "rule_package": matched.get("PACKAGE"),
            "rule_supplier": matched.get("供应商"),
            "rule_ratio": matched.get("层数配比"),
            "duplicate_rule_rows": matched.get("_duplicate_rule_rows", []),
        },
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Step 02: 根据 PACKAGE/供应商匹配层数配比规则。")
    parser.add_argument("--validated", required=True, help="Step 01 输出的 validated.json")
    parser.add_argument("--package", required=True, help="目标 PACKAGE")
    parser.add_argument("--supplier", required=True, help="目标供应商")
    parser.add_argument("--out", required=True, help="输出 matched_rule.json")
    args = parser.parse_args()
    data = match_rule(load_json(args.validated), args.package, args.supplier)
    save_json(data, args.out)
    print(
        "OK: 已匹配规则 "
        f"{data['match']['rule_package']} / {data['match']['rule_supplier']} / {data['ratio']['text']}"
    )


if __name__ == "__main__":
    main()
