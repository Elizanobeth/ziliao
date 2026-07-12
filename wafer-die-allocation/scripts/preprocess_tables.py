#!/usr/bin/env python3
"""Download three source tables and emit a row-preserving preprocessed XLSX.

The output workbook contains a nine-column 预处理表 and a 层数配比 sheet.
CSV/TSV/JSON/XLSX handling uses the Python standard library where possible;
XLS still uses an optional pandas adapter.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import html
import io
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile


ALIASES = {
    "PACKAGE": ["PACKAGE", "Package", "package", "产品类型"],
    "供应商": ["供应商", "Supplier", "supplier"],
    "Fab LotID": ["Fab LotID", "Fab Lot Id", "FabLotID", "LotID", "批次id", "批次ID"],
    "Bin Grade": ["Bin Grade", "BinGrade", "grade", "等级"],
    "Bin Quanity": ["Bin Quanity", "Bin Quantity", "BinQuantity", "quantity", "数量"],
    "T7 Code": ["T7 Code", "T7Code", "Wafer ID", "WaferID", "wafer_id", "晶圆ID"],
    "Lot Wafer QTY": ["Lot Wafer QTY", "Lot Wafer Qty", "LotWaferQTY", "Wafer QTY"],
    "Create Date": ["Create Date", "CreateDate", "create_date", "生产时间", "生产日期"],
    "Wafer Sale": ["Wafer Sale", "WaferSale", "wafer_sale"],
    "层数配比": ["层数配比", "配比", "Ratio", "ratio"],
}

TABLE_REQUIRED = {
    "table1": ["PACKAGE", "供应商", "Fab LotID", "Bin Grade", "Bin Quanity", "T7 Code", "Create Date"],
    "table2": ["Fab LotID", "Wafer Sale"],
    "table3": ["PACKAGE", "供应商", "层数配比"],
}

TABLE1_OUTPUT_FIELDS = ["PACKAGE", "供应商", "Fab LotID", "Bin Grade", "Bin Quanity", "T7 Code", "Lot Wafer QTY", "Create Date", "Wafer Sale"]
TABLE1_SOURCE_FIELDS = TABLE1_OUTPUT_FIELDS[:-1]


def key_norm(value: Any) -> str:
    return re.sub(r"[\s_\-–—/\\().,:：]+", "", str(value or "").strip().casefold())


def value_clean(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def spec_value(spec: Any) -> Tuple[str, Optional[str], Dict[str, str]]:
    if isinstance(spec, str):
        return spec, None, {}
    if isinstance(spec, dict):
        url = str(spec.get("url") or spec.get("path") or "").strip()
        sheet = spec.get("sheet")
        headers = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
        return url, str(sheet) if sheet is not None else None, headers
    return "", None, {}


def safe_source(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return parsed.path or url


def fetch_bytes(source: str, headers: Dict[str, str], timeout: int, max_bytes: int) -> Tuple[bytes, str, str]:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        request = urllib.request.Request(source, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                chunks: List[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"source exceeds max_bytes={max_bytes}")
                return b"".join(chunks), content_type, safe_source(source)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"download failed for {safe_source(source)}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"download failed for {safe_source(source)}: {exc.reason}") from exc
    if parsed.scheme == "file":
        path = Path(parsed.path)
    else:
        path = Path(source)
    if not path.exists() or not path.is_file():
        raise ValueError(f"source file does not exist: {path}")
    if path.stat().st_size > max_bytes:
        raise ValueError(f"source exceeds max_bytes={max_bytes}: {path}")
    return path.read_bytes(), mimetypes.guess_type(str(path))[0] or "", str(path)


def detect_format(source: str, content_type: str, data: bytes) -> str:
    suffix = Path(urlparse(source).path).suffix.casefold()
    if suffix in {".xlsx", ".xlsm"} or data[:4] == b"PK\x03\x04":
        return "xlsx"
    if suffix == ".xls":
        return "xls"
    if suffix in {".tsv"}:
        return "tsv"
    if suffix in {".csv"}:
        return "csv"
    if suffix == ".json" or "json" in content_type.casefold():
        return "json"
    if "spreadsheetml" in content_type.casefold() or "excel" in content_type.casefold():
        return "xlsx"
    sample = data[:1000].lstrip()
    if sample.startswith(b"{") or sample.startswith(b"["):
        return "json"
    return "csv"


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("unable to decode text table")


def clean_header(value: Any, index: int) -> str:
    text = str(value_clean(value) or "").strip()
    return text or f"__unnamed_{index + 1}"


def rows_from_matrix(matrix: Iterable[Iterable[Any]]) -> List[Dict[str, Any]]:
    rows = [[value_clean(v) for v in row] for row in matrix]
    rows = [row for row in rows if any(str(v).strip() for v in row if v is not None)]
    if not rows:
        return []
    headers: List[str] = []
    used: Dict[str, int] = {}
    for index, value in enumerate(rows[0]):
        header = clean_header(value, index)
        used[header] = used.get(header, 0) + 1
        headers.append(header if used[header] == 1 else f"{header}__duplicate_{used[header]}")
    result = []
    for row in rows[1:]:
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        record = {headers[i]: value_clean(padded[i]) for i in range(len(headers))}
        if any(str(v).strip() for v in record.values()):
            result.append(record)
    return result


def rows_from_csv(data: bytes, delimiter_hint: Optional[str] = None) -> List[Dict[str, Any]]:
    text = decode_text(data)
    sample = text[:8192]
    if delimiter_hint:
        delimiter = delimiter_hint
    else:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
        except csv.Error:
            delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    return rows_from_matrix(csv.reader(io.StringIO(text), delimiter=delimiter))


def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xlsx_cell_column(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference or "")
    if not letters:
        return 0
    index = 0
    for char in letters.group(0):
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def read_xlsx_sheets(data: bytes) -> Dict[str, List[Dict[str, Any]]]:
    """Read ordinary XLSX workbooks with stdlib only.

    This supports strings, shared strings, numbers, booleans and blank cells;
    it is sufficient for the flat input/output tables used by this Skill.
    """
    with ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root:
                text = "".join(node.text or "" for node in item.iter() if _xml_local(node.tag) == "t")
                shared.append(text)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for rel in rel_root:
                rels[rel.attrib.get("Id")] = rel.attrib.get("Target", "")
        result: Dict[str, List[Dict[str, Any]]] = {}
        for sheet in [node for node in workbook.iter() if _xml_local(node.tag) == "sheet"]:
            sheet_name = sheet.attrib.get("name", "Sheet")
            rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") or sheet.attrib.get("r:id")
            target = rels.get(rid, "")
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            if target not in names:
                continue
            sheet_root = ET.fromstring(archive.read(target))
            matrix: List[List[Any]] = []
            for row_node in [node for node in sheet_root.iter() if _xml_local(node.tag) == "row"]:
                cells: Dict[int, Any] = {}
                for cell in [node for node in row_node if _xml_local(node.tag) == "c"]:
                    col = _xlsx_cell_column(cell.attrib.get("r", ""))
                    cell_type = cell.attrib.get("t", "")
                    value = ""
                    if cell_type == "inlineStr":
                        value = "".join(node.text or "" for node in cell.iter() if _xml_local(node.tag) == "t")
                    else:
                        value_node = next((node for node in cell if _xml_local(node.tag) == "v"), None)
                        raw = value_node.text if value_node is not None else ""
                        if cell_type == "s" and raw != "":
                            value = shared[int(raw)]
                        elif cell_type == "b":
                            value = raw == "1"
                        else:
                            try:
                                value = int(raw) if raw and str(int(float(raw))) == str(raw) else float(raw) if raw else ""
                            except ValueError:
                                value = raw
                    cells[col] = value
                if cells:
                    width = max(cells) + 1
                    row_values = [""] * width
                    for col, value in cells.items():
                        row_values[col] = value
                    matrix.append(row_values)
            result[sheet_name] = rows_from_matrix(matrix)
        return result


def _xlsx_cell_xml(reference: str, value: Any) -> str:
    if value is None:
        return f'<c r="{reference}" t="inlineStr"><is><t></t></is></c>'
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}" t="n"><v>{value}</v></c>'
    text = html.escape(str(value), quote=False)
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _xlsx_col_name(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def write_xlsx(path: str, sheets: Dict[str, List[Dict[str, Any]]]) -> None:
    content_types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">', '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>', '<Default Extension="xml" ContentType="application/xml"/>', '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
    workbook_sheets = []
    workbook_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    sheet_files: Dict[str, str] = {}
    for index, name in enumerate(sheets, start=1):
        sheet_path = f"xl/worksheets/sheet{index}.xml"
        sheet_files[name] = sheet_path
        workbook_sheets.append(f'<sheet name="{html.escape(name, quote=True)}" sheetId="{index}" r:id="rId{index}"/>')
        workbook_rels.append(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>')
        content_types.append(f'<Override PartName="/{sheet_path}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content_types.append('</Types>')
    workbook_rels.append('</Relationships>')
    workbook_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(workbook_sheets) + '</sheets></workbook>'
    root_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", "".join(workbook_rels))
        for name, rows in sheets.items():
            headers = list(rows[0].keys()) if rows else []
            matrix = [headers] + [[row.get(header, "") for header in headers] for row in rows]
            xml_rows = []
            for row_index, row in enumerate(matrix, start=1):
                cells = "".join(_xlsx_cell_xml(f"{_xlsx_col_name(col_index)}{row_index}", value) for col_index, value in enumerate(row))
                xml_rows.append(f'<row r="{row_index}">{cells}</row>')
            sheet_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + '</sheetData></worksheet>'
            archive.writestr(sheet_files[name], sheet_xml)


def rows_from_excel(data: bytes, fmt: str, sheet: Optional[str]) -> Tuple[List[Dict[str, Any]], str]:
    if fmt == "xlsx":
        workbook = read_xlsx_sheets(data)
        names = list(workbook)
        if not names:
            raise ValueError("workbook contains no sheets")
        sheet_name = sheet or names[0]
        if sheet_name not in names:
            raise ValueError(f"sheet {sheet_name!r} not found; available sheets: {names}")
        return workbook[sheet_name], sheet_name
    try:
        import pandas as pd  # type: ignore
    except ImportError as exc:
        raise ValueError("XLS input requires pandas plus an Excel engine; install them or use XLSX/CSV") from exc
    frame = pd.read_excel(io.BytesIO(data), sheet_name=sheet or 0, dtype=object)
    sheet_name = str(sheet or 0)
    return rows_from_matrix([list(frame.columns)] + frame.where(frame.notna(), "").values.tolist()), sheet_name


def read_table(source: str, sheet: Optional[str], headers: Dict[str, str], timeout: int, max_bytes: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    data, content_type, safe = fetch_bytes(source, headers, timeout, max_bytes)
    fmt = detect_format(source, content_type, data)
    if fmt == "json":
        decoded = json.loads(decode_text(data))
        if isinstance(decoded, dict):
            for key in ("rows", "data", "table"):
                if isinstance(decoded.get(key), list):
                    rows = decoded[key]
                    break
            else:
                raise ValueError("JSON source must contain a rows/data/table array")
        elif isinstance(decoded, list):
            rows = decoded
        else:
            raise ValueError("JSON source must be an array or an object containing rows")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("JSON table rows must be objects")
        return [dict(row) for row in rows], {"format": fmt, "source": safe, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    if fmt in {"xlsx", "xls"}:
        rows, selected_sheet = rows_from_excel(data, fmt, sheet)
    else:
        rows, selected_sheet = rows_from_csv(data, "\t" if fmt == "tsv" else None), None
    return rows, {"format": fmt, "source": safe, "sheet": selected_sheet, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def canonicalize(rows: List[Dict[str, Any]], table_name: str, dedupe_exact: bool = True) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    warnings: List[str] = []
    errors: List[str] = []
    alias_map: Dict[str, str] = {}
    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            alias_map[key_norm(alias)] = canonical
    result: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(rows, start=1):
        normalized: Dict[str, Any] = {}
        for key, value in raw.items():
            canonical = alias_map.get(key_norm(key), str(key).strip())
            value = value_clean(value)
            if canonical in normalized and str(normalized[canonical]).strip() and str(value).strip():
                warnings.append(f"{table_name} row {index} has duplicate mapped field {canonical}; retained the first non-empty value")
                continue
            normalized[canonical] = value
        signature = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
        if dedupe_exact and signature in seen:
            warnings.append(f"{table_name} contains an exact duplicate row at source row {index}; removed it")
            continue
        seen.add(signature)
        result.append(normalized)
    present = set(result[0]) if result else set()
    for required in TABLE_REQUIRED[table_name]:
        if required not in present:
            errors.append(f"{table_name} missing required column after normalization: {required}")
    if table_name == "table1" and "Lot Wafer QTY" not in present:
        warnings.append("table1 has no Lot Wafer QTY column; distinct T7 Code count will be used for wafer count")
    return result, warnings, errors


def ratio_value(value: Any) -> Optional[str]:
    text = str(value or "").strip().replace("：", ":")
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", text)
    if not match or int(match.group(1)) <= 0 or int(match.group(2)) <= 0:
        return None
    return f"{int(match.group(1))}:{int(match.group(2))}"


def build_ratio_matches(table1: List[Dict[str, Any]], table3: List[Dict[str, Any]], threshold: float = 0.80) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """Find one ratio for every PACKAGE/Supplier pair in table1."""
    warnings: List[str] = []
    errors: List[str] = []
    pairs = sorted({(str(row.get("PACKAGE", "")).strip(), str(row.get("供应商", "")).strip()) for row in table1 if str(row.get("PACKAGE", "")).strip() and str(row.get("供应商", "")).strip()})
    matches: List[Dict[str, Any]] = []
    for package, supplier in pairs:
        supplier_norm = key_norm(supplier)
        candidates = []
        for row in table3:
            if key_norm(row.get("供应商", "")) != supplier_norm:
                continue
            candidate_package = str(row.get("PACKAGE", "")).strip()
            score = difflib.SequenceMatcher(None, key_norm(package), key_norm(candidate_package)).ratio()
            parsed_ratio = ratio_value(row.get("层数配比"))
            if score >= threshold and parsed_ratio:
                candidates.append((score, candidate_package, parsed_ratio))
        status = "matched"
        matched_package = ""
        ratio = ""
        score = 0.0
        if not candidates:
            status = "unmatched"
            errors.append(f"no unique PACKAGE/Supplier ratio match for package={package!r}, supplier={supplier!r}")
        else:
            candidates.sort(reverse=True)
            score, matched_package, ratio = candidates[0]
            tied_ratios = {item[2] for item in candidates if score - item[0] < 0.03}
            if len(tied_ratios) > 1:
                status = "ambiguous"
                errors.append(f"ambiguous PACKAGE/Supplier ratio match for package={package!r}, supplier={supplier!r}")
        matches.append({"PACKAGE": package, "供应商": supplier, "层数配比": ratio, "匹配PACKAGE": matched_package, "匹配得分": round(score, 4), "状态": status})
    return matches, warnings, errors


def build_preprocessed_table(table1: List[Dict[str, Any]], table2: List[Dict[str, Any]], warnings: List[str]) -> List[Dict[str, Any]]:
    """Add Wafer Sale without changing table1's non-empty row count."""
    sale_by_lot: Dict[str, str] = {}
    for row in table2:
        lot_id = str(row.get("Fab LotID", "")).strip()
        sale = str(row.get("Wafer Sale", "")).strip().upper()
        if not lot_id:
            continue
        if sale == "N" or not sale_by_lot.get(lot_id):
            sale_by_lot[lot_id] = sale
    result = []
    for row in table1:
        output_row = {field: row.get(field, "") for field in TABLE1_SOURCE_FIELDS}
        lot_id = str(output_row.get("Fab LotID", "")).strip()
        output_row["Wafer Sale"] = sale_by_lot.get(lot_id, "")
        if not output_row["Wafer Sale"]:
            warnings.append(f"no Wafer Sale match for Fab LotID {lot_id!r}")
        result.append(output_row)
    return result


