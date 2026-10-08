"""Postgres + pgvector integration (run with TEST_POSTGRES_URL=postgresql+psycopg://...)."""
import os
import threading

import numpy as np
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres
URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.fixture()
def pg():
    if not URL:
        pytest.skip("TEST_POSTGRES_URL not set")
    from app.core.db import Base
    import app.models  # noqa: F401

    eng = create_engine(URL)
    with eng.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng, sessionmaker(eng, expire_on_commit=False)
    Base.metadata.drop_all(eng)
    eng.dispose()


def test_pgvector_nearest_alias_uses_cosine_operator(pg):
    from app.mapping.embeddings import get_embedder
    from app.mapping.vector_store import nearest_aliases
    from app.models.reference import Oem, OemAlias

    eng, SM = pg
    emb = get_embedder()
    with SM() as s:
        for i, (code, alias) in enumerate([("APPLE", "apple inc"), ("DELL", "dell technologies"), ("BOSCH", "robert bosch gmbh")], 1):
            s.add(Oem(id=i, code=code, name=code))
            s.flush()
            s.add(OemAlias(oem_id=i, alias=alias, normalized_alias=alias, embedding=emb.embed([alias])[0]))
        s.commit()
        hits = nearest_aliases(s, emb.embed(["apple incorporated"])[0], k=3)
        assert hits[0][1] == 1 and hits[0][2] > hits[-1][2]  # (alias_id, oem_id, sim) - Apple ranked first, sims descending


def test_audit_hash_chain_stays_intact_under_concurrent_writers(pg):
    from app.core.audit import audit, verify_chain

    eng, SM = pg

    def worker(w):
        for i in range(15):
            with SM() as s:
                audit(s, f"u{w}", "ACT", "thing", f"{w}-{i}", None, {"i": i})
                s.commit()

    ts = [threading.Thread(target=worker, args=(w,)) for w in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    with SM() as s:
        from app.models.governance import AuditLog

        assert len(s.execute(select(AuditLog)).scalars().all()) == 90
        assert verify_chain(s) == (True, None)
