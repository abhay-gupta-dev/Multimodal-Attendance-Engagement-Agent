import pytest
from io import BytesIO
from zipfile import ZipFile
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException, Request, Response

from app.database.session import Base
from app.main import AudioActivityIn, ExternalAttendanceReview, accsoft_review_record, accsoft_student_attendance, add_attendance, attendance_csv, capabilities, commit_attendance_import, create_attendance_import_preview, create_class, create_session, create_student, dashboard_summary, enroll, ensure_schema, health, save_live_audio_activity, start_session, stop_session
from app.models import ExternalAttendanceRecord, Role, Student, UserAccount
from app.schemas.schemas import AttendanceCreate, ClassCreate, SessionCreate, StudentCreate
from app.services.analytics import engagement_indicator
from app.services.attendance_report import parse_attendance_report, validate_attendance_rows
from app.services.auth import hash_password, verify_password, token_digest
from app.models import AuthSession
import app.main as main_module
from datetime import datetime, timedelta, timezone
from app.audio.analysis import analyze_wav
from app.vision.analysis import analyze_image, analyze_video, vision_status

@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def staff_request(user_id=1, role="super_admin"):
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("testclient", 80), "server": ("testserver", 80), "scheme": "http", "query_string": b""})
    request.state.auth_user_id = user_id
    request.state.auth_role = role
    request.state.auth_student_id = None
    return request


def seed_staff_user(db):
    role = Role(name="super_admin", permissions_json="[]")
    db.add(role); db.flush()
    user = UserAccount(login_name="test-admin", password_hash=hash_password("a strong test password"), role_id=role.id)
    db.add(user); db.commit()
    return user


def seed_student_user(db, code="LNCT-TEST-001"):
    role = db.scalar(select(Role).where(Role.name == "student"))
    if not role:
        role = Role(name="student", permissions_json='["self:read"]')
        db.add(role); db.flush()
    student = Student(student_code=code, name="Sample Learner", institution_code="LNCT", is_demo=False)
    db.add(student); db.flush()
    user = UserAccount(login_name=f"student-{code.lower()}", password_hash=hash_password("a strong test password"), role_id=role.id, student_id=student.id)
    db.add(user); db.commit()
    request = staff_request(user.id, "student")
    request.state.auth_student_id = student.id
    return user, student, request


def test_health() -> None:
    assert health()["status"] == "ok"


def test_dashboard_summary(db) -> None:
    db.add(Student(student_code="TEST-001", name="Test Student"))
    db.commit()
    assert dashboard_summary(db)["students"] == 1
    assert "attendance_percentage" in dashboard_summary(db)


def test_indicator_omits_missing_signals() -> None:
    assert engagement_indicator({"face": 70, "audio": None}) == 70
    assert engagement_indicator({"face": None, "audio": None}) is None


def test_invalid_wav_is_rejected() -> None:
    with pytest.raises(ValueError, match="valid PCM WAV"):
        analyze_wav(b"not audio")


def test_video_invalid_bytes_return_a_readable_error() -> None:
    with pytest.raises(ValueError, match="could not open"):
        analyze_video(b"not a video")


def test_video_incremental_reader_handles_a_small_valid_clip(tmp_path) -> None:
    import cv2
    import numpy as np
    path = tmp_path / "small.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (64, 64))
    if not writer.isOpened():
        pytest.skip("This OpenCV installation has no MJPG encoder")
    for _ in range(5):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()
    result = analyze_video(path.read_bytes(), max_frames=10)
    assert result["sampled_frames"] > 0
    assert result["frame_count"] == 5


def test_vision_capabilities_report_optional_landmark_state() -> None:
    status = vision_status()
    assert "landmarks" in status and "landmark_status" in status
    assert capabilities()["identity_matching"]["available"] is False