def url_spec_from_payload(payload: Dict[str, Any], table_name: str) -> Any:
    table_urls = payload.get("table_urls") or payload.get("urls") or {}
    if table_name in table_urls:
        return table_urls[table_name]
    return payload.get(f"{table_name}_url")


def preprocess(payload: Dict[str, Any], timeout: int = 60, max_bytes: int = 200 * 1024 * 1024) -> Dict[str, Any]:
    common_headers = {str(k): str(v) for k, v in (payload.get("request_headers") or {}).items()}
    output: Dict[str, Any] = {"table1": [], "table2": [], "table3": [], "parameters": dict(payload.get("parameters") or {})}
    errors: List[str] = []
    warnings: List[str] = []
    sources: Dict[str, Any] = {}
    for table_name in ("table1", "table2", "table3"):
        spec = url_spec_from_payload(payload, table_name)
        source, sheet, headers = spec_value(spec)
        if not source:
            errors.append(f"missing URL or file source for {table_name}")
            continue
        merged_headers = dict(common_headers)
        merged_headers.update(headers)
        try:
            rows, source_meta = read_table(source, sheet, merged_headers, timeout, max_bytes)
            # Preserve every non-empty table1 source row. The output table is
            # a row-preserving left join, not a one-to-many join expansion.
            normalized, row_warnings, row_errors = canonicalize(rows, table_name, dedupe_exact=(table_name != "table1"))
            output[table_name] = normalized
            sources[table_name] = source_meta
            warnings.extend(row_warnings)
            errors.extend(row_errors)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{table_name} preprocessing failed for {safe_source(source)}: {exc}")
    if not errors:
        ratio_matches, ratio_warnings, ratio_errors = build_ratio_matches(output["table1"], output["table3"])
        warnings.extend(ratio_warnings)
        errors.extend(ratio_errors)
        output["table1"] = build_preprocessed_table(output["table1"], output["table2"], warnings)
        output["ratio_matches"] = ratio_matches
    output["preprocess"] = {
        "status": "ok" if not errors else "invalid",
        "sources": sources,
        "warnings": warnings,
        "errors": errors,
        "table1_source_row_count": len(output["table1"]),
        "preprocessed_row_count": len(output["table1"]),
        "row_count_preserved": True,
        "max_bytes": max_bytes,
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and normalize wafer allocation table URLs")
    parser.add_argument("--input", required=True, help="JSON containing table_urls and parameters")
    parser.add_argument("--output", required=True, help="preprocessed .xlsx or .json output path")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-bytes", type=int, default=200 * 1024 * 1024)
    args = parser.parse_args()
    with open(args.input, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit("input must be a JSON object")
    output = preprocess(payload, timeout=args.timeout, max_bytes=args.max_bytes)
    if Path(args.output).suffix.casefold() == ".xlsx":
        write_xlsx(args.output, {"预处理表": output["table1"], "层数配比": output.get("ratio_matches", [])})
    else:
        text = json.dumps(output, ensure_ascii=False, indent=2, default=str)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if output["preprocess"]["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
