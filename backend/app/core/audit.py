"""Append-only, hash-chained audit log. Every governance / mapping / admin mutation goes through here."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.events import notify_data_changed
from app.models.governance import AuditLog

GENESIS = "0" * 64


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def _hash(prev: str, user: str, action: str, etype: str, eid: str, before: Any, after: Any) -> str:
    return hashlib.sha256("|".join([prev, user, action, etype, eid, _canon(before), _canon(after)]).encode()).hexdigest()


def audit(session: Session, user: str, action: str, entity_type: str, entity_id: Any, before: Any = None, after: Any = None) -> AuditLog:
    session.execute(text("SELECT pg_advisory_xact_lock(727001)"))  # serialise chain appends across API/worker processes
    last = session.execute(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)).scalar()
    prev = last or GENESIS
    eid = str(entity_id)
    row = AuditLog(user_id=user, action=action, entity_type=entity_type, entity_id=eid, before=before, after=after,
                   prev_hash=prev, hash=_hash(prev, user, action, entity_type, eid, before, after))
    session.add(row)
    session.flush()
    notify_data_changed(session)  # delivered on commit: other users' open pages refresh
    return row


def verify_chain(session: Session) -> tuple[bool, int | None]:
    """Returns (ok, first_bad_id)."""
    prev = GENESIS
    for r in session.execute(select(AuditLog).order_by(AuditLog.id)).scalars():
        if r.prev_hash != prev or r.hash != _hash(prev, r.user_id, r.action, r.entity_type, r.entity_id, r.before, r.after):
            return False, r.id
        prev = r.hash
    return True, None
