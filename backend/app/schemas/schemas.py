from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StudentCreate(BaseModel):
    student_code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=2, max_length=120)
    email: str | None = None
    class_name: str | None = None
    institution_code: str = Field(default="LNCT", max_length=30)
    section: str | None = Field(default=None, max_length=10)
    program: str | None = Field(default=None, max_length=120)
    batch: str | None = Field(default=None, max_length=20)
    study_year: int | None = Field(default=None, ge=1, le=8)
    semester: int | None = Field(default=None, ge=1, le=16)
    academic_session: str | None = Field(default=None, max_length=20)
    enrollment_status: str = Field(default="active", max_length=30)


class StudentUpdate(StudentCreate):
    active: bool = True


class StudentOut(StudentCreate):
    id: int
    active: bool
    model_config = ConfigDict(from_attributes=True)


class ClassCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    subject: str | None = None
    description: str | None = None


class ClassOut(ClassCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class SessionCreate(BaseModel):
    class_id: int | None = None
    title: str = Field(min_length=2, max_length=160)
    source: str = Field(default="demo", pattern="^(demo|webcam|video|audio)$")


class SessionOut(SessionCreate):
    id: int
    status: str
    is_demo: bool
    started_at: datetime | None
    ended_at: datetime | None
    model_config = ConfigDict(from_attributes=True)


class AttendanceCreate(BaseModel):
    student_id: int
    status: str = Field(pattern="^(present|absent|late|unverified)$")
    duration_seconds: int = Field(default=0, ge=0)
    note: str | None = None


class AttendanceOut(AttendanceCreate):
    id: int
    session_id: int
    source: str
    model_config = ConfigDict(from_attributes=True)


class StudentImportSummary(BaseModel):
    import_id: int
    preview_token: str
    source_file: str
    source_rows: int
    valid_rows: int
    newly_imported: int
    new_to_import: int = 0
    already_existing: int
    invalid_rows: int
    conflicting_rows: int
    section_counts: dict[str, int]
    errors: list[dict]
    preview: list[dict]

