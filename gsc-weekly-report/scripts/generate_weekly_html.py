#!/usr/bin/env python3
"""Generate a self-contained GSC weekly report HTML file from organized JSON."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path
from typing import Any


CATEGORIES = [
    ("main_progress", "主要进展", "progress"),
    ("issues", "存在问题", "issues"),
    ("help_needed", "需要帮助", "help"),
    ("next_plan", "下周计划", "plan"),
]

REQUIRED_TEMPLATE_PLACEHOLDERS = [
    "{{REPORT_TITLE}}",
    "{{REPORT_WEEK}}",
    "{{SCOPE}}",
    "{{GENERATED_DATE}}",
    "{{REPORT_SECTIONS}}",
]


def item_to_text(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, dict):
        for key in ("text", "content", "summary", "item"):
            if item.get(key):
                return str(item[key]).strip()
        return "；".join(f"{key}：{value}" for key, value in item.items() if value)
    return str(item).strip()


def ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item_to_text(item) for item in value if item_to_text(item)]
    text = str(value).strip()
    return [text] if text else []


def clean_item_prefix(text: str) -> str:
    return re.sub(r"^[•·\-\d\.、）\)\s]+", "", text).strip()


def render_items(items: list[str]) -> str:
    rendered = []
    for index, item in enumerate(items, start=1):
        clean = html.escape(clean_item_prefix(item))
        rendered.append(
            f"""
            <li class="report-item">
              <span class="item-index">{index}</span>
              <span class="item-text">{clean}</span>
            </li>
            """.strip()
        )
    return "\n".join(rendered)


def render_sections(summary: dict[str, Any]) -> str:
    sections = []
    for key, title, tone in CATEGORIES:
        items = ensure_list(summary.get(key))
        if not items:
            continue
        sections.append(
            f"""
            <section class="report-section tone-{tone}">
              <h2>{html.escape(title)}</h2>
              <ol class="report-list">
                {render_items(items)}
              </ol>
            </section>
            """.strip()
        )
    if sections:
        return "\n".join(sections)
    return """
    <section class="report-section tone-empty">
      <h2>周报内容</h2>
      <ol class="report-list">
        <li class="report-item"><span class="item-index">1</span><span class="item-text">暂无可展示内容。</span></li>
      </ol>
    </section>
    """.strip()


def default_template_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "weekly_template.html"


def fill_template(template_text: str, replacements: dict[str, str]) -> str:
    rendered = template_text
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def validate_template(template_text: str, template_path: Path) -> None:
    missing = [
        placeholder
        for placeholder in REQUIRED_TEMPLATE_PLACEHOLDERS
        if placeholder not in template_text
    ]
    if missing:
        raise SystemExit(
            "HTML template is missing required placeholder(s): "
            + ", ".join(missing)
            + f"\nTemplate: {template_path}"
        )


def build_html(summary: dict[str, Any], template_text: str) -> str:
    week = str(summary.get("week") or "").strip()
    title = str(summary.get("title") or f"{week} 周报").strip()
    scope = str(summary.get("scope") or "全部section").strip()
    generated_date = str(summary.get("generated_date") or dt.date.today().isoformat())
    sections_html = render_sections(summary)

    escaped_title = html.escape(title)
    escaped_scope = html.escape(scope)
    escaped_week = html.escape(week)
    escaped_date = html.escape(generated_date)

    return fill_template(
        template_text,
        {
            "{{REPORT_TITLE}}": escaped_title,
            "{{REPORT_WEEK}}": escaped_week,
            "{{SCOPE}}": escaped_scope,
            "{{GENERATED_DATE}}": escaped_date,
            "{{REPORT_SECTIONS}}": sections_html,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a self-contained GSC weekly report HTML file.")
    parser.add_argument("--summary", required=True, help="Path to weekly_report_summary.json.")
    parser.add_argument(
        "--template",
        help="Optional HTML template path. Defaults to assets/weekly_template.html.",
    )
    parser.add_argument("--output", required=True, help="Output HTML path.")
    args = parser.parse_args()

    summary_path = Path(args.summary).expanduser().resolve()
    template_path = Path(args.template).expanduser().resolve() if args.template else default_template_path()
    output_path = Path(args.output).expanduser().resolve()

    if not summary_path.exists():
        raise SystemExit(f"Summary JSON does not exist: {summary_path}")
    if not template_path.exists():
        raise SystemExit(f"HTML template does not exist: {template_path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    template_text = template_path.read_text(encoding="utf-8")
    validate_template(template_text, template_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_html(summary, template_text), encoding="utf-8")

    print(json.dumps({"output": str(output_path), "template": str(template_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
