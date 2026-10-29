"""Add manual AccSoft handoff metadata and private attendance report imports.

Revision ID: 20261029_0002
Revises: 20261029_0001
Create Date: 2026-10-29
"""

from alembic import op
from sqlalchemy import inspect

from app.models import AttendanceImportJob, ExternalAttendanceRecord, ExternalPortalConnection

revision = "20261029_0002"
down_revision = "20261029_0001"
branch_labels = None
depends_on = None

NEW_TABLES = [ExternalPortalConnection.__table__, AttendanceImportJob.__table__, ExternalAttendanceRecord.__table__]


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
