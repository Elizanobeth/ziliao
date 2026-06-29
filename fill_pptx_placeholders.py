#!/usr/bin/env python3
"""复制 PPTX 并替换指定 OOXML 部分中的文本占位符。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "xml": "http://www.w3.org/XML/1998/namespace",
}

for prefix, uri in NS.items():
    if prefix != "xml":
        ET.register_namespace(prefix, uri)

A_T = f"{{{NS['a']}}}t"
A_P = f"{{{NS['a']}}}p"
XML_SPACE = f"{{{NS['xml']}}}space"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="复制 PPTX，并替换 {{TITLE}} 这类文本占位符。"
    )
    parser.add_argument("source_pptx", type=Path, help="源文件或模板 PPTX")
    parser.add_argument(
        "output_pptx",
        type=Path,
        nargs="?",
        help="输出 PPTX。使用 --list-placeholders 时可以省略。",
    )
    parser.add_argument("--map", dest="map_path", type=Path, help="JSON 替换映射文件")
    parser.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="替换值，可重复传入。",
    )
    parser.add_argument(
        "--literal-keys",
        action="store_true",
        help="按 JSON key 原样匹配，不把无花括号 key 自动包装成 {{KEY}}。",
    )
    parser.add_argument("--prefix", default="{{", help="非精确 key 的占位符前缀")
    parser.add_argument("--suffix", default="}}", help="非精确 key 的占位符后缀")
    parser.add_argument(
        "--list-placeholders",
        action="store_true",
        help="打印检测到的占位符，然后退出，不写入 PPTX。",
    )
    parser.add_argument(
        "--placeholder-regex",
        default=r"\{\{[^{}]+\}\}",
        help="供 --list-placeholders 和 --fail-on-unreplaced 使用的正则表达式。",
    )
    parser.add_argument("--include-notes", action="store_true", help="同时替换备注页")
    parser.add_argument(
        "--include-layouts",
        action="store_true",
        help="同时替换幻灯片版式；会影响继承该版式文字的页面。",
    )
    parser.add_argument(
        "--include-masters",
        action="store_true",
        help="同时替换幻灯片母版；会影响继承该母版文字的页面。",
    )
    parser.add_argument("--include-charts", action="store_true", help="同时替换图表 XML")
    parser.add_argument(
        "--all-ppt-xml",
        action="store_true",
        help="替换每个 ppt/*.xml 部分中的文本。仅用于结构特殊的模板。",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="找不到某个待替换 token 时不报错。",
    )
    parser.add_argument(
        "--fail-on-unreplaced",
        action="store_true",
        help="如果输出文件中仍有匹配 --placeholder-regex 的占位符，则报错。",
    )
    parser.add_argument("--report", type=Path, help="写入 JSON 替换报告")
    return parser.parse_args()


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(stringify(item) for item in value)
    if isinstance(value, (dict, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def load_mapping(args: argparse.Namespace) -> dict[str, str]:
    raw: dict[str, Any] = {}
    if args.map_path:
        with args.map_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise SystemExit("--map 必须指向一个 JSON 对象")
        raw.update(loaded)

    for item in args.sets:
        if "=" not in item:
            raise SystemExit(f"--set 必须使用 KEY=VALUE 格式：{item!r}")
        key, value = item.split("=", 1)
        raw[key] = value

    replacements: dict[str, str] = {}
    for key, value in raw.items():
        token = str(key)
        if not args.literal_keys and not (
            token.startswith(args.prefix) and token.endswith(args.suffix)
        ):
            token = f"{args.prefix}{token}{args.suffix}"
        replacements[token] = stringify(value)
    return dict(sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True))


def is_target_part(name: str, args: argparse.Namespace) -> bool:
    if not name.endswith(".xml"):
        return False
    if args.all_ppt_xml:
        return name.startswith("ppt/")
    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name):
        return True
    if args.include_notes and re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name):
        return True
    if args.include_layouts and re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", name):
        return True
    if args.include_masters and re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", name):
        return True
    if args.include_charts and re.fullmatch(r"ppt/charts/chart\d+\.xml", name):
        return True
    return False


def iter_target_xml(source: Path, args: argparse.Namespace) -> list[tuple[str, bytes]]:
    parts: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(source) as zin:
        for info in zin.infolist():
            if is_target_part(info.filename, args):
                parts.append((info.filename, zin.read(info.filename)))
    return parts


def text_nodes(root: ET.Element) -> list[ET.Element]:
    return list(root.iter(A_T))


def replace_in_text(
    text: str, replacements: dict[str, str], counts: Counter[str]
) -> tuple[str, bool]:
    changed = False
    for token, replacement in replacements.items():
        found = text.count(token)
        if found:
            text = text.replace(token, replacement)
            counts[token] += found
            changed = True
    return text, changed


def set_text(node: ET.Element, value: str) -> None:
    node.text = value
    if value[:1].isspace() or value[-1:].isspace():
        node.set(XML_SPACE, "preserve")


def replace_xml_part(
    xml_bytes: bytes, replacements: dict[str, str]
) -> tuple[bytes, Counter[str], int, int]:
    root = ET.fromstring(xml_bytes)
    counts: Counter[str] = Counter()
    changed_nodes = 0
    collapsed_paragraphs = 0

    for node in text_nodes(root):
        original = node.text or ""
        updated, changed = replace_in_text(original, replacements, counts)
        if changed:
            set_text(node, updated)
            changed_nodes += 1

    for paragraph in root.iter(A_P):
        nodes = text_nodes(paragraph)
        if len(nodes) < 2:
            continue
        combined = "".join(node.text or "" for node in nodes)
        updated, changed = replace_in_text(combined, replacements, counts)
        if not changed:
            continue
        set_text(nodes[0], updated)
        for node in nodes[1:]:
            set_text(node, "")
        collapsed_paragraphs += 1

    if not changed_nodes and not collapsed_paragraphs:
        return xml_bytes, counts, changed_nodes, collapsed_paragraphs
    return (
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
        counts,
        changed_nodes,
        collapsed_paragraphs,
    )


def scan_placeholders(source: Path, args: argparse.Namespace) -> dict[str, Any]:
    pattern = re.compile(args.placeholder_regex)
    totals: Counter[str] = Counter()
    parts: dict[str, set[str]] = defaultdict(set)

    for name, xml_bytes in iter_target_xml(source, args):
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            continue
        for paragraph in root.iter(A_P):
            text = "".join(node.text or "" for node in text_nodes(paragraph))
            for match in pattern.findall(text):
                totals[match] += 1
                parts[match].add(name)

    return {
        "source": str(source),
        "placeholders": [
            {"token": token, "count": count, "parts": sorted(parts[token])}
            for token, count in sorted(totals.items())
        ],
    }


def write_filled_pptx(
    source: Path, output: Path, args: argparse.Namespace, replacements: dict[str, str]
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "source": str(source),
        "output": str(output),
        "replacements": {token: 0 for token in replacements},
        "changed_parts": [],
        "collapsed_paragraphs": 0,
        "unreplaced": {},
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(output, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if is_target_part(info.filename, args):
                try:
                    data, counts, changed_nodes, collapsed = replace_xml_part(data, replacements)
                except ET.ParseError as exc:
                    print(f"警告：跳过无法解析的 XML 部分 {info.filename}: {exc}", file=sys.stderr)
                else:
                    if changed_nodes or collapsed:
                        report["changed_parts"].append(
                            {
                                "part": info.filename,
                                "changed_text_nodes": changed_nodes,
                                "collapsed_paragraphs": collapsed,
                            }
                        )
                        report["collapsed_paragraphs"] += collapsed
                    for token, count in counts.items():
                        report["replacements"][token] += count
            zout.writestr(info, data)

    missing = [token for token, count in report["replacements"].items() if count == 0]
    if missing and not args.allow_missing:
        raise SystemExit(f"未找到这些 token：{', '.join(missing)}")

    if args.fail_on_unreplaced:
        scan_args = argparse.Namespace(**vars(args))
        scan_args.all_ppt_xml = args.all_ppt_xml
        remaining = scan_placeholders(output, scan_args)
        unreplaced = {item["token"]: item for item in remaining["placeholders"]}
        report["unreplaced"] = unreplaced
        if unreplaced:
            names = ", ".join(sorted(unreplaced))
            raise SystemExit(f"仍有未替换的占位符：{names}")

    return report


def main() -> int:
    args = parse_args()
    if not args.source_pptx.exists():
        raise SystemExit(f"找不到源 PPTX：{args.source_pptx}")
    if args.list_placeholders:
        print(json.dumps(scan_placeholders(args.source_pptx, args), ensure_ascii=False, indent=2))
        return 0
    if not args.output_pptx:
        raise SystemExit("除非使用 --list-placeholders，否则必须提供 output_pptx")

    replacements = load_mapping(args)
    if not replacements:
        raise SystemExit("没有提供替换值。请使用 --map 或 --set。")

    report = write_filled_pptx(args.source_pptx, args.output_pptx, args, replacements)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
