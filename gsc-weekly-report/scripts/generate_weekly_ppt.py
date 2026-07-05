#!/usr/bin/env python3
"""Fill a GSC weekly report PPTX template from organized JSON."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.oxml.ns import qn
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: python-pptx. Install python-pptx or use the Codex bundled Python runtime.") from exc


PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
BULLET_TAGS = {
    qn("a:buAutoNum"),
    qn("a:buBlip"),
    qn("a:buChar"),
    qn("a:buFont"),
    qn("a:buNone"),
}

CATEGORIES = {
    "main_progress": {
        "placeholder": "{{MAIN_PROGRESS}}",
        "empty_text": "暂无主要进展。",
    },
    "issues": {
        "placeholder": "{{ISSUES}}",
        "empty_text": "暂无明显问题。",
    },
    "help_needed": {
        "placeholder": "{{HELP_NEEDED}}",
        "empty_text": "暂无需额外协调事项。",
    },
    "next_plan": {
        "placeholder": "{{NEXT_PLAN}}",
        "empty_text": "暂无明确下周计划。",
    },
}


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
        return [text for item in value if (text := item_to_text(item))]
    text = str(value).strip()
    return [text] if text else []


def clean_item_prefix(text: str) -> str:
    text = re.sub(r"^[•·\-]\s*", "", text.strip())
    return re.sub(r"^\d+[\.、）\)]\s*", "", text).strip()


def bullet_text(items: list[str], empty_text: str) -> str:
    source_items = items or [empty_text]
    return "\n".join(clean_item_prefix(item) for item in source_items)


def build_replacements(summary: dict[str, Any]) -> dict[str, str]:
    week = str(summary.get("week") or "").strip()
    title = str(summary.get("title") or f"{week} 周报").strip()
    generated_date = str(summary.get("generated_date") or dt.date.today().isoformat())
    scope = str(summary.get("scope") or "全部section").strip()
    replacements = {
        "{{REPORT_WEEK}}": week,
        "{{REPORT_TITLE}}": title,
        "{{SCOPE}}": scope,
        "{{GENERATED_DATE}}": generated_date,
    }
    for category, config in CATEGORIES.items():
        replacements[config["placeholder"]] = bullet_text(
            ensure_list(summary.get(category)),
            config["empty_text"],
        )
    return replacements


def replace_text(text: str, replacements: dict[str, str]) -> str:
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


def font_snapshot(text_frame: Any) -> dict[str, Any]:
    for paragraph in text_frame.paragraphs:
        for run in paragraph.runs:
            font = run.font
            color = None
            try:
                color = font.color.rgb
            except AttributeError:
                color = None
            return {
                "name": font.name,
                "size": font.size,
                "bold": font.bold,
                "italic": font.italic,
                "underline": font.underline,
                "color": color,
            }
    return {}


def apply_font(run: Any, snapshot: dict[str, Any]) -> None:
    font = run.font
    if snapshot.get("name"):
        font.name = snapshot["name"]
    if snapshot.get("size"):
        font.size = snapshot["size"]
    if snapshot.get("bold") is not None:
        font.bold = snapshot["bold"]
    if snapshot.get("italic") is not None:
        font.italic = snapshot["italic"]
    if snapshot.get("underline") is not None:
        font.underline = snapshot["underline"]
    if snapshot.get("color") is not None:
        font.color.rgb = snapshot["color"]


def paragraph_properties_snapshot(text_frame: Any) -> Any | None:
    for paragraph in text_frame.paragraphs:
        paragraph_properties = paragraph._p.pPr  # noqa: SLF001 - python-pptx has no public pPr API.
        if paragraph_properties is not None:
            return copy.deepcopy(paragraph_properties)
    return None


def without_bullet_properties(snapshot: Any | None) -> Any | None:
    if snapshot is None:
        return None
    copied = copy.deepcopy(snapshot)
    for child in list(copied):
        if child.tag in BULLET_TAGS:
            copied.remove(child)
    return copied


def apply_paragraph_properties(paragraph: Any, snapshot: Any | None) -> None:
    if snapshot is None:
        return
    current = paragraph._p.pPr  # noqa: SLF001
    if current is not None:
        paragraph._p.remove(current)  # noqa: SLF001
    paragraph._p.insert(0, copy.deepcopy(snapshot))  # noqa: SLF001


def set_text_preserving_style(text_frame: Any, text: str) -> None:
    style = font_snapshot(text_frame)
    paragraph_style = paragraph_properties_snapshot(text_frame)
    blank_paragraph_style = without_bullet_properties(paragraph_style)
    alignment = text_frame.paragraphs[0].alignment if text_frame.paragraphs else None
    text_frame.clear()
    lines = text.splitlines() or [""]
    for index, line in enumerate(lines):
        paragraph = text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
        apply_paragraph_properties(paragraph, paragraph_style if line.strip() else blank_paragraph_style)
        paragraph.alignment = alignment
        run = paragraph.add_run()
        run.text = line
        apply_font(run, style)


def iter_shapes(shapes: Any):
    for shape in shapes:
        yield shape
        if getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.GROUP:
            yield from iter_shapes(shape.shapes)


def replace_in_text_frame(shape: Any, replacements: dict[str, str]) -> int:
    if not getattr(shape, "has_text_frame", False):
        return 0
    original = shape.text
    updated = replace_text(original, replacements)
    if updated == original:
        return 0
    set_text_preserving_style(shape.text_frame, updated)
    return 1


def replace_in_table(shape: Any, replacements: dict[str, str]) -> int:
    if not getattr(shape, "has_table", False):
        return 0
    changed = 0
    for row in shape.table.rows:
        for cell in row.cells:
            original = cell.text
            updated = replace_text(original, replacements)
            if updated != original:
                cell.text = updated
                changed += 1
    return changed


def unresolved_placeholders(prs: Presentation) -> list[str]:
    unresolved: set[str] = set()
    for slide in prs.slides:
        for shape in iter_shapes(slide.shapes):
            texts: list[str] = []
            if getattr(shape, "has_text_frame", False):
                texts.append(shape.text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    for cell in row.cells:
                        texts.append(cell.text)
            for text in texts:
                unresolved.update(PLACEHOLDER_RE.findall(text))
    return sorted(unresolved)


def fill_template(template_path: Path, summary_path: Path, output_path: Path) -> dict[str, Any]:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    replacements = build_replacements(summary)
    prs = Presentation(str(template_path))

    changed = 0
    for slide in prs.slides:
        for shape in iter_shapes(slide.shapes):
            changed += replace_in_text_frame(shape, replacements)
            changed += replace_in_table(shape, replacements)

    unresolved = unresolved_placeholders(prs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    return {
        "output": str(output_path),
        "slide_count": len(prs.slides),
        "changed_shapes_or_cells": changed,
        "unresolved_placeholders": unresolved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a GSC weekly report PPTX from organized JSON.")
    parser.add_argument("--summary", required=True, help="Path to weekly_report_summary.json.")
    parser.add_argument("--template", required=True, help="Path to PPTX template with placeholders.")
    parser.add_argument("--output", required=True, help="Output PPTX path.")
    args = parser.parse_args()

    summary_path = Path(args.summary).expanduser().resolve()
    template_path = Path(args.template).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not summary_path.exists():
        raise SystemExit(f"Summary JSON does not exist: {summary_path}")
    if not template_path.exists():
        raise SystemExit(f"Template PPTX does not exist: {template_path}")

    result = fill_template(template_path, summary_path, output_path)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not result["unresolved_placeholders"] else 2


if __name__ == "__main__":
    sys.exit(main())
