"""Nearest-neighbour search over alias / account embeddings.
PostgreSQL -> pgvector cosine operator (HNSW-indexable). Other dialects -> exact numpy cosine."""
from __future__ import annotations

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.reference import Account, AccountOemMapping, OemAlias


def _is_pg(session: Session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _vec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in np.asarray(v, dtype=float)) + "]"


def nearest_aliases(session: Session, q: np.ndarray, k: int = 8) -> list[tuple[int, int, float]]:
    """-> [(alias_id, oem_id, cosine_similarity)]"""
    if _is_pg(session):
        rows = session.execute(
            text("SELECT id, oem_id, 1 - (embedding <=> CAST(:q AS vector)) AS sim FROM oem_aliases "
                 "WHERE embedding IS NOT NULL ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"),
            {"q": _vec_literal(q), "k": k}).all()
        return [(r.id, r.oem_id, float(r.sim)) for r in rows]
    rows = session.execute(select(OemAlias.id, OemAlias.oem_id, OemAlias.embedding).where(OemAlias.embedding.is_not(None))).all()
    if not rows:
        return []
    M = np.vstack([r.embedding for r in rows])
    sims = M @ q / (np.linalg.norm(M, axis=1) * np.linalg.norm(q) + 1e-12)
    order = np.argsort(-sims)[:k]
    return [(rows[i].id, rows[i].oem_id, float(sims[i])) for i in order]


def nearest_mapped_accounts(session: Session, q: np.ndarray, k: int = 5, exclude_account_id: int | None = None
                            ) -> list[tuple[int, int, str, float]]:
    """-> [(account_id, oem_id, region_code, similarity)] over accounts with an ACTIVE mapping."""
    if _is_pg(session):
        rows = session.execute(
            text("SELECT a.id AS account_id, m.oem_id, m.region_code, 1 - (a.embedding <=> CAST(:q AS vector)) AS sim "
                 "FROM accounts a JOIN account_oem_mappings m ON m.account_id = a.id AND m.status = 'ACTIVE' "
                 "WHERE a.embedding IS NOT NULL AND a.id <> :ex ORDER BY a.embedding <=> CAST(:q AS vector) LIMIT :k"),
            {"q": _vec_literal(q), "k": k, "ex": exclude_account_id or -1}).all()
        return [(r.account_id, r.oem_id, r.region_code, float(r.sim)) for r in rows]
    rows = session.execute(
        select(Account.id, Account.embedding, AccountOemMapping.oem_id, AccountOemMapping.region_code)
        .join(AccountOemMapping, AccountOemMapping.account_id == Account.id)
        .where(AccountOemMapping.status == "ACTIVE", Account.embedding.is_not(None), Account.id != (exclude_account_id or -1))).all()
    if not rows:
        return []
    M = np.vstack([r.embedding for r in rows])
    sims = M @ q / (np.linalg.norm(M, axis=1) * np.linalg.norm(q) + 1e-12)
    order = np.argsort(-sims)[:k]
    return [(rows[i].id, rows[i].oem_id, rows[i].region_code, float(sims[i])) for i in order]