def test_live_audio_activity_requires_active_session_and_stores_only_derived_values(db) -> None:
    user = seed_staff_user(db)
    request = staff_request(user.id)
    session = create_session(SessionCreate(title="Live lesson", source="webcam"), request, db, user.id)
    with pytest.raises(HTTPException) as inactive:
        save_live_audio_activity(session.id, AudioActivityIn(duration_seconds=1, activity_percentage=24), request, db, user.id)
    assert inactive.value.status_code == 409
    start_session(session.id, request, db, user.id)
    result = save_live_audio_activity(session.id, AudioActivityIn(duration_seconds=1, activity_percentage=24), request, db, user.id)
    assert result["activity_percentage"] == 24
    assert result["data_quality"] == "browser-rms-anonymous"


def test_live_image_path_counts_without_storing_frame() -> None:
    import cv2
    import numpy as np
    ok, encoded = cv2.imencode(".jpg", np.zeros((64, 64, 3), dtype=np.uint8))
    assert ok
    result = analyze_image(encoded.tobytes())
    assert result["face_detections"] == 0


def test_schema_update_adds_landmark_columns_to_previous_database(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE vision_metrics (id INTEGER PRIMARY KEY, session_id INTEGER, recorded_at DATETIME, sampled_frames INTEGER, face_detections INTEGER, data_quality VARCHAR(30))")
    ensure_schema(engine)
    names = {column["name"] for column in __import__("sqlalchemy").inspect(engine).get_columns("vision_metrics")}
    assert {"landmark_faces", "avg_yaw_proxy", "avg_pitch_proxy"}.issubset(names)
    engine.dispose()


def test_class_session_attendance_workflow_and_duplicate_guard(db) -> None:
    user = seed_staff_user(db)
    request = staff_request(user.id)
    classroom = create_class(ClassCreate(name="Test class"), db)
    student = create_student(StudentCreate(student_code="T-1", name="Learner One"), db)
    enroll(classroom.id, student.id, db)
    session = create_session(SessionCreate(class_id=classroom.id, title="Lesson", source="demo"), request, db, user.id)
    assert start_session(session.id, request, db, user.id).status == "active"
    record = add_attendance(session.id, AttendanceCreate(student_id=student.id, status="present", duration_seconds=900), request, db, user.id)
    assert record.source == "manual"
    with pytest.raises(HTTPException) as error:
        add_attendance(session.id, AttendanceCreate(student_id=student.id, status="present"), request, db, user.id)
    assert error.value.status_code == 409
    assert stop_session(session.id, request, db, user.id).status == "completed"
    report = attendance_csv(db)
    assert report.media_type == "text/csv"
    assert "attachment; filename=attendance.csv" in report.headers["content-disposition"]


def test_password_hash_and_token_digest_are_one_way() -> None:
    encoded = hash_password("correct horse battery staple")
    assert encoded != "correct horse battery staple"
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)
    assert token_digest("opaque-session-token") != "opaque-session-token"


def test_login_invalid_password_and_student_scope(db) -> None:
    role = Role(name="student", permissions_json='["self:read"]')
    db.add(role); db.flush()
    student = Student(student_code="LNCT-PRIVATE-1", name="Private Student", institution_code="LNCT", is_demo=False)
    db.add(student); db.flush()
    account = UserAccount(login_name="private-student", password_hash=hash_password("student password 123"), role_id=role.id, student_id=student.id)
    db.add(account); db.commit()
    request = staff_request(account.id, "student")
    request.state.auth_student_id = student.id
    with pytest.raises(HTTPException) as invalid:
        main_module.login(main_module.AuthInput(login_name="private-student", password="incorrect password"), request, Response(), db)
    assert invalid.value.status_code == 401
    valid = main_module.login(main_module.AuthInput(login_name="private-student", password="student password 123"), request, Response(), db)
    assert valid["user"]["student_code"] == "LNCT-PRIVATE-1"
    assert main_module.auth_me(request, db)["user"]["role"] == "student"
    attendance = main_module.my_attendance(request, db)
    assert attendance["attendance_percentage"] is None
    assert attendance["message"] == "No verified attendance records are available for this period."
    with pytest.raises(HTTPException) as blocked:
        main_module.require_roles("super_admin")(request)
    assert blocked.value.status_code == 403


