"""Parse student-provided CSV/XLSX attendance exports without portal credentials."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

NAMESPACE = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
ALIASES = {
    "student_code": {"studentid", "studentcode", "enrollment", "enrollmentno", "enrollmentnumber", "rollno", "rollnumber", "scholarno", "scholarnumber", "studentaccid"},
    "student_name": {"studentname", "name", "nameofstudent"},
    "academic_session": {"academicsession", "session", "sessionyear", "academicyear"},
    "semester": {"semester", "sem", "semestername"},
    "subject_code": {"subjectcode", "coursecode", "papercode", "subjectid"},
    "subject_name": {"subject", "subjectname", "coursename", "paper", "papername", "course"},
    "classes_conducted": {"classesconducted", "totalclasses", "totallectures", "lecturesheld", "classheld", "conducted"},
    "classes_attended": {"classesattended", "attended", "presentcount", "classespresent", "lectureattended"},
    "absences": {"absences", "absent", "classesabsent", "absentcount", "lectureabsent"},
    "attendance_percentage": {"attendancepercentage", "attendancepercent", "attendance", "percentage", "attendancepct"},
    "attendance_date": {"date", "attendancedate", "classdate"},
    "status": {"status", "attendancestatus"},
}


def normalize_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _column_index(reference: str) -> int:
    match = re.match(r"([A-Z]+)", reference)
    if not match:
        return -1
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def _xlsx_table(content: bytes) -> list[tuple[int, list[str]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("The XLSX report is invalid or damaged") from exc
    with archive:
        files = archive.infolist()
        if len(files) > 2_000 or sum(item.file_size for item in files) > 50 * 1024 * 1024:
            raise ValueError("The XLSX report is too large after decompression")
        names = {item.filename for item in files}
        if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
            raise ValueError("The XLSX report is missing workbook metadata")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = workbook.find("m:sheets/m:sheet", NAMESPACE)
        if sheet is None:
            raise ValueError("The XLSX report does not contain a worksheet")
        relationship_id = sheet.attrib.get(f"{{{NAMESPACE['r']}}}id")
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next((item.attrib.get("Target") for item in relationships.findall("p:Relationship", NAMESPACE) if item.attrib.get("Id") == relationship_id), None)
        if not target:
            raise ValueError("The first worksheet could not be located")
        worksheet_path = posixpath.normpath(posixpath.join("xl", target.lstrip("/"))) if not target.startswith("/") else target.lstrip("/")
        if worksheet_path not in names or not worksheet_path.startswith("xl/"):
            raise ValueError("The first worksheet path is invalid")
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = ["".join(item.itertext()) for item in root.findall("m:si", NAMESPACE)]
        root = ET.fromstring(archive.read(worksheet_path))
        output: list[tuple[int, list[str]]] = []
        for row in root.findall("m:sheetData/m:row", NAMESPACE):
            source_row = int(row.attrib.get("r", "0"))
            values: list[str] = []
            for cell in row.findall("m:c", NAMESPACE):
                index = _column_index(cell.attrib.get("r", ""))
                if index < 0:
                    continue
                while len(values) <= index:
                    values.append("")
                value = cell.find("m:v", NAMESPACE)
                kind = cell.attrib.get("t")
                if kind == "s" and value is not None:
                    try:
                        values[index] = shared_strings[int(value.text or "0")]
                    except (IndexError, ValueError):
                        values[index] = ""
                elif kind == "inlineStr":
                    inline = cell.find("m:is", NAMESPACE)
                    values[index] = "".join(inline.itertext()) if inline is not None else ""
                elif value is not None:
                    values[index] = value.text or ""
            if any(value.strip() for value in values):
                output.append((source_row, values))
            if len(output) > MAX_ROWS + 30:
                raise ValueError(f"Reports may contain at most {MAX_ROWS:,} rows")
        return output


def _table(filename: str, content: bytes) -> list[tuple[int, list[str]]]:
    if not content:
        raise ValueError("The attendance report is empty")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("Attendance report must be 10 MB or smaller")
    suffix = filename.rsplit(".", 1)[-1].lower()
    if suffix == "xlsx":
        return _xlsx_table(content)
    if suffix != "csv":
        raise ValueError("Only CSV and XLSX attendance exports are supported")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV reports must use UTF-8 encoding") from exc
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect=dialect))
    if len(rows) > MAX_ROWS + 30:
        raise ValueError(f"Reports may contain at most {MAX_ROWS:,} rows")
    return [(index + 1, row) for index, row in enumerate(rows) if any(str(value).strip() for value in row)]


def parse_attendance_report(filename: str, content: bytes) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    rows = _table(filename, content)
    for header_index, (header_row_number, cells) in enumerate(rows[:30]):
        normalized = {normalize_header(value): index for index, value in enumerate(cells) if normalize_header(value)}
        mapping = {field: next((normalized[key] for key in aliases if key in normalized), None) for field, aliases in ALIASES.items()}
        required_identity = mapping["student_code"] is not None and mapping["academic_session"] is not None and mapping["semester"] is not None
        subject_present = mapping["subject_code"] is not None or mapping["subject_name"] is not None
        if not required_identity or not subject_present:
            continue
        records: list[tuple[int, dict[str, str]]] = []
        for source_row_number, values in rows[header_index + 1:]:
            record = {field: (values[index].strip() if index < len(values) else "") for field, index in mapping.items() if index is not None}
            if any(value for value in record.values()):
                records.append((source_row_number, record))
        headers = [field for field, index in mapping.items() if index is not None]
        return headers, records
    raise ValueError("Could not find a header row with student/enrollment ID, academic session, semester, and subject name or code")


def parse_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    patterns = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y")
    for pattern in patterns:
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            pass
    try:
        serial = float(raw)
        if 20_000 <= serial <= 80_000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    raise ValueError("Attendance date is not in a recognized date format")


def _integer(value: str, label: str) -> int | None:
    raw = (value or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number") from exc
    if not number.is_integer() or number < 0 or number > 100_000:
        raise ValueError(f"{label} must be a whole number between 0 and 100000")
    return int(number)


def _percentage(value: str) -> float | None:
    raw = (value or "").strip().replace("%", "").replace(",", "")
    if not raw:
        return None
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError("Attendance percentage must be numeric") from exc
    if not 0 <= number <= 100:
        raise ValueError("Attendance percentage must be between 0 and 100")
    return number


def _semester(value: str) -> int:
    match = re.search(r"(?:semester|sem)?\s*(\d{1,2})", (value or "").strip(), re.IGNORECASE)
    if not match or not 1 <= int(match.group(1)) <= 16:
        raise ValueError("Semester must be between 1 and 16")
    return int(match.group(1))


def validate_attendance_rows(rows: list[tuple[int, dict[str, str]]], *, student_code: str, student_name: str) -> dict:
    """Normalize report rows, enforcing identity and source-provided values only."""
    valid: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()
    expected_code = student_code.strip().casefold()
    expected_name = normalize_header(student_name)
    for row_number, raw in rows:
        try:
            reported_code = (raw.get("student_code") or "").strip()
            if not reported_code or reported_code.casefold() != expected_code:
                raise ValueError("Enrollment/student ID does not match your signed-in account")
            reported_name = (raw.get("student_name") or "").strip()
            if reported_name and normalize_header(reported_name) != expected_name:
                raise ValueError("Student name does not match the signed-in account")
            academic_session = (raw.get("academic_session") or "").strip()
            if not academic_session or len(academic_session) > 40:
                raise ValueError("Academic session is required and must be at most 40 characters")
            semester = _semester(raw.get("semester", ""))
            subject_code = (raw.get("subject_code") or "").strip() or None
            subject_name = (raw.get("subject_name") or "").strip() or None
            if not subject_code and not subject_name:
                raise ValueError("Subject name or subject code is required")
            if subject_code and len(subject_code) > 50 or subject_name and len(subject_name) > 160:
                raise ValueError("Subject code or name exceeds its maximum length")
            classes_conducted = _integer(raw.get("classes_conducted", ""), "Classes conducted")
            classes_attended = _integer(raw.get("classes_attended", ""), "Classes attended")
            absences = _integer(raw.get("absences", ""), "Absences")
            attendance_percentage = _percentage(raw.get("attendance_percentage", ""))
            attendance_date = parse_date(raw.get("attendance_date", ""))
            status = (raw.get("status") or "").strip() or None
            if status and len(status) > 24:
                raise ValueError("Attendance status must be at most 24 characters")
            if classes_conducted is not None and classes_attended is not None:
                if classes_attended > classes_conducted:
                    raise ValueError("Classes attended cannot exceed classes conducted")
                if absences is not None and absences > classes_conducted:
                    raise ValueError("Absences cannot exceed classes conducted")
            has_summary = attendance_percentage is not None or (classes_conducted is not None and classes_attended is not None)
            has_dated_status = attendance_date is not None and status is not None
            if has_summary:
                record_type = "summary"
            elif has_dated_status:
                record_type = "dated"
            else:
                raise ValueError("Provide a source attendance percentage or conducted/attended counts, or both an attendance date and status")
            canonical = {
                "student_code": expected_code,
                "academic_session": academic_session,
                "semester": semester,
                "subject_code": subject_code,
                "subject_name": subject_name,
                "record_type": record_type,
                "classes_conducted": classes_conducted,
                "classes_attended": classes_attended,
                "absences": absences,
                "attendance_percentage": attendance_percentage,
                "attendance_date": attendance_date.isoformat() if attendance_date else None,
                "status": status,
            }
            fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
            if fingerprint in seen:
                errors.append({"row": row_number, "kind": "duplicate", "reason": "Duplicate attendance row in this report"})
                continue
            seen.add(fingerprint)
            canonical.update({"row_number": row_number, "attendance_date": attendance_date, "source_record_hash": fingerprint, "source_payload": raw})
            valid.append(canonical)
        except ValueError as exc:
            errors.append({"row": row_number, "kind": "invalid", "reason": str(exc)})
    return {"valid": valid, "errors": errors, "source_rows": len(rows)}
