"""Fuzzy ML mapper: Levenshtein token alignment + embedding cosine (pgvector) + kNN over already-mapped accounts.
Everything below `fuzzy_auto_threshold` is routed to a steward queue - it is never silently merged."""
from __future__ import annotations

from datetime import date

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from rapidfuzz.distance import Levenshtein
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings_store import get_setting
from app.mapping import vector_store
from app.mapping.embeddings import get_embedder
from app.mapping.normalize import normalize_name
from app.models.reference import Account, AccountOemMapping, OemAlias


@dataclass
class Candidate:
    oem_id: int
    score: float
    name_sim: float
    cosine: float
    knn: float
    region_code: str | None
    best_alias: str
    bonus: float = 0.0
    extra: dict = field(default_factory=dict)


def name_similarity(name_norm: str, alias_norm: str, generic: set[str]) -> float:
    """Typo-tolerant token coverage of the alias inside the name, penalised by extra *distinctive* tokens;
    falls back to whole-string Levenshtein similarity."""
    nt, at = name_norm.split(), alias_norm.split()
    if not nt or not at:
        return 0.0
    best = [max(Levenshtein.normalized_similarity(a, t) for t in nt) for a in at]
    best = [b if b >= 0.6 else 0.0 for b in best]
    coverage = float(np.mean(best))
    matched = {t for t in nt if any(Levenshtein.normalized_similarity(a, t) >= 0.6 for a in at)}
    extras = [t for t in nt if t not in matched and t not in generic]
    token_sim = coverage * (1 - min(0.36, 0.12 * len(extras)))
    return max(token_sim, Levenshtein.normalized_similarity(name_norm, alias_norm))


def ensure_embeddings(session: Session) -> int:
    emb = get_embedder()
    n = 0
    for model, attr in ((Account, "name"), (OemAlias, "alias")):
        rows = list(session.execute(select(model).where(model.embedding.is_(None))).scalars())
        if rows:
            vecs = emb.embed([getattr(r, attr) for r in rows])
            for r, v in zip(rows, vecs):
                r.embedding = v
            n += len(rows)
    session.flush()
    return n


def score_account(session: Session, acc: Account, aliases_by_oem: dict[int, list[OemAlias]], weights: dict, generic: set[str],
                  top_k: int = 8) -> list[Candidate]:
    q = acc.embedding if acc.embedding is not None else get_embedder().embed([acc.name])[0]
    name_norm = normalize_name(acc.name)
    alias_hits = vector_store.nearest_aliases(session, q, top_k)
    knn_hits = vector_store.nearest_mapped_accounts(session, q, 5, exclude_account_id=acc.id)
    cos_by_oem: dict[int, float] = defaultdict(float)
    for _, oem_id, sim in alias_hits:
        cos_by_oem[oem_id] = max(cos_by_oem[oem_id], sim)
    wsum = sum(max(s, 0) for *_, s in knn_hits) or 1.0
    knn_by_oem: dict[int, float] = defaultdict(float)
    region_votes: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for _, oem_id, region, sim in knn_hits:
        knn_by_oem[oem_id] += max(sim, 0) / wsum
        region_votes[oem_id][region] += max(sim, 0)
    cands: list[Candidate] = []
    for oem_id in set(cos_by_oem) | set(knn_by_oem):
        sims = [(name_similarity(name_norm, normalize_name(a.alias), generic), a.alias) for a in aliases_by_oem.get(oem_id, [])]
        ns, best_alias = max(sims, default=(0.0, ""))
        score = weights["name"] * ns + weights["vector"] * max(cos_by_oem[oem_id], 0) + weights["knn"] * knn_by_oem[oem_id]
        region = acc.region_code or (max(region_votes[oem_id], key=region_votes[oem_id].get) if region_votes[oem_id] else None)
        cands.append(Candidate(oem_id, float(min(score, 1.0)), float(ns), float(cos_by_oem[oem_id]), float(knn_by_oem[oem_id]), region, best_alias))
    return sorted(cands, key=lambda c: -c.score)


def run_fuzzy_mapper(session: Session, user: str = "system:fuzzy", valid_from=None) -> dict:
    ensure_embeddings(session)
    auto_t = float(get_setting(session, "fuzzy_auto_threshold"))
    review_t = float(get_setting(session, "fuzzy_review_threshold"))
    weights = get_setting(session, "fuzzy_weights")
    generic = {g.lower() for g in get_setting(session, "fuzzy_generic_tokens")}
    aliases_by_oem: dict[int, list[OemAlias]] = defaultdict(list)
    for a in session.execute(select(OemAlias)).scalars():
        aliases_by_oem[a.oem_id].append(a)
    blocked = defaultdict(set)  # (account) -> oems already rejected
    for m in session.execute(select(AccountOemMapping).where(AccountOemMapping.status == "REJECTED")).scalars():
        blocked[m.account_id].add(m.oem_id)
    live = {r for (r,) in session.execute(select(AccountOemMapping.account_id).where(AccountOemMapping.status.in_(["ACTIVE", "PENDING_REVIEW"])))}
    out = dict(auto=0, review=0, no_candidate=0, skipped_distributors=0)
    # process accounts in descending name-quality order is unnecessary; kNN evidence only uses ACTIVE rows
    for acc in session.execute(select(Account).where(Account.is_active).order_by(Account.id)).scalars():
        if acc.id in live:
            continue
        if acc.account_type == "DISTRIBUTOR":
            out["skipped_distributors"] += 1
            continue
        cands = [c for c in score_account(session, acc, aliases_by_oem, weights, generic) if c.oem_id not in blocked[acc.id]]
        if not cands or cands[0].score < review_t or not cands[0].region_code:
            out["no_candidate"] += 1
            continue
        top = cands[0]
        status = "ACTIVE" if top.score >= auto_t else "PENDING_REVIEW"
        out["auto" if status == "ACTIVE" else "review"] += 1
        session.add(AccountOemMapping(
            account_id=acc.id, oem_id=top.oem_id, region_code=top.region_code, allocation_pct=1.0, source="FUZZY", confidence=top.score,
            status=status, valid_from=valid_from or date(1900, 1, 1), created_by=user,
            evidence={"candidates": [dict(oem_id=c.oem_id, score=round(c.score, 4), name_sim=round(c.name_sim, 4), cosine=round(c.cosine, 4),
                                          knn=round(c.knn, 4), best_alias=c.best_alias) for c in cands[:3]]}))
        session.flush()  # later accounts may use this ACTIVE mapping as kNN evidence
    return out