def test_bootstrap_is_one_time_and_admin_login_is_private(db, monkeypatch) -> None:
    db.add(Role(name="super_admin", permissions_json='["accounts:manage"]'))
    db.commit()
    monkeypatch.setattr(main_module.settings, "auth_bootstrap_token", "local-test-bootstrap-token")
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(main_module.settings, "auth_bootstrap_token_expires_at", expiry)
    request = staff_request()
    payload = main_module.AuthInput(login_name="first-admin", password="a sufficiently long passphrase")
    with pytest.raises(HTTPException) as missing:
        main_module.bootstrap_admin(payload, request, Response(), None, db)
    assert missing.value.status_code == 400
    with pytest.raises(HTTPException) as incorrect:
        main_module.bootstrap_admin(payload, request, Response(), "wrong-token", db)
    assert incorrect.value.status_code == 403
    monkeypatch.setattr(main_module.settings, "auth_bootstrap_token_expires_at", (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    with pytest.raises(HTTPException) as expired:
        main_module.bootstrap_admin(payload, request, Response(), "local-test-bootstrap-token", db)
    assert expired.value.status_code == 410
    monkeypatch.setattr(main_module.settings, "auth_bootstrap_token", "")
    with pytest.raises(HTTPException) as unconfigured:
        main_module.bootstrap_admin(payload, request, Response(), "local-test-bootstrap-token", db)
    assert unconfigured.value.status_code == 503
    monkeypatch.setattr(main_module.settings, "auth_bootstrap_token", "local-test-bootstrap-token")
    monkeypatch.setattr(main_module.settings, "auth_bootstrap_token_expires_at", expiry)
    result = main_module.bootstrap_admin(payload, request, Response(), "local-test-bootstrap-token", db)
    assert result["user"]["role"] == "super_admin"
    with pytest.raises(HTTPException) as duplicate:
        main_module.bootstrap_admin(payload, request, Response(), "local-test-bootstrap-token", db)
    assert duplicate.value.status_code == 409
    status = main_module.auth_status(db)
    assert status["bootstrap_token_state"] == "disabled"
    assert "local-test-bootstrap-token" not in str(status)
    auth = db.scalar(select(AuthSession))
    assert auth is not None
    assert len(auth.token_hash) == 64
    with pytest.raises(HTTPException) as invalid:
        main_module.login(main_module.AuthInput(login_name="first-admin", password="incorrect password"), request, Response(), db)
    assert invalid.value.status_code == 401
    login = main_module.login(payload, request, Response(), db)
    assert login["user"]["login_name"] == "first-admin"


def test_accsoft_attendance_upload_preview_import_review_and_duplicate_guard(db):
    user, student, request = seed_student_user(db)
    csv_bytes = (
        "Student ID,Student Name,Academic Session,Semester,Subject Code,Subject Name,Classes Conducted,Classes Attended,Attendance %\n"
        "LNCT-TEST-001,Sample Learner,2025-26,5,CS501,Data Structures,20,18,90\n"
    ).encode()
    preview = create_attendance_import_preview(user, student, "attendance.csv", csv_bytes, db)
    assert preview["accepted_rows"] == 1
    assert preview["duplicate_rows"] == preview["rejected_rows"] == 0
    result = commit_attendance_import(preview["preview_token"], user, student, "attendance.csv", csv_bytes, db)
    assert result["imported_rows"] == 1
    assert db.scalar(select(ExternalAttendanceRecord).where(ExternalAttendanceRecord.student_id == student.id)).verification_status == "pending_review"
    private_data = accsoft_student_attendance(request, db)
    assert private_data["verified_records"] == 0
    assert private_data["message"] == "No verified attendance data has been imported or retrieved yet."
    assert len(private_data["records"]) == 1

    admin = seed_staff_user(db)
    record = db.scalar(select(ExternalAttendanceRecord).where(ExternalAttendanceRecord.student_id == student.id))
    reviewed = accsoft_review_record(record.id, ExternalAttendanceReview(decision="verified", reason="Checked against the official report"), staff_request(admin.id), admin.id, db)
    assert reviewed["verification_status"] == "verified"
    assert accsoft_student_attendance(request, db)["verified_records"] == 1

    second_preview = create_attendance_import_preview(user, student, "attendance.csv", csv_bytes, db)
    assert second_preview["accepted_rows"] == 0
    assert second_preview["duplicate_rows"] == 1


def test_accsoft_report_identity_mismatch_is_rejected_and_student_scope_is_enforced(db):
    _, student, request = seed_student_user(db)
    csv_bytes = (
        "Enrollment No,Academic Session,Semester,Subject,Classes Conducted,Classes Attended\n"
        "SOMEONE-ELSE,2025-26,5,Networks,10,8\n"
    ).encode()
    from app.main import attendance_report_preview
    preview = attendance_report_preview("report.csv", csv_bytes, student, db)
    assert len(preview["valid"]) == 0
    assert preview["rejected_rows"] == 1
    assert "does not match" in preview["errors"][0]["reason"]
    other_request = staff_request(999, "student")
    other_request.state.auth_student_id = student.id
    with pytest.raises(HTTPException) as denied:
        accsoft_student_attendance(other_request, db)
    assert denied.value.status_code == 403


def test_accsoft_preview_requires_same_file_and_expires(db):
    user, student, _ = seed_student_user(db)
    report = (
        "Student ID,Academic Session,Semester,Subject,Classes Conducted,Classes Attended\n"
        "LNCT-TEST-001,2025-26,5,Networks,10,8\n"
    ).encode()
    preview = create_attendance_import_preview(user, student, "report.csv", report, db)
    with pytest.raises(HTTPException) as changed:
        commit_attendance_import(preview["preview_token"], user, student, "report.csv", report.replace(b",10,8", b",10,9"), db)
    assert changed.value.status_code == 409
    assert db.scalar(select(ExternalAttendanceRecord)) is None

    job = db.scalar(select(main_module.AttendanceImportJob).where(main_module.AttendanceImportJob.preview_token == preview["preview_token"]))
    job.created_at = datetime.utcnow() - timedelta(hours=2)
    db.commit()
    with pytest.raises(HTTPException) as expired:
        commit_attendance_import(preview["preview_token"], user, student, "report.csv", report, db)
    assert expired.value.status_code == 410
    assert job.status == "expired"


def test_accsoft_routes_are_registered_and_review_queue_is_superadmin_only():
    paths = {route.path for route in main_module.app.routes}
    assert "/api/v1/accsoft/student/attendance/preview" in paths
    assert "/api/v1/accsoft/student/attendance/{preview_token}/confirm" in paths
    assert "/api/v1/accsoft/admin/pending" in paths
    department_request = staff_request(7, "department_admin")
    with pytest.raises(HTTPException) as forbidden:
        main_module.require_roles("super_admin")(department_request)
    assert forbidden.value.status_code == 403


def test_attendance_report_parser_accepts_xlsx_and_rejects_malformed_csv():
    rows = [
        ["Student ID", "Student Name", "Academic Session", "Semester", "Subject Code", "Subject Name", "Classes Conducted", "Classes Attended", "Attendance Percentage"],
        ["LNCT-TEST-001", "Sample Learner", "2025-26", "5", "CS501", "Data Structures", "20", "18", "90"],
    ]
    def column(index):
        result = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result
    sheet_rows=[]
    for row_number, values in enumerate(rows, start=1):
        cells=[]
        for index, value in enumerate(values, start=1):
            ref=f"{column(index)}{row_number}"
            if row_number == 1 or index <= 6:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    xlsx=BytesIO()
    with ZipFile(xlsx,"w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Attendance" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    headers, parsed=parse_attendance_report("attendance.xlsx",xlsx.getvalue())
    assert "attendance_percentage" in headers and len(parsed)==1
    validated=validate_attendance_rows(parsed,student_code="LNCT-TEST-001",student_name="Sample Learner")
    assert validated["valid"][0]["attendance_percentage"]==90
    with pytest.raises(ValueError,match="Could not find a header row"):
        parse_attendance_report("broken.csv",b"garbage,not,a,recognized,report\nfoo,bar,baz\n")
