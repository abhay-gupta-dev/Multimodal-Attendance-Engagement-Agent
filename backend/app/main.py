import csv
import hashlib
import hmac
import ipaddress
import io
import json
import os
import re
import secrets
import tempfile
import uuid
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.database.session import Base, SessionLocal, engine, get_db
from app.models import AcademicTerm, ApplicationSetting, AttendanceCorrection, AttendanceImportJob, AttendanceRecord, AuthSession, AuditLog, AudioMetric, ClassEnrollment, Classroom, ClassSession, DataImportJob, EngagementMetric, ExternalAttendanceRecord, ExternalPortalConnection, Faculty, FacultySubjectAssignment, InstitutionProfile, Recommendation, Role, Student, Subject, UserAccount, VisionMetric
from app.schemas.schemas import AttendanceCreate, AttendanceOut, ClassCreate, ClassOut, SessionCreate, SessionOut, StudentCreate, StudentImportSummary, StudentOut, StudentUpdate
from app.services.analytics import engagement_indicator, recommendations
from app.services.demo import seed_demo
from app.services.roster_import import parse_roster, validate_rows
from app.services.attendance_report import MAX_FILE_BYTES, parse_attendance_report, validate_attendance_rows
from app.services.auth import clear_login_failures, hash_password, login_allowed, new_session_token, record_login_failure, retry_after, token_digest, verify_password
from app.audio.analysis import analyze_wav
from app.vision.analysis import analyze_image, analyze_video_path, vision_status

app = FastAPI(title="Multimodal Attendance & Engagement Agent", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[item.strip() for item in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


INSTITUTION_PROFILE_DEFAULTS = {
    "name": "Lakshmi Narain College of Technology",
    "abbreviation": "LNCT",
    "campus": "Bhopal",
    "address": "Kalchuri Nagar, P.O. Kolua, Raisen Road, Bhopal, Madhya Pradesh, India – 462022",
    "city": "Bhopal",
    "state": "Madhya Pradesh",
    "postal_code": "462022",
    "country": "India",
    "website": "https://lnct.ac.in/",
    "contact_page": "https://lnct.ac.in/contact/",
    "reception": "0755-6185300",
    "email": "info@lnct.ac.in",
    "college_code": "0103",
    "affiliation": "Rajiv Gandhi Proudyogiki Vishwavidyalaya (RGPV), Bhopal",
    "approval": "AICTE",
    "logo": "/lnct-logo.png",
    "favicon": "/favicon.png",
    "departments": [
        "Computer Science and Engineering", "Information Technology", "Electronics and Communication Engineering",
        "Electrical and Electronics Engineering", "Electrical Engineering", "Mechanical Engineering", "Civil Engineering",
    ],
    "programs": [
        {"level": "Undergraduate Engineering", "name": "Computer Science Engineering (CSE)"},
        {"level": "Undergraduate Engineering", "name": "Computer Science Engineering – Artificial Intelligence and Machine Learning (CSE-AIML)"},
        {"level": "Undergraduate Engineering", "name": "Mechanical Engineering"},
        {"level": "Undergraduate Engineering", "name": "Electrical and Electronics Engineering"},
        {"level": "Undergraduate Engineering", "name": "Civil Engineering"},
        {"level": "Undergraduate Engineering", "name": "Electronics and Communication Engineering"},
        {"level": "Undergraduate Engineering", "name": "Electrical Engineering"},
        {"level": "Undergraduate Engineering", "name": "Information Technology"},
        {"level": "Postgraduate Engineering", "name": "M.E./M.Tech – VLSI Design"},
        {"level": "Postgraduate Engineering", "name": "M.E./M.Tech – Thermal Engineering"},
        {"level": "Postgraduate Engineering", "name": "M.E./M.Tech – Power Electronics"},
        {"level": "Postgraduate Engineering", "name": "M.E./M.Tech – Computer Science and Engineering"},
        {"level": "Postgraduate Engineering", "name": "M.E./M.Tech – Construction Technology and Management"},
    ],
}
INSTITUTION_SOURCES = [
    {"label": "Official website", "url": "https://lnct.ac.in/"},
    {"label": "Contact", "url": "https://lnct.ac.in/contact/"},
    {"label": "About LNCT", "url": "https://lnct.ac.in/about-us/"},
    {"label": "LNCT Bhopal courses", "url": "https://lnct.ac.in/lnct-college/"},
    {"label": "LNCT Group profile (code and affiliation)", "url": "https://lnct.ac.in/lnct-group/"},
    {"label": "Academic calendar", "url": "https://lnct.ac.in/academic-calendar/"},
    {"label": "Course booklet 2026", "url": "https://lnct.ac.in/wp-content/uploads/2026/02/LNCT-Booklet-2026.pdf"},
    {"label": "CSE-AIML", "url": "https://lnct.ac.in/computer-science-engineering/"},
    {"label": "Official logo", "url": "https://lnct.ac.in/wp-content/uploads/2021/04/LNCT-Logo.png"},
    {"label": "Official favicon", "url": "https://lnct.ac.in/wp-content/uploads/2021/05/cropped-android-chrome-512x512-1-32x32.png"},
]
INSTITUTION_LAST_VERIFIED = "2026-10-29"


def save_default_institution_profile(db: Session) -> None:
    if db.get(InstitutionProfile, 1):
        return
    db.add(InstitutionProfile(id=1, profile_json=json.dumps(INSTITUTION_PROFILE_DEFAULTS), source_urls_json=json.dumps(INSTITUTION_SOURCES), last_verified=INSTITUTION_LAST_VERIFIED))
    db.commit()


ROLE_PERMISSIONS = {
    "super_admin": ["institution:manage", "accounts:manage", "students:manage", "classes:manage", "attendance:manage", "reports:read"],
    "department_admin": ["department:manage", "students:manage", "classes:manage", "attendance:manage", "reports:read"],
    "faculty": ["assigned_classes:read", "attendance:manage"],
    "student": ["self:read"],
}


def seed_roles(db: Session) -> None:
    for name, permissions in ROLE_PERMISSIONS.items():
        if not db.scalar(select(Role).where(Role.name == name)):
            db.add(Role(name=name, permissions_json=json.dumps(permissions)))
    db.commit()


@app.middleware("http")
async def local_only_api(request: Request, call_next):
    """Keep student and media APIs on loopback unless an explicit remote auth layer is added."""
    if request.url.path.startswith("/api/") or request.url.path.startswith("/docs") or request.url.path.startswith("/openapi.json"):
        host = request.client.host if request.client else ""
        try:
            is_local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_local = host == "testclient"
        if not is_local:
            return JSONResponse({"detail": "This local application accepts requests from this computer only."}, status_code=403)
        origin = request.headers.get("origin")
        allowed_origins = {item.strip().rstrip("/").lower() for item in settings.cors_origins.split(",")}
        if origin and origin.rstrip("/").lower() not in allowed_origins:
            return JSONResponse({"detail": "This local API only accepts requests from the configured frontend origin."}, status_code=403)
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        public_paths = {"/api/v1/health", "/api/v1/capabilities", "/api/v1/auth/status", "/api/v1/auth/bootstrap", "/api/v1/auth/login"}
        if path == "/api/v1/institution/profile" and request.method == "GET":
            return await call_next(request)
        if path in public_paths or path.startswith("/api/v1/imports/students/"):
            return await call_next(request)
        raw_token = request.cookies.get("lnct_session")
        if not raw_token:
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                raw_token = auth_header[7:].strip()
        if not raw_token:
            return JSONResponse({"detail": "Sign in is required."}, status_code=401)
        with SessionLocal() as auth_db:
            auth_session = auth_db.get(AuthSession, token_digest(raw_token))
            if not auth_session or auth_session.revoked_at or auth_session.expires_at <= datetime.utcnow():
                return JSONResponse({"detail": "Your session has expired. Sign in again."}, status_code=401)
            account = auth_db.get(UserAccount, auth_session.user_id)
            if not account or not account.active:
                return JSONResponse({"detail": "This account is inactive."}, status_code=403)
            role = auth_db.get(Role, account.role_id)
            request.state.auth_user_id = account.id
            request.state.auth_role = role.name if role else ""
            request.state.auth_student_id = account.student_id
        if request.state.auth_role == "student":
            student_paths = {"/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/me", "/api/v1/me/attendance"}
            if path not in student_paths and not path.startswith("/api/v1/accsoft/student/"):
                return JSONResponse({"detail": "Students can access only their own profile and attendance."}, status_code=403)
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    ensure_schema(engine)
    with next(get_db()) as db:
        seed_roles(db)
    if settings.demo_mode:
        with next(get_db()) as db:
            seed_demo(db)
    with next(get_db()) as db:
        save_default_institution_profile(db)


def ensure_schema(bind) -> None:
    """Create development tables and add compatible nullable fields to older SQLite databases."""
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "sqlite":
        columns = {column["name"] for column in inspect(bind).get_columns("vision_metrics")}
        student_columns = {column["name"] for column in inspect(bind).get_columns("students")}
        import_columns = {column["name"] for column in inspect(bind).get_columns("data_import_jobs")}
        with bind.begin() as connection:
            if "landmark_faces" not in columns:
                connection.execute(text("ALTER TABLE vision_metrics ADD COLUMN landmark_faces INTEGER NOT NULL DEFAULT 0"))
            if "avg_yaw_proxy" not in columns:
                connection.execute(text("ALTER TABLE vision_metrics ADD COLUMN avg_yaw_proxy FLOAT"))
            if "avg_pitch_proxy" not in columns:
                connection.execute(text("ALTER TABLE vision_metrics ADD COLUMN avg_pitch_proxy FLOAT"))
            additions = {
                "institution_code": "VARCHAR(30) NOT NULL DEFAULT 'LNCT'",
                "section": "VARCHAR(10)", "program": "VARCHAR(120)", "batch": "VARCHAR(20)", "study_year": "INTEGER",
                "semester": "INTEGER", "academic_session": "VARCHAR(20)",
                "enrollment_status": "VARCHAR(30) NOT NULL DEFAULT 'active'", "source_file": "VARCHAR(255)",
                "is_demo": "BOOLEAN NOT NULL DEFAULT 0",
            }
            for name, definition in additions.items():
                if name not in student_columns:
                    connection.execute(text(f"ALTER TABLE students ADD COLUMN {name} {definition}"))
            for name, definition in {"study_year": "INTEGER NOT NULL DEFAULT 2", "semester": "INTEGER NOT NULL DEFAULT 3", "program": "VARCHAR(120) NOT NULL DEFAULT 'B.Tech AIML'"}.items():
                if name not in import_columns:
                    connection.execute(text(f"ALTER TABLE data_import_jobs ADD COLUMN {name} {definition}"))
            # The existing demo seed uses STU- identifiers; keep those records segregated.
            connection.execute(text("UPDATE students SET is_demo=1, institution_code='DEMO', enrollment_status='demo' WHERE student_code LIKE 'STU-%'"))


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "attendance-agent"}


@app.get("/api/v1/capabilities")
def capabilities() -> dict:
    vision = vision_status()
    return {"camera_capture": True, "microphone_activity": True, "video_upload": True, "vision": vision, "identity_matching": {"available": False, "status": "No verified local identity model is installed; anonymous counting and manual attendance are enabled"}, "biometric_enrollment": {"available": False, "status": "Unavailable until a verified local matching model and secure template store are configured"}}


@app.get("/api/v1/institution/profile")
def institution_profile(db: Session = Depends(get_db)) -> dict:
    profile = db.get(InstitutionProfile, 1)
    if profile is None:
        save_default_institution_profile(db)
        profile = db.get(InstitutionProfile, 1)
    return {**json.loads(profile.profile_json), "last_verified": profile.last_verified, "sources": json.loads(profile.source_urls_json), "verification_status": "verified against official LNCT Bhopal pages"}


def require_roster_admin(x_roster_admin: str | None = Header(default=None, alias="X-Roster-Admin")) -> None:
    if not settings.roster_import_token:
        raise HTTPException(503, "Roster imports are disabled until ROSTER_IMPORT_TOKEN is configured in backend/.env")
    if not x_roster_admin or not hmac.compare_digest(x_roster_admin, settings.roster_import_token):
        raise HTTPException(403, "Roster administrator token is invalid")


def require_roles(*roles: str):
    def dependency(request: Request) -> int:
        current_role = getattr(request.state, "auth_role", None)
        if not current_role:
            raise HTTPException(401, "Sign in is required")
        if current_role not in roles:
            raise HTTPException(403, "Your account is not authorized for this action")
        return request.state.auth_user_id
    return dependency


def faculty_class_ids(db: Session, user_id: int) -> set[int]:
    account = db.get(UserAccount, user_id)
    if not account or not account.faculty_id:
        return set()
    return set(db.scalars(select(FacultySubjectAssignment.class_id).where(FacultySubjectAssignment.faculty_id == account.faculty_id)))


def ensure_class_access(class_id: int, db: Session, request: Request) -> None:
    role = getattr(request.state, "auth_role", None)
    if role == "faculty" and class_id not in faculty_class_ids(db, request.state.auth_user_id):
        raise HTTPException(403, "This class is not assigned to your faculty account")


def ensure_student_access(student_id: int, db: Session, request: Request) -> None:
    role = getattr(request.state, "auth_role", None)
    if role == "faculty":
        assigned = faculty_class_ids(db, request.state.auth_user_id)
        enrolled = db.scalar(select(ClassEnrollment.id).where(ClassEnrollment.student_id == student_id, ClassEnrollment.class_id.in_(assigned))) if assigned else None
        if not enrolled:
            raise HTTPException(403, "This student is not in one of your assigned classes")


class AuthInput(BaseModel):
    login_name: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=12, max_length=256)


