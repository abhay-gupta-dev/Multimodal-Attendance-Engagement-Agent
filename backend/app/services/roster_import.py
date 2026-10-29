"""Private roster parsing and non-destructive import helpers for CSV/XLSX sources."""
from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships", "p": "http://schemas.openxmlformats.org/package/2006/relationships"}
ALIASES = {
    "enrollment": {"enrollment", "enrollmentnumber", "enrollmentno", "rollnumber", "rollno", "studentid", "admissionnumber"},
    "name": {"studentname", "name", "nameofstudent"},
    "section": {"section", "classsection", "batchsection"},
}


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _xlsx_rows(content: bytes) -> tuple[list[str], list[tuple[int, dict[str, object]]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("The workbook is invalid or damaged") from exc
    with archive:
        names = set(archive.namelist())
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = workbook.find("m:sheets/m:sheet", NS)
        if sheet is None:
            raise ValueError("The workbook does not contain a worksheet")
        rel_id = sheet.attrib.get(f"{{{NS['r']}}}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next((rel.attrib.get("Target") for rel in rels.findall("p:Relationship", NS) if rel.attrib.get("Id") == rel_id), None)
        if not target:
            raise ValueError("The workbook's first worksheet could not be located")
        sheet_path = posixpath.normpath(posixpath.join("xl", target.lstrip("/"))) if not target.startswith("/") else target.lstrip("/")
        if sheet_path not in names:
            raise ValueError("The workbook's first worksheet is missing")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            sst = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in sst.findall("m:si", NS)]
        zero_formats: dict[int, str] = {}
        if "xl/styles.xml" in names:
            styles = ET.fromstring(archive.read("xl/styles.xml"))
            formats = {int(f.attrib["numFmtId"]): f.attrib.get("formatCode", "") for f in styles.findall("m:numFmts/m:numFmt", NS)}
            xfs = styles.find("m:cellXfs", NS)
            if xfs is not None:
                for style_id, xf in enumerate(xfs.findall("m:xf", NS)):
                    fmt = formats.get(int(xf.attrib.get("numFmtId", "0")), "")
                    if re.fullmatch(r"0+", fmt):
                        zero_formats[style_id] = fmt
        sheet_xml = ET.fromstring(archive.read(sheet_path))
        data_rows: list[tuple[int, dict[str, object]]] = []
        for row in sheet_xml.findall("m:sheetData/m:row", NS):
            values: dict[str, object] = {}
            for cell in row.findall("m:c", NS):
                ref = cell.attrib.get("r", "")
                col_match = re.match(r"[A-Z]+", ref)
                if not col_match:
                    continue
                value = cell.find("m:v", NS)
                typ = cell.attrib.get("t")
                if typ == "s" and value is not None:
                    result: object = shared[int(value.text or "0")]
                elif typ == "inlineStr":
                    inline = cell.find("m:is", NS)
                    result = "".join(inline.itertext()) if inline is not None else ""
                elif value is not None:
                    result = value.text or ""
                    if typ in (None, "n") and cell.attrib.get("s") is not None:
                        fmt = zero_formats.get(int(cell.attrib["s"]))
                        if fmt and re.fullmatch(r"\d+(?:\.0+)?", str(result)):
                            result = f"{int(float(str(result))):0{len(fmt)}d}"
                else:
                    result = ""
                values[col_match.group()] = result
            data_rows.append((int(row.attrib.get("r", "0")), values))
        return _locate_headers(data_rows)


def _locate_headers(rows: list[tuple[int, dict[str, object]]]) -> tuple[list[str], list[tuple[int, dict[str, object]]]]:
    for index, (row_number, values) in enumerate(rows[:30]):
        by_key = {_key(value): col for col, value in values.items()}
        mapping = {field: next((by_key[a] for a in aliases if a in by_key), None) for field, aliases in ALIASES.items()}
        if all(mapping.values()):
            records = []
            for source_row, row in rows[index + 1:]:
                mapped = {field: row.get(col, "") for field, col in mapping.items()}
                if any(str(v or "").strip() for v in mapped.values()):
                    records.append((source_row, mapped))
            return [str(values[mapping[field]]) for field in ("enrollment", "name", "section")], records
    raise ValueError("Could not identify Enrollment, Student Name, and Section headers in the first 30 rows")


def parse_roster(filename: str, content: bytes) -> tuple[list[str], list[tuple[int, dict[str, object]]]]:
    suffix = filename.rsplit(".", 1)[-1].lower()
    if suffix == "xlsx":
        return _xlsx_rows(content)
    if suffix != "csv":
        raise ValueError("Only .xlsx and .csv roster files are supported")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must use UTF-8 or UTF-8 with BOM encoding") from exc
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [(i, {str(k): v for k, v in row.items() if k is not None}) for i, row in enumerate(csv.DictReader(io.StringIO(text), dialect=dialect), start=2)]
    raw = [(i, {_key(k): v for k, v in row.items()}) for i, row in rows]
    for i, row in raw:
        mapping = {field: next((alias for alias in aliases if alias in row), None) for field, aliases in ALIASES.items()}
        if all(mapping.values()):
            records = [(rn, {field: values.get(mapping[field], "") for field in mapping}) for rn, values in raw if any(str(values.get(mapping[field], "") or "").strip() for field in mapping)]
            return list(mapping), records
    raise ValueError("Could not identify Enrollment, Student Name, and Section headers")


def validate_rows(rows: list[tuple[int, dict[str, object]]]) -> dict:
    seen: dict[str, tuple[str, str]] = {}
    valid: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    sections: Counter[str] = Counter()
    for row_number, record in rows:
        enrollment = str(record.get("enrollment", "") or "").strip()
        name = str(record.get("name", "") or "").strip()
        section = str(record.get("section", "") or "").strip().upper()
        error = None
        if not enrollment or len(enrollment) > 40:
            error = "Enrollment number is required and must be at most 40 characters"
        elif not name or len(name) > 120:
            error = "Student name is required and must be at most 120 characters"
        elif section not in set("ABCDEFG"):
            error = "Section must be one of A through G"
        elif enrollment.casefold() in seen:
            error = "Duplicate enrollment number within this file"
        if error:
            errors.append({"row": row_number, "reason": error, "kind": "invalid" if not enrollment or not name or section not in set("ABCDEFG") else "conflict"})
            continue
        seen[enrollment.casefold()] = (name, section)
        sections[section] += 1
        valid.append({"row": row_number, "student_code": enrollment, "name": name, "section": section})
    return {"source_rows": len(rows), "valid": valid, "errors": errors, "section_counts": dict(sorted(sections.items()))}
