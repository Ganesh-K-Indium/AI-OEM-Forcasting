"""Shared schema: users, workspaces, jobs (+ pgvector). Workspace data lives in per-workspace schemas created at runtime.

Revision ID: 0001
Revises:
"""
from alembic import op

import app.models  # noqa: F401
from app.core.db import Base, shared_tables

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(op.get_bind(), tables=shared_tables())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind(), tables=shared_tables())
