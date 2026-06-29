#!/usr/bin/env python3
"""从真实 PPTX 中提取可复用模板结构，并可生成 token 化模板副本。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

P_SP = f"{{{NS['p']}}}sp"
P_CNVPR = f"{{{NS['p']}}}cNvPr"
P_PH = f"{{{NS['p']}}}ph"
A_P = f"{{{NS['a']}}}p"
A_T = f"{{{NS['a']}}}t"
A_OFF = f"{{{NS['a']}}}off"
A_EXT = f"{{{NS['a']}}}ext"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
EMU_PER_INCH = 914400


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从真实 PPTX 提取模板结构、文本清单和可填充 token。"
    )
    parser.add_argument("source_pptx", type=Path, help="源 PPTX")
    parser.add_argument("--out-dir", type=Path, required=True, help="输出目录")
    parser.add_argument(
        "--make-template-pptx",
        action="store_true",
        help="生成一份把可替换文本替换成 token 的模板 PPTX",
    )
    parser.add_argument(
        "--template-name",
        default="extracted_template.pptx",
        help="token 化模板 PPTX 文件名",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=2,
        help="少于该字符数的文本不生成 token，默认 2",
    )
    parser.add_argument(
        "--skip-regex",
        action="append",
        default=[],
        help="跳过匹配该正则的文本，可重复传入",
    )
    parser.add_argument(
        "--include-numeric",
        action="store_true",
        help="也为纯数字或页码类短文本生成 token",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="JSON 使用缩进格式输出",
    )
    return parser.parse_args()


def slide_sort_key(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def slide_parts(zf: zipfile.ZipFile) -> list[str]:
    return sorted(
        [
            name
            for name in zf.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        ],
        key=slide_sort_key,
    )


def emu_to_inches(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return round(int(value) / EMU_PER_INCH, 3)
    except ValueError:
        return None


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(A_T))


def shape_meta(shape: ET.Element) -> dict[str, Any]:
    c_nv_pr = shape.find(f".//{P_CNVPR}")
    ph = shape.find(f".//{P_PH}")
    off = shape.find(f".//{A_OFF}")
    ext = shape.find(f".//{A_EXT}")
    return {
        "shape_id": c_nv_pr.get("id") if c_nv_pr is not None else None,
        "shape_name": c_nv_pr.get("name") if c_nv_pr is not None else None,
        "placeholder_type": ph.get("type") if ph is not None else None,
        "position_inches": {
            "x": emu_to_inches(off.get("x")) if off is not None else None,
            "y": emu_to_inches(off.get("y")) if off is not None else None,
            "w": emu_to_inches(ext.get("cx")) if ext is not None else None,
            "h": emu_to_inches(ext.get("cy")) if ext is not None else None,
        },
    }


def text_role(meta: dict[str, Any], slide_text_index: int) -> str:
    ph_type = meta.get("placeholder_type") or ""
    shape_name = (meta.get("shape_name") or "").lower()
    if ph_type in {"title", "ctrTitle", "subTitle"} or "title" in shape_name:
        return "title"
    if slide_text_index <= 2:
        return "likely_title_or_subtitle"
    if ph_type:
        return f"placeholder:{ph_type}"
    return "body_or_label"


def should_tokenize(text: str, args: argparse.Namespace, skip_patterns: list[re.Pattern[str]]) -> bool:
    stripped = text.strip()
    if len(stripped) < args.min_chars:
        return False
    if not args.include_numeric and re.fullmatch(r"[\d\s./:：\-–—()\[\]]+", stripped):
        return False
    for pattern in skip_patterns:
        if pattern.search(stripped):
            return False
    return True


def token_for(slide_number: int, item_number: int) -> str:
    return f"{{{{S{slide_number:02d}_T{item_number:02d}}}}}"


def extract_slide(
    xml_bytes: bytes,
    slide_number: int,
    part_name: str,
    args: argparse.Namespace,
    skip_patterns: list[re.Pattern[str]],
    mutate: bool,
) -> tuple[ET.Element, dict[str, Any], dict[str, str], dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    slide_items: list[dict[str, Any]] = []
    token_originals: dict[str, str] = {}
    blank_values: dict[str, str] = {}
    item_number = 0
    visible_text_index = 0

    for shape in root.iter(P_SP):
        meta = shape_meta(shape)
        for paragraph_index, paragraph in enumerate(shape.iter(A_P), start=1):
            text = paragraph_text(paragraph)
            if not text.strip():
                continue
            visible_text_index += 1
            tokenizable = should_tokenize(text, args, skip_patterns)
            token = None
            if tokenizable:
                item_number += 1
                token = token_for(slide_number, item_number)
                token_originals[token] = text
                blank_values[token] = ""
                if mutate:
                    text_nodes = list(paragraph.iter(A_T))
                    if text_nodes:
                        text_nodes[0].text = token
                        text_nodes[0].set(XML_SPACE, "preserve")
                        for node in text_nodes[1:]:
                            node.text = ""

            slide_items.append(
                {
                    "slide": slide_number,
                    "part": part_name,
                    "token": token,
                    "text": text,
                    "role": text_role(meta, visible_text_index),
                    "paragraph_index": paragraph_index,
                    "tokenizable": tokenizable,
                    **meta,
                }
            )

    slide = {
        "slide": slide_number,
        "part": part_name,
        "text_item_count": len(slide_items),
        "token_count": len(token_originals),
        "text_items": slide_items,
    }
    return root, slide, token_originals, blank_values


def read_slide_size(zf: zipfile.ZipFile) -> dict[str, float | None]:
    try:
        root = ET.fromstring(zf.read("ppt/presentation.xml"))
    except KeyError:
        return {"width_inches": None, "height_inches": None}
    sld_sz = root.find(f".//{{{NS['p']}}}sldSz")
    if sld_sz is None:
        return {"width_inches": None, "height_inches": None}
    return {
        "width_inches": emu_to_inches(sld_sz.get("cx")),
        "height_inches": emu_to_inches(sld_sz.get("cy")),
    }


def write_json(path: Path, data: Any, pretty: bool) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2 if pretty else None),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    if not args.source_pptx.exists():
        raise SystemExit(f"找不到源 PPTX：{args.source_pptx}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    skip_patterns = [re.compile(pattern) for pattern in args.skip_regex]
    manifest: dict[str, Any] = {
        "source": str(args.source_pptx),
        "mode": "extract_real_pptx_template",
        "slide_size": {},
        "slides": [],
        "outputs": {},
    }
    all_token_originals: dict[str, str] = {}
    all_blank_values: dict[str, str] = {}
    literal_counts: Counter[str] = Counter()
    mutated_parts: dict[str, bytes] = {}

    with zipfile.ZipFile(args.source_pptx) as zf:
        parts = slide_parts(zf)
        manifest["slide_count"] = len(parts)
        manifest["slide_size"] = read_slide_size(zf)
        for slide_number, part_name in enumerate(parts, start=1):
            root, slide, token_originals, blank_values = extract_slide(
                zf.read(part_name),
                slide_number,
                part_name,
                args,
                skip_patterns,
                mutate=args.make_template_pptx,
            )
            manifest["slides"].append(slide)
            all_token_originals.update(token_originals)
            all_blank_values.update(blank_values)
            for value in token_originals.values():
                literal_counts[value] += 1
            if args.make_template_pptx:
                mutated_parts[part_name] = ET.tostring(
                    root, encoding="utf-8", xml_declaration=True
                )

    token_original_path = args.out_dir / "token_original_values.json"
    blank_values_path = args.out_dir / "fill_values.blank.json"
    manifest_path = args.out_dir / "template_manifest.json"
    literal_unique_path = args.out_dir / "literal_unique_replacements.blank.json"

    unique_literals = {
        text: "" for text, count in sorted(literal_counts.items()) if count == 1
    }
    duplicates = {
        text: count for text, count in sorted(literal_counts.items()) if count > 1
    }

    manifest["token_count"] = len(all_token_originals)
    manifest["literal_duplicate_texts"] = duplicates
    manifest["outputs"] = {
        "manifest": str(manifest_path),
        "token_original_values": str(token_original_path),
        "fill_values_blank": str(blank_values_path),
        "literal_unique_replacements_blank": str(literal_unique_path),
    }

    if args.make_template_pptx:
        template_pptx = args.out_dir / args.template_name
        with zipfile.ZipFile(args.source_pptx) as zin, zipfile.ZipFile(template_pptx, "w") as zout:
            for info in zin.infolist():
                data = mutated_parts.get(info.filename, zin.read(info.filename))
                zout.writestr(info, data)
        manifest["outputs"]["template_pptx"] = str(template_pptx)

    write_json(token_original_path, all_token_originals, args.pretty)
    write_json(blank_values_path, all_blank_values, args.pretty)
    write_json(literal_unique_path, unique_literals, args.pretty)
    write_json(manifest_path, manifest, args.pretty)

    print(json.dumps(manifest["outputs"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