class AccountCreate(AuthInput):
    role: str = Field(pattern="^(department_admin|faculty|student)$")
    student_id: int | None = None
    faculty_id: int | None = None


class FacultyCreate(BaseModel):
    staff_code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=160)
    department: str | None = Field(default=None, max_length=120)


class SubjectCreate(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=160)
    program: str = Field(min_length=2, max_length=120)
    semester: int = Field(ge=1, le=16)
    academic_session: str = Field(min_length=4, max_length=40)


class FacultyAssignmentCreate(BaseModel):
    faculty_id: int
    subject_id: int
    class_id: int


class ExternalAttendanceReview(BaseModel):
    decision: str = Field(pattern="^(verified|rejected)$")
    reason: str = Field(min_length=3, max_length=1000, pattern=r".*\S.*")


def user_public(db: Session, account: UserAccount) -> dict:
    role = db.get(Role, account.role_id)
    student = db.get(Student, account.student_id) if account.student_id else None
    faculty = db.get(Faculty, account.faculty_id) if account.faculty_id else None
    return {"id": account.id, "login_name": account.login_name, "role": role.name if role else "", "student_id": account.student_id, "student_code": student.student_code if student else None, "name": student.name if student else faculty.name if faculty else account.login_name}


def issue_session(db: Session, account: UserAccount, request: Request, response: Response) -> dict:
    token = new_session_token()
    expires = datetime.utcnow() + timedelta(hours=settings.auth_session_ttl_hours)
    db.add(AuthSession(token_hash=token_digest(token), user_id=account.id, expires_at=expires))
    db.commit()
    response.set_cookie("lnct_session", token, max_age=settings.auth_session_ttl_hours * 3600, httponly=True, secure=request.url.scheme == "https", samesite="strict", path="/api/v1")
    return user_public(db, account)


@app.get("/api/v1/auth/status")
def auth_status(db: Session = Depends(get_db)) -> dict:
    initialized = bool(db.scalar(select(func.count(UserAccount.id)).join(Role).where(Role.name == "super_admin")))
    token_state = "disabled" if initialized else "missing" if not settings.auth_bootstrap_token else "invalid-expiry"
    if not initialized and settings.auth_bootstrap_token:
        try:
            expiration = datetime.fromisoformat(settings.auth_bootstrap_token_expires_at.replace("Z", "+00:00"))
            token_state = "expired" if expiration.tzinfo is None or expiration <= datetime.now(timezone.utc) else "ready"
        except (ValueError, TypeError):
            token_state = "invalid-expiry"
    messages = {
        "missing": "First administrator setup requires AUTH_BOOTSTRAP_TOKEN and AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT in backend/.env.",
        "expired": "The administrator setup token has expired. Generate a new token and expiry, then restart the backend.",
        "invalid-expiry": "AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT must be a future ISO 8601 timestamp with a UTC offset.",
    }
    return {"initialized": initialized, "bootstrap_configured": token_state == "ready", "bootstrap_token_state": token_state, "bootstrap_message": messages.get(token_state)}


