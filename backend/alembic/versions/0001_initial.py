"""Baseline schema (all tables) + pgvector extension and HNSW indexes.

Revision ID: 0001
Revises:
"""
from alembic import op

import app.models  # noqa: F401
from app.core.db import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    if pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind)
    if pg:
        op.execute("CREATE INDEX IF NOT EXISTS ix_accounts_embedding_hnsw ON accounts USING hnsw (embedding vector_cosine_ops)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_oem_aliases_embedding_hnsw ON oem_aliases USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
