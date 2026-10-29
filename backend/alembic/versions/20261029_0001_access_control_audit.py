"""Add local accounts, role access, faculty assignments, and audit history.

Revision ID: 20261029_0001
Revises:
Create Date: 2026-10-29
"""

from alembic import op
from sqlalchemy import inspect

from app.models import ApplicationSetting, AttendanceCorrection, AuthSession, AuditLog, BiometricEnrollment, Faculty, FacultySubjectAssignment, ImportError, Role, Subject, UserAccount

revision = "20261029_0001"
down_revision = None
branch_labels = None
depends_on = None

NEW_TABLES = [
    Role.__table__, Faculty.__table__, Subject.__table__, UserAccount.__table__, AuthSession.__table__,
    FacultySubjectAssignment.__table__, AttendanceCorrection.__table__, AuditLog.__table__,
    BiometricEnrollment.__table__, ImportError.__table__, ApplicationSetting.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    for table in NEW_TABLES:
        if table.name not in existing:
            table.create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(NEW_TABLES):
        if inspect(bind).has_table(table.name):
            table.drop(bind=bind)