@app.post("/api/v1/auth/bootstrap")
def bootstrap_admin(payload: AuthInput, request: Request, response: Response, x_bootstrap_token: str | None = Header(default=None, alias="X-Admin-Bootstrap"), db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(func.count(UserAccount.id)).join(Role).where(Role.name == "super_admin")) or db.get(ApplicationSetting, "initial_super_admin_created"):
        raise HTTPException(409, "Initial administrator setup is already complete")
    expected = settings.auth_bootstrap_token
    if not expected:
        raise HTTPException(503, "First administrator setup is unavailable: configure AUTH_BOOTSTRAP_TOKEN and AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT in backend/.env, then restart the backend.")
    try:
        expiration = datetime.fromisoformat(settings.auth_bootstrap_token_expires_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise HTTPException(503, "AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT must be a future ISO 8601 timestamp with a UTC offset.")
    if expiration.tzinfo is None:
        raise HTTPException(503, "AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT must include a UTC offset, for example a timestamp ending in Z.")
    if expiration <= datetime.now(timezone.utc):
        raise HTTPException(410, "Administrator setup token has expired. Generate a new AUTH_BOOTSTRAP_TOKEN and expiry, then restart the backend.")
    if not x_bootstrap_token:
        raise HTTPException(400, "Administrator setup token is required.")
    if not hmac.compare_digest(x_bootstrap_token, expected):
        raise HTTPException(403, "Administrator setup token is invalid.")
    role = db.scalar(select(Role).where(Role.name == "super_admin"))
    account = UserAccount(login_name=payload.login_name.strip().casefold(), password_hash=hash_password(payload.password), role_id=role.id, active=True)
    db.add(ApplicationSetting(key="initial_super_admin_created", value="true"))
    db.add(account)
    try:
        db.flush()
        db.add(AuditLog(actor_user_id=account.id, action="admin.bootstrap", entity_type="user_account", entity_id=str(account.id), details_json="{}"))
        result = issue_session(db, account, request, response)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Initial administrator setup has already completed, or that login name is already in use") from exc
    return {"user": result}


@app.post("/api/v1/auth/login")
def login(payload: AuthInput, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    key = f"{request.client.host if request.client else 'local'}:{payload.login_name.strip().casefold()}"
    if not login_allowed(key):
        raise HTTPException(429, "Too many failed sign-in attempts. Try again later.", headers={"Retry-After": str(retry_after(key))})
    account = db.scalar(select(UserAccount).where(UserAccount.login_name == payload.login_name.strip().casefold()))
    password_hash = account.password_hash if account else hash_password("Unavailable account check password 123!")
    valid = verify_password(payload.password, password_hash)
    if not account or not account.active or not valid:
        record_login_failure(key)
        raise HTTPException(401, "Login name or password is incorrect")
    clear_login_failures(key)
    result = issue_session(db, account, request, response)
    db.add(AuditLog(actor_user_id=account.id, action="auth.login", entity_type="user_account", entity_id=str(account.id), details_json="{}"))
    db.commit()
    return {"user": result}


@app.get("/api/v1/auth/me")
def auth_me(request: Request, db: Session = Depends(get_db)) -> dict:
    account = db.get(UserAccount, request.state.auth_user_id)
    if not account:
        raise HTTPException(401, "Sign in is required")
    return {"user": user_public(db, account)}


@app.get("/api/v1/me")
def my_profile(request: Request, db: Session = Depends(get_db)) -> dict:
    account = db.get(UserAccount, request.state.auth_user_id)
    if not account:
        raise HTTPException(401, "Sign in is required")
    if account.student_id:
        student = get_student(account.student_id, db)
        return {"role": "student", "student": StudentOut.model_validate(student).model_dump()}
    if account.faculty_id:
        faculty = db.get(Faculty, account.faculty_id)
        return {"role": "faculty", "faculty": {"id": faculty.id, "staff_code": faculty.staff_code, "name": faculty.name, "email": faculty.email, "department": faculty.department}}
    return {"role": db.get(Role, account.role_id).name, "login_name": account.login_name}


@app.get("/api/v1/me/attendance")
def my_attendance(request: Request, db: Session = Depends(get_db), academic_session: str | None = None, semester: int | None = None, subject_class_id: int | None = None, start_date: date | None = None, end_date: date | None = None) -> dict:
    student_id = getattr(request.state, "auth_student_id", None)
    if not student_id or getattr(request.state, "auth_role", None) != "student":
        raise HTTPException(403, "A linked student account is required")
    classes_query = select(ClassEnrollment.class_id).where(ClassEnrollment.student_id == student_id)
    class_ids = list(db.scalars(classes_query))
    if not class_ids:
        return {"student_id": student_id, "sessions_conducted": 0, "verified_sessions": 0, "attendance_percentage": None, "message": "No verified attendance records are available for this period.", "subjects": [], "records": []}
    class_query = select(Classroom).where(Classroom.id.in_(class_ids))
    if academic_session:
        class_query = class_query.where(Classroom.name.contains(academic_session))
    if semester:
        class_query = class_query.where(Classroom.name.contains(f"Semester {semester}"))
    if subject_class_id:
        class_query = class_query.where(Classroom.id == subject_class_id)
    class_rows = list(db.scalars(class_query))
    selected_class_ids = [item.id for item in class_rows]
    sessions_query = select(ClassSession).where(ClassSession.class_id.in_(selected_class_ids), ClassSession.status == "completed", ClassSession.is_demo.is_(False)) if selected_class_ids else select(ClassSession).where(False)
    if start_date:
        sessions_query = sessions_query.where(ClassSession.ended_at >= datetime.combine(start_date, time.min))
    if end_date:
        sessions_query = sessions_query.where(ClassSession.ended_at < datetime.combine(end_date + timedelta(days=1), time.min))
    completed = list(db.scalars(sessions_query.order_by(ClassSession.ended_at.desc())))
    session_ids = [item.id for item in completed]
    records = list(db.scalars(select(AttendanceRecord).where(AttendanceRecord.student_id == student_id, AttendanceRecord.session_id.in_(session_ids)).order_by(AttendanceRecord.updated_at.desc()))) if session_ids else []
    verified = [row for row in records if row.status in ("present", "late", "absent")]
    attended = sum(row.status in ("present", "late") for row in verified)
    by_session = {row.session_id: row for row in records}
    class_by_id = {item.id: item for item in class_rows}
    subjects = []
    for class_id in selected_class_ids:
        subject_sessions = [item for item in completed if item.class_id == class_id]
        subject_records = [by_session[item.id] for item in subject_sessions if item.id in by_session and by_session[item.id].status in ("present", "late", "absent")]
        subject_attended = sum(item.status in ("present", "late") for item in subject_records)
        all_verified = len(subject_records) == len(subject_sessions) and bool(subject_sessions)
        subjects.append({"class_id": class_id, "subject": class_by_id[class_id].subject or class_by_id[class_id].name, "sessions_conducted": len(subject_sessions), "verified_sessions": len(subject_records), "classes_attended": subject_attended, "absences": sum(item.status == "absent" for item in subject_records), "attendance_percentage": round(subject_attended / len(subject_sessions) * 100, 1) if all_verified else None, "message": None if all_verified else "No verified attendance records are available for this period."})
    overall_complete = len(verified) == len(completed) and bool(completed)
    return {
        "student_id": student_id,
        "sessions_conducted": len(completed),
        "verified_sessions": len(verified),
        "attendance_percentage": round(attended / len(completed) * 100, 1) if overall_complete else None,
        "message": None if overall_complete else "No verified attendance records are available for this period.",
        "subjects": subjects,
        "records": [{"session_id": row.session_id, "class_id": next((s.class_id for s in completed if s.id == row.session_id), None), "subject": class_by_id.get(next((s.class_id for s in completed if s.id == row.session_id), None)).subject if class_by_id.get(next((s.class_id for s in completed if s.id == row.session_id), None)) else None, "session_title": next((s.title for s in completed if s.id == row.session_id), "Class session"), "status": row.status, "updated_at": row.updated_at.isoformat()} for row in records],
    }


ACCSoft_STUDENT_URL = "https://portal.lnct.ac.in/Accsoft2/studentlogin.aspx"


def linked_accsoft_student(request: Request, db: Session) -> tuple[UserAccount, Student]:
    if getattr(request.state, "auth_role", None) != "student":
        raise HTTPException(403, "A linked student account is required")
    user_id = getattr(request.state, "auth_user_id", None)
    student_id = getattr(request.state, "auth_student_id", None)
    account = db.get(UserAccount, user_id) if user_id else None
    student = db.get(Student, student_id) if student_id else None
    if not account or account.student_id != student_id or not student or student.is_demo:
        raise HTTPException(403, "A real LNCT student record linked to this account is required")
    return account, student


def attendance_report_preview(filename: str, contents: bytes, student: Student, db: Session) -> dict:
    try:
        _, rows = parse_attendance_report(filename, contents)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    validated = validate_attendance_rows(rows, student_code=student.student_code, student_name=student.name)
    fingerprints = [row["source_record_hash"] for row in validated["valid"]]
    existing: set[str] = set()
    for offset in range(0, len(fingerprints), 500):
        batch = fingerprints[offset:offset + 500]
        existing.update(db.scalars(select(ExternalAttendanceRecord.source_record_hash).where(ExternalAttendanceRecord.source_record_hash.in_(batch))))
    new_rows = [row for row in validated["valid"] if row["source_record_hash"] not in existing]
    duplicates = len(validated["valid"]) - len(new_rows) + sum(error["kind"] == "duplicate" for error in validated["errors"])
    errors = validated["errors"] + [
        {"row": row["row_number"], "kind": "duplicate", "reason": "This attendance row has already been imported"}
        for row in validated["valid"] if row["source_record_hash"] in existing
    ]
    return {"source_rows": validated["source_rows"], "valid": new_rows, "errors": errors, "duplicate_rows": duplicates, "rejected_rows": sum(error["kind"] == "invalid" for error in validated["errors"])}


def create_attendance_import_preview(account: UserAccount, student: Student, filename: str, contents: bytes, db: Session) -> dict:
    result = attendance_report_preview(filename, contents, student, db)
    token = str(uuid.uuid4())
    job = AttendanceImportJob(preview_token=token, student_id=student.id, requested_by_user_id=account.id, file_sha256=hashlib.sha256(contents).hexdigest(), source_file=filename or "attendance-report", source_rows=result["source_rows"], valid_rows=len(result["valid"]), duplicate_rows=result["duplicate_rows"], rejected_rows=result["rejected_rows"], errors_json=json.dumps(result["errors"]), status="previewed")
    db.add(job)
    db.commit()
    preview_rows = [{key: value.isoformat() if isinstance(value, date) else value for key, value in row.items() if key != "source_payload"} for row in result["valid"][:50]]
    return {"preview_token": token, "source_rows": result["source_rows"], "accepted_rows": len(result["valid"]), "duplicate_rows": result["duplicate_rows"], "rejected_rows": result["rejected_rows"], "errors": result["errors"][:200], "rows": preview_rows, "rows_truncated": len(result["valid"]) > 50, "expires_in_seconds": 3600, "notice": "Report contents are student-uploaded and remain pending staff review; the portal has not authenticated this file."}


def commit_attendance_import(preview_token: str, account: UserAccount, student: Student, filename: str, contents: bytes, db: Session) -> dict:
    job = db.scalar(select(AttendanceImportJob).where(AttendanceImportJob.preview_token == preview_token, AttendanceImportJob.student_id == student.id, AttendanceImportJob.requested_by_user_id == account.id))
    if not job:
        raise HTTPException(404, "Attendance preview was not found for this account")
    if job.status != "previewed":
        raise HTTPException(409, "This attendance preview has already been used")
    if job.created_at < datetime.utcnow() - timedelta(hours=1):
        job.status = "expired"
        db.commit()
        raise HTTPException(410, "Attendance preview expired. Upload and preview the report again.")
    if not hmac.compare_digest(hashlib.sha256(contents).hexdigest(), job.file_sha256):
        raise HTTPException(409, "The selected report differs from the preview. Preview the exact file you intend to import.")
    result = attendance_report_preview(filename, contents, student, db)
    new_records = [ExternalAttendanceRecord(student_id=student.id, import_job_id=job.id, source_record_hash=row["source_record_hash"], verification_status="pending_review", academic_session=row["academic_session"], semester=row["semester"], subject_code=row["subject_code"], subject_name=row["subject_name"], record_type=row["record_type"], classes_conducted=row["classes_conducted"], classes_attended=row["classes_attended"], absences=row["absences"], attendance_percentage=row["attendance_percentage"], attendance_date=row["attendance_date"], status=row["status"], source_filename=filename or job.source_file, source_row_number=row["row_number"], source_payload_json=json.dumps(row["source_payload"], ensure_ascii=False)) for row in result["valid"]]
    job.status = "imported"
    job.inserted_rows = len(new_records)
    job.duplicate_rows = result["duplicate_rows"]
    job.rejected_rows = result["rejected_rows"]
    job.errors_json = json.dumps(result["errors"])
    job.completed_at = datetime.utcnow()
    db.add_all(new_records)
    db.add(AuditLog(actor_user_id=account.id, action="accsoft.attendance.import", entity_type="attendance_import_job", entity_id=str(job.id), details_json=json.dumps({"inserted": len(new_records), "duplicates": job.duplicate_rows, "rejected": job.rejected_rows})))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "An attendance record was imported concurrently. Preview the report again; no existing record was overwritten.") from exc
    return {"status": job.status, "imported_rows": job.inserted_rows, "duplicate_rows": job.duplicate_rows, "rejected_rows": job.rejected_rows, "message": "Imported records are pending staff review."}


@app.get("/api/v1/accsoft/student/connection")
def accsoft_connection(request: Request, db: Session = Depends(get_db)) -> dict:
    account, student = linked_accsoft_student(request, db)
    connection = db.scalar(select(ExternalPortalConnection).where(ExternalPortalConnection.user_id == account.id, ExternalPortalConnection.student_id == student.id))
    return {"provider": "LNCT AccSoft", "portal_url": ACCSoft_STUDENT_URL, "integration_mode": "manual_portal_and_student_report_upload", "credentials_stored": False, "connection": {"status": connection.status, "last_opened_at": connection.last_opened_at.isoformat() if connection else None, "disconnected_at": connection.disconnected_at.isoformat() if connection and connection.disconnected_at else None} if connection else None}


@app.post("/api/v1/accsoft/student/connection/opened")
def accsoft_portal_opened(request: Request, db: Session = Depends(get_db)) -> dict:
    account, student = linked_accsoft_student(request, db)
    connection = db.scalar(select(ExternalPortalConnection).where(ExternalPortalConnection.user_id == account.id))
    if connection:
        connection.status = "manual_portal_only"
        connection.last_opened_at = datetime.utcnow()
        connection.disconnected_at = None
    else:
        connection = ExternalPortalConnection(user_id=account.id, student_id=student.id, portal_url=ACCSoft_STUDENT_URL)
        db.add(connection)
    db.flush()
    db.add(AuditLog(actor_user_id=account.id, action="accsoft.portal.opened", entity_type="external_portal_connection", entity_id=str(connection.id), details_json=json.dumps({"mode": "manual_handoff"})))
    db.commit()
    return {"status": "manual_portal_only", "message": "The official portal opened in a separate tab. Attendance is not fetched automatically."}


@app.post("/api/v1/accsoft/student/connection/disconnect")
def accsoft_disconnect(request: Request, db: Session = Depends(get_db)) -> dict:
    account, _ = linked_accsoft_student(request, db)
    connection = db.scalar(select(ExternalPortalConnection).where(ExternalPortalConnection.user_id == account.id))
    if connection:
        connection.status = "disconnected"
        connection.disconnected_at = datetime.utcnow()
        db.add(AuditLog(actor_user_id=account.id, action="accsoft.portal.disconnected", entity_type="external_portal_connection", entity_id=str(connection.id), details_json="{}"))
        db.commit()
    return {"status": "disconnected", "message": "Local portal handoff tracking was disconnected. Sign out from AccSoft separately if needed."}


@app.post("/api/v1/accsoft/student/attendance/preview")
async def accsoft_attendance_preview(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    account, student = linked_accsoft_student(request, db)
    filename = Path(file.filename or "").name[:255]
    contents = await file.read(MAX_FILE_BYTES + 1)
    await file.close()
    return create_attendance_import_preview(account, student, filename, contents, db)


@app.post("/api/v1/accsoft/student/attendance/{preview_token}/confirm")
async def accsoft_attendance_confirm(preview_token: str, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    account, student = linked_accsoft_student(request, db)
    filename = Path(file.filename or "").name[:255]
    contents = await file.read(MAX_FILE_BYTES + 1)
    await file.close()
    return commit_attendance_import(preview_token, account, student, filename, contents, db)


@app.get("/api/v1/accsoft/student/attendance")
def accsoft_student_attendance(request: Request, db: Session = Depends(get_db)) -> dict:
    _, student = linked_accsoft_student(request, db)
    records = list(db.scalars(select(ExternalAttendanceRecord).where(ExternalAttendanceRecord.student_id == student.id).order_by(ExternalAttendanceRecord.imported_at.desc(), ExternalAttendanceRecord.id.desc())))
    verified_count = sum(item.verification_status == "verified" for item in records)
    return {"records": [{"id": item.id, "verification_status": item.verification_status, "academic_session": item.academic_session, "semester": item.semester, "subject_code": item.subject_code, "subject_name": item.subject_name, "record_type": item.record_type, "classes_conducted": item.classes_conducted, "classes_attended": item.classes_attended, "absences": item.absences, "attendance_percentage": item.attendance_percentage, "attendance_date": item.attendance_date.isoformat() if item.attendance_date else None, "status": item.status, "source_filename": item.source_filename, "imported_at": item.imported_at.isoformat(), "verified_at": item.verified_at.isoformat() if item.verified_at else None, "review_note": item.review_note} for item in records], "verified_records": verified_count, "message": None if verified_count else "No verified attendance data has been imported or retrieved yet."}


@app.get("/api/v1/accsoft/student/imports")
def accsoft_student_imports(request: Request, db: Session = Depends(get_db)) -> list[dict]:
    account, student = linked_accsoft_student(request, db)
    jobs = list(db.scalars(select(AttendanceImportJob).where(AttendanceImportJob.student_id == student.id, AttendanceImportJob.requested_by_user_id == account.id).order_by(AttendanceImportJob.created_at.desc())))
    return [{"id": job.id, "source_file": job.source_file, "source_rows": job.source_rows, "inserted_rows": job.inserted_rows, "duplicate_rows": job.duplicate_rows, "rejected_rows": job.rejected_rows, "status": job.status, "created_at": job.created_at.isoformat(), "completed_at": job.completed_at.isoformat() if job.completed_at else None} for job in jobs]


@app.get("/api/v1/accsoft/admin/pending")
def accsoft_admin_pending(_: int = Depends(require_roles("super_admin")), db: Session = Depends(get_db)) -> list[dict]:
    records = list(db.scalars(select(ExternalAttendanceRecord).where(ExternalAttendanceRecord.verification_status == "pending_review").order_by(ExternalAttendanceRecord.imported_at.asc()).limit(200)))
    return [{"id": item.id, "student_code": db.get(Student, item.student_id).student_code, "student_name": db.get(Student, item.student_id).name, "academic_session": item.academic_session, "semester": item.semester, "subject_code": item.subject_code, "subject_name": item.subject_name, "classes_conducted": item.classes_conducted, "classes_attended": item.classes_attended, "absences": item.absences, "attendance_percentage": item.attendance_percentage, "attendance_date": item.attendance_date.isoformat() if item.attendance_date else None, "status": item.status, "source_filename": item.source_filename, "source_row_number": item.source_row_number, "imported_at": item.imported_at.isoformat()} for item in records]


@app.post("/api/v1/accsoft/admin/records/{record_id}/review")
def accsoft_review_record(record_id: int, payload: ExternalAttendanceReview, request: Request, actor_id: int = Depends(require_roles("super_admin")), db: Session = Depends(get_db)) -> dict:
    record = db.get(ExternalAttendanceRecord, record_id)
    if not record:
        raise HTTPException(404, "Attendance import record not found")
    if record.verification_status != "pending_review":
        raise HTTPException(409, "This attendance record has already been reviewed")
    record.verification_status = payload.decision
    record.reviewed_by_user_id = actor_id
    record.verified_at = datetime.utcnow() if payload.decision == "verified" else None
    record.review_note = payload.reason.strip()
    db.add(AuditLog(actor_user_id=actor_id, action=f"accsoft.attendance.{payload.decision}", entity_type="external_attendance_record", entity_id=str(record.id), details_json=json.dumps({"reason": payload.reason.strip()})))
    db.commit()
    return {"id": record.id, "verification_status": record.verification_status, "verified_at": record.verified_at.isoformat() if record.verified_at else None}


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    token = request.cookies.get("lnct_session")
    if token:
        session = db.get(AuthSession, token_digest(token))
        if session:
            session.revoked_at = datetime.utcnow()
            db.commit()
    response.delete_cookie("lnct_session", path="/api/v1", httponly=True, samesite="strict")
    return {"status": "signed-out"}


@app.post("/api/v1/auth/accounts", status_code=201)
def create_account(payload: AccountCreate, actor_id: int = Depends(require_roles("super_admin", "department_admin")), db: Session = Depends(get_db)) -> dict:
    actor = db.get(UserAccount, actor_id)
    role = db.scalar(select(Role).where(Role.name == payload.role))
    if not role:
        raise HTTPException(422, "Unknown account role")
    if actor and db.get(Role, actor.role_id).name == "department_admin" and payload.role == "department_admin":
        raise HTTPException(403, "Only a Super Admin can create another Department Admin")
    if payload.role == "student":
        student = db.get(Student, payload.student_id) if payload.student_id else None
        if not student or student.is_demo:
            raise HTTPException(422, "A real LNCT student record is required")
        if payload.faculty_id is not None:
            raise HTTPException(422, "Student accounts cannot link to faculty")
    elif payload.role == "faculty":
        faculty = db.get(Faculty, payload.faculty_id) if payload.faculty_id else None
        if not faculty:
            raise HTTPException(422, "Create or select a faculty record first")
    account = UserAccount(login_name=payload.login_name.strip().casefold(), password_hash=hash_password(payload.password), role_id=role.id, student_id=payload.student_id, faculty_id=payload.faculty_id, active=True)
    db.add(account)
    try:
        db.flush()
        db.add(AuditLog(actor_user_id=actor_id, action="auth.account.create", entity_type="user_account", entity_id=str(account.id), details_json=json.dumps({"role": payload.role})))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Login name or linked student/faculty is already assigned") from exc
    return user_public(db, account)


@app.get("/api/v1/auth/accounts")
def list_accounts(_: int = Depends(require_roles("super_admin", "department_admin")), db: Session = Depends(get_db)) -> list[dict]:
    return [user_public(db, account) | {"active": account.active, "created_at": account.created_at.isoformat()} for account in db.scalars(select(UserAccount).order_by(UserAccount.created_at.desc()))]


@app.post("/api/v1/faculty", status_code=201)
def create_faculty(payload: FacultyCreate, _: int = Depends(require_roles("super_admin", "department_admin")), db: Session = Depends(get_db)) -> dict:
    faculty = Faculty(**payload.model_dump())
    db.add(faculty)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Staff code or email is already in use") from exc
    db.refresh(faculty)
    return {"id": faculty.id, **payload.model_dump(), "active": faculty.active}


@app.get("/api/v1/faculty")
def list_faculty(_: int = Depends(require_roles("super_admin", "department_admin", "faculty")), db: Session = Depends(get_db)) -> list[dict]:
    return [{"id": item.id, "staff_code": item.staff_code, "name": item.name, "email": item.email, "department": item.department, "active": item.active} for item in db.scalars(select(Faculty).order_by(Faculty.name))]


@app.post("/api/v1/subjects", status_code=201)
def create_subject(payload: SubjectCreate, _: int = Depends(require_roles("super_admin", "department_admin")), db: Session = Depends(get_db)) -> dict:
    subject = Subject(**payload.model_dump())
    db.add(subject)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Subject code already exists") from exc
    db.refresh(subject)
    return {"id": subject.id, **payload.model_dump(), "active": subject.active}


@app.get("/api/v1/subjects")
def list_subjects(_: int = Depends(require_roles("super_admin", "department_admin", "faculty")), db: Session = Depends(get_db)) -> list[dict]:
    return [{"id": item.id, "code": item.code, "name": item.name, "program": item.program, "semester": item.semester, "academic_session": item.academic_session, "active": item.active} for item in db.scalars(select(Subject).order_by(Subject.program, Subject.semester, Subject.code))]


@app.post("/api/v1/faculty-subject-assignments", status_code=201)
def create_faculty_assignment(payload: FacultyAssignmentCreate, actor_id: int = Depends(require_roles("super_admin", "department_admin")), db: Session = Depends(get_db)) -> dict:
    assignment = FacultySubjectAssignment(**payload.model_dump())
    db.add(assignment)
    try:
        db.flush()
        db.add(AuditLog(actor_user_id=actor_id, action="faculty.subject.assign", entity_type="faculty_subject_assignment", entity_id=str(assignment.id), details_json=json.dumps(payload.model_dump())))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Faculty, subject, or class assignment is invalid or already exists") from exc
    return {"id": assignment.id, **payload.model_dump()}


class InstitutionProfileUpdate(BaseModel):
    profile: dict
    sources: list[dict]
    last_verified: str = Field(pattern=r"^20\d\d-\d\d-\d\d$")


@app.put("/api/v1/institution/profile")
def update_institution_profile(payload: InstitutionProfileUpdate, actor_id: int = Depends(require_roles("super_admin")), db: Session = Depends(get_db)) -> dict:
    current = db.get(InstitutionProfile, 1)
    if current is None:
        save_default_institution_profile(db)
        current = db.get(InstitutionProfile, 1)
    current.profile_json = json.dumps(payload.profile, ensure_ascii=False)
    current.source_urls_json = json.dumps(payload.sources, ensure_ascii=False)
    current.last_verified = payload.last_verified
    db.add(AuditLog(actor_user_id=actor_id, action="institution.profile.update", entity_type="institution_profile", entity_id="1", details_json=json.dumps({"last_verified": payload.last_verified})))
    db.commit()
    return institution_profile(db)


@app.get("/api/v1/students", response_model=list[StudentOut])
def students(request: Request, db: Session = Depends(get_db), search: str | None = None, section: str | None = None, _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> list[Student]:
    query = select(Student).where(Student.is_demo.is_(False), Student.institution_code == "LNCT").order_by(Student.section, Student.student_code)
    if search:
        query = query.where(Student.name.ilike(f"%{search}%") | Student.student_code.ilike(f"%{search}%"))
    if section:
        query = query.where(Student.section == section.upper())
    if getattr(request.state, "auth_role", None) == "faculty":
        assigned = faculty_class_ids(db, request.state.auth_user_id)
        if not assigned:
            return []
        query = query.where(Student.id.in_(select(ClassEnrollment.student_id).where(ClassEnrollment.class_id.in_(assigned))))
    return list(db.scalars(query))


@app.post("/api/v1/students", response_model=StudentOut, status_code=201)
def create_student(payload: StudentCreate, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin"))) -> Student:
    student = Student(**payload.model_dump())
    db.add(student)
    try:
        db.commit()
        db.refresh(student)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Student code already exists") from exc
    return student


def get_student(student_id: int, db: Session) -> Student:
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(404, "Student not found")
    return student


@app.get("/api/v1/students/{student_id}", response_model=StudentOut)
def student_detail(student_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> Student:
    ensure_student_access(student_id, db, request)
    return get_student(student_id, db)


@app.put("/api/v1/students/{student_id}", response_model=StudentOut)
def update_student(student_id: int, payload: StudentUpdate, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin"))) -> Student:
    student = get_student(student_id, db)
    for key, value in payload.model_dump().items():
        setattr(student, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Student code already exists") from exc
    db.refresh(student)
    return student


@app.delete("/api/v1/students/{student_id}", status_code=204)
def delete_student(student_id: int, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin"))) -> None:
    student = get_student(student_id, db)
    student.active = False
    db.commit()


@app.get("/api/v1/classes", response_model=list[ClassOut])
def classes(request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> list[Classroom]:
    query = select(Classroom).order_by(Classroom.name)
    if getattr(request.state, "auth_role", None) == "faculty":
        assigned = faculty_class_ids(db, request.state.auth_user_id)
        if not assigned:
            return []
        query = query.where(Classroom.id.in_(assigned))
    return list(db.scalars(query))


@app.get("/api/v1/classes/{class_id}")
def get_class(class_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> dict:
    ensure_class_access(class_id, db, request)
    classroom = db.get(Classroom, class_id)
    if not classroom:
        raise HTTPException(404, "Class not found")
    roster = db.scalars(select(Student).join(ClassEnrollment).where(ClassEnrollment.class_id == class_id).order_by(Student.name)).all()
    return {**ClassOut.model_validate(classroom).model_dump(), "students": [StudentOut.model_validate(item).model_dump() for item in roster]}


@app.post("/api/v1/classes", response_model=ClassOut, status_code=201)
def create_class(payload: ClassCreate, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin"))) -> Classroom:
    classroom = Classroom(**payload.model_dump())
    db.add(classroom)
    try:
        db.commit()
        db.refresh(classroom)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Class name already exists") from exc
    return classroom


@app.put("/api/v1/classes/{class_id}", response_model=ClassOut)
def update_class(class_id: int, payload: ClassCreate, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin"))) -> Classroom:
    classroom = db.get(Classroom, class_id)
    if not classroom:
        raise HTTPException(404, "Class not found")
    for key, value in payload.model_dump().items(): setattr(classroom, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Class name already exists") from exc
    db.refresh(classroom)
    return classroom


@app.delete("/api/v1/classes/{class_id}", status_code=204)
def delete_class(class_id: int, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin"))) -> None:
    classroom = db.get(Classroom, class_id)
    if not classroom: raise HTTPException(404, "Class not found")
    db.delete(classroom)
    db.commit()


@app.post("/api/v1/classes/{class_id}/students/{student_id}", status_code=201)
def enroll(class_id: int, student_id: int, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin"))) -> dict[str, str]:
    if not db.get(Classroom, class_id) or not db.get(Student, student_id):
        raise HTTPException(404, "Class or student not found")
    db.add(ClassEnrollment(class_id=class_id, student_id=student_id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Student is already enrolled") from exc
    return {"status": "enrolled"}


@app.delete("/api/v1/classes/{class_id}/students/{student_id}", status_code=204)
def unenroll(class_id: int, student_id: int, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin"))) -> None:
    enrollment = db.scalar(select(ClassEnrollment).where(ClassEnrollment.class_id == class_id, ClassEnrollment.student_id == student_id))
    if not enrollment: raise HTTPException(404, "Enrollment not found")
    db.delete(enrollment)
    db.commit()


@app.get("/api/v1/sessions", response_model=list[SessionOut])
def sessions(request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> list[ClassSession]:
    query = select(ClassSession).order_by(ClassSession.created_at.desc())
    if getattr(request.state, "auth_role", None) == "faculty":
        assigned = faculty_class_ids(db, request.state.auth_user_id)
        if not assigned:
            return []
        query = query.where(ClassSession.class_id.in_(assigned))
    return list(db.scalars(query))


@app.post("/api/v1/sessions", response_model=SessionOut, status_code=201)
def create_session(payload: SessionCreate, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> ClassSession:
    if payload.class_id and not db.get(Classroom, payload.class_id):
        raise HTTPException(404, "Class not found")
    if payload.class_id:
        ensure_class_access(payload.class_id, db, request)
    session = ClassSession(**payload.model_dump(), is_demo=payload.source == "demo")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(session_id: int, db: Session) -> ClassSession:
    session = db.get(ClassSession, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session


@app.post("/api/v1/sessions/{session_id}/start", response_model=SessionOut)
def start_session(session_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> ClassSession:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    if session.status == "completed":
        raise HTTPException(409, "Completed sessions cannot be restarted")
    session.status = "active"
    session.started_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    return session


@app.post("/api/v1/sessions/{session_id}/stop", response_model=SessionOut)
def stop_session(session_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> ClassSession:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    session.status = "completed"
    session.ended_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    return session


@app.get("/api/v1/sessions/{session_id}/attendance", response_model=list[AttendanceOut])
def session_attendance(session_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> list[AttendanceRecord]:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    return list(db.scalars(select(AttendanceRecord).where(AttendanceRecord.session_id == session_id)))


@app.get("/api/v1/students/{student_id}/attendance", response_model=list[AttendanceOut])
def student_attendance(student_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> list[AttendanceRecord]:
    ensure_student_access(student_id, db, request)
    get_student(student_id, db)
    return list(db.scalars(select(AttendanceRecord).where(AttendanceRecord.student_id == student_id).order_by(AttendanceRecord.updated_at.desc())))


@app.post("/api/v1/sessions/{session_id}/attendance", response_model=AttendanceOut, status_code=201)
def add_attendance(session_id: int, payload: AttendanceCreate, request: Request, db: Session = Depends(get_db), actor_id: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> AttendanceRecord:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    if not db.get(Student, payload.student_id):
        raise HTTPException(404, "Student not found")
    record = AttendanceRecord(session_id=session_id, source="manual", **payload.model_dump())
    db.add(record)
    try:
        db.flush()
        db.add(AuditLog(actor_user_id=actor_id, action="attendance.record", entity_type="attendance_record", entity_id=str(record.id), details_json=json.dumps({"status": record.status, "student_id": record.student_id, "session_id": session_id})))
        db.commit()
        db.refresh(record)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Attendance already exists for this student and session") from exc
    return record


@app.put("/api/v1/attendance/{attendance_id}", response_model=AttendanceOut)
def update_attendance(attendance_id: int, payload: AttendanceCreate, request: Request, db: Session = Depends(get_db), actor_id: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> AttendanceRecord:
    record = db.get(AttendanceRecord, attendance_id)
    if not record:
        raise HTTPException(404, "Attendance record not found")
    if not payload.note or len(payload.note.strip()) < 3:
        raise HTTPException(422, "A correction reason of at least 3 characters is required")
    session = get_session(record.session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    old_status = record.status
    for key, value in payload.model_dump().items():
        setattr(record, key, value)
    record.source = "manual-correction"
    db.add(AttendanceCorrection(attendance_id=record.id, actor_user_id=actor_id, old_status=old_status, new_status=record.status, reason=payload.note.strip()))
    db.add(AuditLog(actor_user_id=actor_id, action="attendance.correct", entity_type="attendance_record", entity_id=str(record.id), details_json=json.dumps({"from": old_status, "to": record.status})))
    db.commit()
    db.refresh(record)
    return record


@app.get("/api/v1/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> dict:
    student_count = db.scalar(select(func.count(Student.id)).where(Student.active.is_(True), Student.is_demo.is_(False), Student.institution_code == "LNCT")) or 0
    session_count = db.scalar(select(func.count(ClassSession.id)).where(ClassSession.status == "completed", ClassSession.is_demo.is_(False))) or 0
    recorded_present = db.scalar(select(func.count(AttendanceRecord.id)).join(ClassSession).where(ClassSession.is_demo.is_(False), AttendanceRecord.status.in_(["present", "late"]))) or 0
    recorded_total = db.scalar(select(func.count(AttendanceRecord.id)).join(ClassSession).where(ClassSession.is_demo.is_(False))) or 0
    demo_present = db.scalar(select(func.count(AttendanceRecord.id)).join(ClassSession).where(ClassSession.is_demo.is_(True), AttendanceRecord.status.in_(["present", "late"]))) or 0
    demo_total = db.scalar(select(func.count(AttendanceRecord.id)).join(ClassSession).where(ClassSession.is_demo.is_(True))) or 0
    recorded_average = db.scalar(select(func.avg(EngagementMetric.indicator)).join(ClassSession).where(ClassSession.is_demo.is_(False)))
    demo_average = db.scalar(select(func.avg(EngagementMetric.indicator)).join(ClassSession).where(ClassSession.is_demo.is_(True)))
    indicator = recorded_average if recorded_average is not None else demo_average
    mode = "mixed" if recorded_average is not None and demo_average is not None else "recorded" if recorded_average is not None else "demo" if demo_average is not None else "empty"
    attendance_rate = recorded_present / recorded_total * 100 if recorded_total else None
    sections = dict(db.execute(select(Student.section, func.count(Student.id)).where(Student.active.is_(True), Student.is_demo.is_(False), Student.institution_code == "LNCT", Student.section.is_not(None)).group_by(Student.section)).all())
    return {"students": student_count, "sections": sections, "classes_conducted": session_count, "active_sessions": db.scalar(select(func.count(ClassSession.id)).where(ClassSession.status == "active", ClassSession.is_demo.is_(False))) or 0, "attendance_percentage": round(attendance_rate, 1) if attendance_rate is not None else None, "verified_attendance_available": recorded_total > 0, "average_indicator": round(recorded_average, 1) if recorded_average is not None else 0, "indicator_data_mode": "recorded" if recorded_average is not None else "none", "data_mode": "recorded" if recorded_average is not None else "empty"}


@app.get("/api/v1/dashboard/trends")
def dashboard_trends(db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> dict:
    rows = db.execute(select(EngagementMetric, ClassSession.is_demo).join(ClassSession, EngagementMetric.session_id == ClassSession.id).order_by(EngagementMetric.recorded_at)).all()
    def point(metric: EngagementMetric) -> dict:
        return {"label": metric.recorded_at.strftime("%b %d"), "value": metric.indicator}
    return {"engagement": [point(metric) for metric, _ in rows], "demo_engagement": [point(metric) for metric, is_demo in rows if is_demo], "recorded_engagement": [point(metric) for metric, is_demo in rows if not is_demo], "audio": [{"label": metric.recorded_at.strftime("%b %d"), "value": metric.audio_activity} for metric, _ in rows if metric.audio_activity is not None]}


@app.get("/api/v1/sessions/{session_id}/analytics")
def session_analytics(session_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> dict:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    metrics = list(db.scalars(select(EngagementMetric).where(EngagementMetric.session_id == session_id).order_by(EngagementMetric.recorded_at)))
    if not metrics:
        return {"indicator": None, "data_quality": "no-data", "metrics": [], "vision": [], "audio": [], "recommendations": []}
    latest = metrics[-1]
    visions = list(db.scalars(select(VisionMetric).where(VisionMetric.session_id == session_id)))
    audios = list(db.scalars(select(AudioMetric).where(AudioMetric.session_id == session_id)))
    recs = list(db.scalars(select(Recommendation).where(Recommendation.session_id == session_id)))
    return {"indicator": latest.indicator, "data_quality": latest.data_quality, "metrics": [{"recorded_at": item.recorded_at, "indicator": item.indicator, "face_visibility": item.face_visibility, "audio_activity": item.audio_activity} for item in metrics], "vision": [VisionOut.model_validate(item).model_dump() for item in visions], "audio": [AudioOut.model_validate(item).model_dump() for item in audios], "recommendations": [RecommendationOut.model_validate(item).model_dump() for item in recs]}


@app.get("/api/v1/students/{student_id}/analytics")
def student_analytics(student_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> dict:
    ensure_student_access(student_id, db, request)
    student = get_student(student_id, db)
    records = list(db.scalars(select(AttendanceRecord).where(AttendanceRecord.student_id == student_id)))
    total = len(records)
    attended = sum(record.status in ("present", "late") for record in records)
    return {"student": StudentOut.model_validate(student).model_dump(), "sessions_attended": attended, "sessions_recorded": total, "attendance_percentage": round(attended / total * 100, 1) if total else None, "total_duration_seconds": sum(record.duration_seconds for record in records if record.status in ("present", "late")), "attendance": [AttendanceOut.model_validate(record).model_dump() for record in records]}


class VisionOut(BaseModel):
    id: int
    session_id: int
    sampled_frames: int
    face_detections: int
    landmark_faces: int
    avg_yaw_proxy: float | None
    avg_pitch_proxy: float | None
    data_quality: str
    model_config = ConfigDict(from_attributes=True)


class AudioOut(BaseModel):
    id: int
    session_id: int
    duration_seconds: float
    activity_percentage: float
    data_quality: str
    model_config = ConfigDict(from_attributes=True)


class RecommendationOut(BaseModel):
    id: int
    session_id: int | None
    title: str
    explanation: str
    action: str
    evidence: str
    confidence: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AudioActivityIn(BaseModel):
    duration_seconds: float = Field(gt=0, le=10)
    activity_percentage: float = Field(ge=0, le=100)


@app.get("/api/v1/sessions/{session_id}/recommendations", response_model=list[RecommendationOut])
def session_recommendations(session_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> list[Recommendation]:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    return list(db.scalars(select(Recommendation).where(Recommendation.session_id == session_id).order_by(Recommendation.created_at.desc())))


@app.post("/api/v1/sessions/{session_id}/recommendations/generate", response_model=list[RecommendationOut])
def generate_recommendations(session_id: int, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> list[Recommendation]:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    metric = db.scalar(select(EngagementMetric).where(EngagementMetric.session_id == session_id).order_by(EngagementMetric.recorded_at.desc()))
    if not metric: return []
    generated = recommendations(metric.indicator, metric.audio_activity, metric.data_quality)
    db.query(Recommendation).filter(Recommendation.session_id == session_id).delete(synchronize_session=False)
    saved = []
    for item in generated:
        record = Recommendation(session_id=session_id, **item)
        db.add(record); saved.append(record)
    db.commit()
    for item in saved: db.refresh(item)
    return saved


async def read_upload(file: UploadFile, allowed: set[str]) -> bytes:
    suffix = (file.filename or "").rsplit(".", 1)[-1].lower()
    if suffix not in allowed: raise HTTPException(415, f"Supported file types: {', '.join(sorted(allowed))}")
    content = await file.read(settings.upload_max_mb * 1024 * 1024 + 1)
    if len(content) > settings.upload_max_mb * 1024 * 1024: raise HTTPException(413, "Uploaded file exceeds the configured size limit")
    if not content: raise HTTPException(400, "Uploaded file is empty")
    return content


async def read_roster_upload(file: UploadFile) -> tuple[str, bytes, list[tuple[int, dict]], dict]:
    filename = Path(file.filename or "roster").name
    if filename.rsplit(".", 1)[-1].lower() not in {"xlsx", "csv"}:
        raise HTTPException(415, "Roster imports accept .xlsx or .csv files")
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "Roster files must be 20 MB or smaller")
    try:
        headers, rows = parse_roster(filename, content)
        validated = validate_rows(rows)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, str(exc)) from exc
    return filename, content, rows, validated


def roster_plan(db: Session, validated: dict) -> dict:
    errors = list(validated["errors"])
    codes = [str(item["student_code"]).casefold() for item in validated["valid"]]
    existing = db.scalars(select(Student).where(func.lower(Student.student_code).in_(codes))).all() if codes else []
    existing_by_code = {row.student_code.casefold(): row for row in existing}
    new_rows, already = [], 0
    for item in validated["valid"]:
        current = existing_by_code.get(str(item["student_code"]).casefold())
        if not current:
            new_rows.append(item)
            continue
        matches = (
            current.institution_code == "LNCT"
            and current.name == item["name"]
            and current.section == item["section"]
            and current.source_file == "AIML 3 Sem Roll List July 25.xlsx"
            and current.program in {"B.Tech AIML", "B.Tech CSE-AIML"}
        )
        if matches:
            already += 1
        else:
            errors.append({"row": item["row"], "reason": "Enrollment number already exists with different or unverifiable student details", "kind": "conflict"})
    return {"new": new_rows, "already": already, "errors": errors, "invalid": sum(error["kind"] == "invalid" for error in errors), "conflicts": sum(error["kind"] == "conflict" for error in errors)}


def import_summary(job: DataImportJob, *, new_to_import: int = 0, preview: list[dict] | None = None) -> dict:
    return {"import_id": job.id, "preview_token": job.preview_token, "source_file": job.source_file, "source_rows": job.source_rows, "valid_rows": job.valid_rows, "newly_imported": job.newly_imported, "new_to_import": new_to_import, "already_existing": job.already_existing, "invalid_rows": job.invalid_rows, "conflicting_rows": job.conflicting_rows, "section_counts": job.section_counts if hasattr(job, "section_counts") else {}, "errors": json.loads(job.errors_json or "[]"), "preview": preview or []}


@app.post("/api/v1/imports/students/preview", response_model=StudentImportSummary)
async def preview_student_import(file: UploadFile = File(...), academic_session: str = Form("2025-26"), study_year: int = Form(2), semester: int = Form(3), _: None = Depends(require_roster_admin), db: Session = Depends(get_db)) -> dict:
    if not re.fullmatch(r"20\d\d-\d\d", academic_session) or not 1 <= study_year <= 8 or not 1 <= semester <= 16:
        raise HTTPException(422, "Academic session, year, or semester is invalid")
    filename, content, rows, validated = await read_roster_upload(file)
    plan = roster_plan(db, validated)
    errors = plan["errors"][:200]
    job = DataImportJob(preview_token=str(uuid.uuid4()), file_sha256=hashlib.sha256(content).hexdigest(), source_file=filename, source_rows=len(rows), valid_rows=len(validated["valid"]), already_existing=plan["already"], invalid_rows=plan["invalid"], conflicting_rows=plan["conflicts"], academic_session=academic_session, program="B.Tech AIML", study_year=study_year, semester=semester, status="previewed", errors_json=json.dumps(errors))
    db.add(job); db.commit(); db.refresh(job)
    sample = [{"student_code": item["student_code"], "name": item["name"], "section": item["section"]} for item in plan["new"][:10]]
    return {"import_id": job.id, "preview_token": job.preview_token, "source_file": filename, "source_rows": len(rows), "valid_rows": len(validated["valid"]), "newly_imported": 0, "new_to_import": len(plan["new"]), "already_existing": plan["already"], "invalid_rows": plan["invalid"], "conflicting_rows": plan["conflicts"], "section_counts": validated["section_counts"], "errors": errors, "preview": sample}


@app.post("/api/v1/imports/students/{preview_token}/confirm")
async def confirm_student_import(preview_token: str, file: UploadFile = File(...), _: None = Depends(require_roster_admin), db: Session = Depends(get_db)) -> dict:
    job = db.scalar(select(DataImportJob).where(DataImportJob.preview_token == preview_token))
    if not job: raise HTTPException(404, "Import preview not found")
    if job.status != "previewed": raise HTTPException(409, "This import preview has already been completed")
    if datetime.utcnow() - job.created_at > timedelta(hours=1): raise HTTPException(410, "Import preview expired; create a new preview")
    filename, content, rows, validated = await read_roster_upload(file)
    if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), job.file_sha256): raise HTTPException(409, "The selected file differs from the reviewed preview")
    plan = roster_plan(db, validated)
    try:
        term = db.scalar(select(AcademicTerm).where(AcademicTerm.institution_code == "LNCT", AcademicTerm.program == job.program, AcademicTerm.academic_session == job.academic_session, AcademicTerm.semester == job.semester))
        if not term:
            term = AcademicTerm(institution_code="LNCT", program=job.program, academic_session=job.academic_session, year=job.study_year, semester=job.semester, verification_status="source-roster-unverified", source_file=filename)
            db.add(term); db.flush()
        class_by_section = {}
        for item in plan["new"]:
            section = item["section"]
            if section not in class_by_section:
                class_name = f"{job.program} · Semester {job.semester} · Section {section} · {job.academic_session}"
                classroom = db.scalar(select(Classroom).where(Classroom.name == class_name))
                if not classroom:
                    classroom = Classroom(name=class_name, subject=job.program, description="Imported roster cohort; current enrollment and teaching assignments remain unverified.")
                    db.add(classroom); db.flush()
                class_by_section[section] = classroom
            student = Student(student_code=item["student_code"], name=item["name"], class_name=class_by_section[section].name, institution_code="LNCT", section=section, program=job.program, study_year=job.study_year, semester=job.semester, academic_session=job.academic_session, enrollment_status="source-roster-unverified", source_file=filename, is_demo=False, active=True)
            db.add(student); db.flush()
            db.add(ClassEnrollment(class_id=class_by_section[section].id, student_id=student.id))
        job.source_rows = len(rows); job.valid_rows = len(validated["valid"]); job.newly_imported = len(plan["new"]); job.already_existing = plan["already"]; job.invalid_rows = plan["invalid"]; job.conflicting_rows = plan["conflicts"]; job.errors_json = json.dumps(plan["errors"][:200]); job.status = "imported"; job.completed_at = datetime.utcnow()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Roster changed during import; preview again to review the current records") from exc
    db.refresh(job)
    return {"import_id": job.id, "source_file": job.source_file, "source_rows": job.source_rows, "valid_rows": job.valid_rows, "newly_imported": job.newly_imported, "already_existing": job.already_existing, "invalid_rows": job.invalid_rows, "conflicting_rows": job.conflicting_rows, "academic_session": job.academic_session, "program": job.program, "status": job.status, "notice": "No attendance records were created. Imported enrollment status is source-roster-unverified."}


@app.get("/api/v1/imports/students/history")
def student_import_history(_: None = Depends(require_roster_admin), db: Session = Depends(get_db)) -> list[dict]:
    jobs = db.scalars(select(DataImportJob).order_by(DataImportJob.created_at.desc()).limit(50)).all()
    return [{"import_id": job.id, "source_file": job.source_file, "source_rows": job.source_rows, "newly_imported": job.newly_imported, "already_existing": job.already_existing, "invalid_rows": job.invalid_rows, "conflicting_rows": job.conflicting_rows, "academic_session": job.academic_session, "status": job.status, "created_at": job.created_at, "completed_at": job.completed_at} for job in jobs]


async def save_video_upload(file: UploadFile) -> str:
    suffix = (file.filename or "").rsplit(".", 1)[-1].lower()
    allowed = {"mp4", "avi", "mov", "mkv", "webm"}
    if suffix not in allowed:
        raise HTTPException(415, f"Supported video extensions: {', '.join(sorted(allowed))}")
    descriptor, path = tempfile.mkstemp(suffix=f".{suffix}")
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.upload_max_mb * 1024 * 1024:
                    raise HTTPException(413, "Uploaded file exceeds the configured size limit")
                destination.write(chunk)
        if total == 0:
            raise HTTPException(400, "Uploaded file is empty")
        return path
    except Exception:
        try: os.unlink(path)
        except OSError: pass
        raise


@app.post("/api/v1/sessions/{session_id}/media/video")
async def analyze_session_video(session_id: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> dict:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    path = await save_video_upload(file)
    try:
        result = await run_in_threadpool(analyze_video_path, path)
        metric = VisionMetric(session_id=session_id, **{key: value for key, value in result.items() if key in {"sampled_frames", "face_detections", "landmark_faces", "avg_yaw_proxy", "avg_pitch_proxy", "data_quality"}})
        db.add(metric); db.commit(); db.refresh(metric)
        return {**result, "landmark_status": vision_status()["landmark_status"], "notice": "Anonymous face counts only; approximate visual landmarks are not attention or identity measurements. No attendance was inferred."}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        try: os.unlink(path)
        except OSError: pass


@app.post("/api/v1/sessions/{session_id}/media/frame")
async def analyze_live_frame(session_id: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> dict:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    if session.status != "active": raise HTTPException(409, "Start the session before sending camera frames")
    content = await read_upload(file, {"jpg", "jpeg", "png"})
    try: result = await run_in_threadpool(analyze_image, content)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc: raise HTTPException(503, str(exc)) from exc
    metric = VisionMetric(session_id=session_id, sampled_frames=1, **{key: value for key, value in result.items() if key in {"face_detections", "landmark_faces", "avg_yaw_proxy", "avg_pitch_proxy"}}, data_quality="measured-anonymous")
    db.add(metric); db.commit()
    return {"face_detections": result["face_detections"], "landmark_faces": result["landmark_faces"], "avg_yaw_proxy": result["avg_yaw_proxy"], "avg_pitch_proxy": result["avg_pitch_proxy"], "landmark_status": result["landmark_status"], "recorded_at": datetime.utcnow(), "notice": "Frame processed locally; raw image was not stored. Pose proxies are approximate visual indicators only."}


@app.post("/api/v1/sessions/{session_id}/media/audio-activity")
def save_live_audio_activity(session_id: int, payload: AudioActivityIn, request: Request, db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> dict:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    if session.status != "active": raise HTTPException(409, "Start the session before sending microphone activity")
    metric = AudioMetric(session_id=session_id, duration_seconds=payload.duration_seconds, activity_percentage=payload.activity_percentage, data_quality="browser-rms-anonymous")
    db.add(metric); db.commit(); db.refresh(metric)
    return {"activity_percentage": metric.activity_percentage, "duration_seconds": metric.duration_seconds, "recorded_at": metric.recorded_at, "data_quality": metric.data_quality}


@app.post("/api/v1/sessions/{session_id}/media/audio")
async def analyze_session_audio(session_id: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> dict:
    session = get_session(session_id, db)
    if session.class_id:
        ensure_class_access(session.class_id, db, request)
    content = await read_upload(file, {"wav"})
    try: result = await run_in_threadpool(analyze_wav, content)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    metric = AudioMetric(session_id=session_id, duration_seconds=result["duration_seconds"], activity_percentage=result["activity_percentage"])
    db.add(metric); db.commit(); db.refresh(metric)
    return {**result, "notice": "Audio activity is anonymous and does not identify speakers or infer engagement."}


@app.get("/api/v1/reports/attendance.csv")
def attendance_csv(db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin", "department_admin", "faculty"))) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["session_id", "student_id", "status", "duration_seconds", "source"])
    for item in db.scalars(select(AttendanceRecord).order_by(AttendanceRecord.session_id, AttendanceRecord.student_id)):
        writer.writerow([item.session_id, item.student_id, item.status, item.duration_seconds, item.source])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=attendance.csv"})


@app.post("/api/v1/demo/seed")
def reseed_demo(db: Session = Depends(get_db), _: int = Depends(require_roles("super_admin"))) -> dict[str, str]:
    seed_demo(db)
    return {"status": "ready", "message": "Demo data is only created when the database is empty."}
