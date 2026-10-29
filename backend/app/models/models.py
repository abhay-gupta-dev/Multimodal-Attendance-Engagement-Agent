from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    class_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    institution_code: Mapped[str] = mapped_column(String(30), default="LNCT", index=True)
    section: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    program: Mapped[str | None] = mapped_column(String(120), nullable=True)
    batch: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    study_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    semester: Mapped[int | None] = mapped_column(Integer, nullable=True)
    academic_session: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    enrollment_status: Mapped[str] = mapped_column(String(30), default="active")
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Classroom(Base):
    __tablename__ = "classes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    subject: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ClassEnrollment(Base):
    __tablename__ = "class_enrollments"
    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    __table_args__ = (UniqueConstraint("class_id", "student_id", name="uq_class_student"),)


class ClassSession(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(30), default="demo")
    status: Mapped[str] = mapped_column(String(20), default="planned")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="unverified")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("session_id", "student_id", name="uq_attendance_session_student"),)


class EngagementMetric(Base):
    __tablename__ = "engagement_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    indicator: Mapped[float] = mapped_column(Float)
    face_visibility: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_activity: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_quality: Mapped[str] = mapped_column(String(20), default="measured")


class VisionMetric(Base):
    __tablename__ = "vision_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sampled_frames: Mapped[int] = mapped_column(Integer, default=0)
    face_detections: Mapped[int] = mapped_column(Integer, default=0)
    data_quality: Mapped[str] = mapped_column(String(30), default="measured-anonymous")
    landmark_faces: Mapped[int] = mapped_column(Integer, default=0)
    avg_yaw_proxy: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_pitch_proxy: Mapped[float | None] = mapped_column(Float, nullable=True)


class AudioMetric(Base):
    __tablename__ = "audio_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0)
    activity_percentage: Mapped[float] = mapped_column(Float, default=0)
    data_quality: Mapped[str] = mapped_column(String(30), default="measured-rms")


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(160))
    explanation: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AcademicTerm(Base):
    __tablename__ = "academic_terms"
    id: Mapped[int] = mapped_column(primary_key=True)
    institution_code: Mapped[str] = mapped_column(String(30), default="LNCT", index=True)
    program: Mapped[str] = mapped_column(String(120))
    academic_session: Mapped[str] = mapped_column(String(20), index=True)
    year: Mapped[int] = mapped_column(Integer)
    semester: Mapped[int] = mapped_column(Integer)
    verification_status: Mapped[str] = mapped_column(String(40), default="source-roster-unverified")
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("institution_code", "program", "academic_session", "semester", name="uq_institution_program_term"),)


class DataImportJob(Base):
    __tablename__ = "data_import_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    preview_token: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    file_sha256: Mapped[str] = mapped_column(String(64))
    source_file: Mapped[str] = mapped_column(String(255))
    source_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    newly_imported: Mapped[int] = mapped_column(Integer, default=0)
    already_existing: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    conflicting_rows: Mapped[int] = mapped_column(Integer, default=0)
    academic_session: Mapped[str] = mapped_column(String(20), default="2025-26")
    program: Mapped[str] = mapped_column(String(120), default="B.Tech AIML")
    study_year: Mapped[int] = mapped_column(Integer, default=2)
    semester: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(20), default="previewed")
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InstitutionProfile(Base):
    __tablename__ = "institution_profiles"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    profile_json: Mapped[str] = mapped_column(Text)
    source_urls_json: Mapped[str] = mapped_column(Text)
    last_verified: Mapped[str] = mapped_column(String(10))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    permissions_json: Mapped[str] = mapped_column(Text, default="[]")


class UserAccount(Base):
    __tablename__ = "user_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    login_name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id"), unique=True, nullable=True)
    faculty_id: Mapped[int | None] = mapped_column(ForeignKey("faculty.id"), unique=True, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Faculty(Base):
    __tablename__ = "faculty"
    id: Mapped[int] = mapped_column(primary_key=True)
    staff_code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    program: Mapped[str] = mapped_column(String(120), index=True)
    semester: Mapped[int] = mapped_column(Integer)
    academic_session: Mapped[str] = mapped_column(String(40), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FacultySubjectAssignment(Base):
    __tablename__ = "faculty_subject_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculty.id", ondelete="CASCADE"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"))
    __table_args__ = (UniqueConstraint("faculty_id", "subject_id", "class_id", name="uq_faculty_subject_class"),)


class AttendanceCorrection(Base):
    __tablename__ = "attendance_corrections"
    id: Mapped[int] = mapped_column(primary_key=True)
    attendance_id: Mapped[int] = mapped_column(ForeignKey("attendance_records.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id"))
    old_status: Mapped[str] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class BiometricEnrollment(Base):
    __tablename__ = "biometric_enrollments"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), unique=True)
    encrypted_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="not-enrolled")


class ImportError(Base):
    __tablename__ = "import_errors"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_job_id: Mapped[int] = mapped_column(ForeignKey("data_import_jobs.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)


class ApplicationSetting(Base):
    __tablename__ = "application_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ExternalPortalConnection(Base):
    """Metadata only: records an official-portal handoff, never portal credentials."""
    __tablename__ = "external_portal_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"), unique=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), default="lnct_accsoft")
    portal_url: Mapped[str] = mapped_column(String(255), default="https://portal.lnct.ac.in/Accsoft2/studentlogin.aspx")
    status: Mapped[str] = mapped_column(String(30), default="manual_portal_only")
    first_opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AttendanceImportJob(Base):
    __tablename__ = "attendance_import_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    preview_token: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_file: Mapped[str] = mapped_column(String(255))
    source_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    inserted_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(24), default="previewed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExternalAttendanceRecord(Base):
    __tablename__ = "external_attendance_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    import_job_id: Mapped[int] = mapped_column(ForeignKey("attendance_import_jobs.id", ondelete="CASCADE"), index=True)
    source_record_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="student_supplied_report")
    verification_status: Mapped[str] = mapped_column(String(24), default="pending_review", index=True)
    academic_session: Mapped[str] = mapped_column(String(40), index=True)
    semester: Mapped[int] = mapped_column(Integer)
    subject_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    subject_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    record_type: Mapped[str] = mapped_column(String(20))
    classes_conducted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classes_attended: Mapped[int | None] = mapped_column(Integer, nullable=True)
    absences: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attendance_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    attendance_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source_filename: Mapped[str] = mapped_column(String(255))
    source_row_number: Mapped[int] = mapped_column(Integer)
    source_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

