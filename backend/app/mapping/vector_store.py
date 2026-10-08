"""Nearest-neighbour search over alias / account embeddings (pgvector cosine operator `<=>`, served by the HNSW indexes)."""
from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session


def _vec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in np.asarray(v, dtype=float)) + "]"


def nearest_aliases(session: Session, q: np.ndarray, k: int = 8) -> list[tuple[int, int, float]]:
    """-> [(alias_id, oem_id, cosine_similarity)]"""
    rows = session.execute(
        text("SELECT id, oem_id, 1 - (embedding <=> CAST(:q AS vector)) AS sim FROM oem_aliases "
             "WHERE embedding IS NOT NULL ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"),
        {"q": _vec_literal(q), "k": k}).all()
    return [(r.id, r.oem_id, float(r.sim)) for r in rows]


def nearest_mapped_accounts(session: Session, q: np.ndarray, k: int = 5, exclude_account_id: int | None = None
                            ) -> list[tuple[int, int, str, float]]:
    """-> [(account_id, oem_id, region_code, similarity)] over accounts with an ACTIVE mapping."""
    rows = session.execute(
        text("SELECT a.id AS account_id, m.oem_id, m.region_code, 1 - (a.embedding <=> CAST(:q AS vector)) AS sim "
             "FROM accounts a JOIN account_oem_mappings m ON m.account_id = a.id AND m.status = 'ACTIVE' "
             "WHERE a.embedding IS NOT NULL AND a.id <> :ex ORDER BY a.embedding <=> CAST(:q AS vector) LIMIT :k"),
        {"q": _vec_literal(q), "k": k, "ex": exclude_account_id or -1}).all()
    return [(r.account_id, r.oem_id, r.region_code, float(r.sim)) for r in rows]
