"""Tests run against a real PostgreSQL + pgvector (`make test-db` starts one on :5544).
Every test works inside the throw-away workspace schema `ws_test`; shared tables (users, workspaces, jobs) live in `public`."""
import os
import tempfile
import warnings
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="oem_tests_"))
os.environ.update(DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://oem:oem@localhost:5544/oem_test"), ENVIRONMENT="test",
                  ARTIFACT_DIR=str(_TMP / "artifacts"), IMPORT_DIR=str(_TMP / "import"), ENABLE_CHRONOS="false", USE_CELERY="false", MC_SAMPLES="120",
                  SYNTH_OPPORTUNITIES="1200", JWT_SECRET="test-secret-test-secret-test-secret-123", BOOTSTRAP_DEMO_WORKSPACE="false")
warnings.filterwarnings("ignore")

import pytest  # noqa: E402

import app.models  # noqa: E402,F401
from app.core import workspace as wsmod  # noqa: E402
from app.core.db import Base, SessionLocal, current_schema, engine, shared_tables  # noqa: E402

TEST_SCHEMA = "ws_test"


@pytest.fixture(scope="session", autouse=True)
def _shared_schema():
    wsmod.ensure_extensions()
    Base.metadata.drop_all(engine, tables=shared_tables())
    Base.metadata.create_all(engine, tables=shared_tables())
    wsmod.drop_schema(TEST_SCHEMA)
    wsmod.provision_schema(TEST_SCHEMA)
    current_schema.set(TEST_SCHEMA)
    yield


@pytest.fixture(autouse=True)
def _bind_workspace():
    current_schema.set(TEST_SCHEMA)
    yield


@pytest.fixture(scope="session")
def bundle():
    from app.core.config import get_settings
    from app.data.synthetic import generate

    s = get_settings()
    return generate(s.synth_seed, s.synth_end_month, s.synth_months, s.synth_opportunities)


@pytest.fixture()
def fresh_db():
    wsmod.reset_workspace(TEST_SCHEMA)
    with SessionLocal() as s:
        yield s


@pytest.fixture(scope="session")
def loaded_db(bundle):
    """Synthetic data loaded + entity-resolved + materialised (no forecasts)."""
    from app.data import loader
    from app.mapping import service

    current_schema.set(TEST_SCHEMA)
    wsmod.reset_workspace(TEST_SCHEMA)
    with SessionLocal() as s:
        loader.ensure_demo_users(s)
        loader.load_bundle(s, bundle)
        service.run_mapping_pipeline(s, "test")
        loader.steward_replay(s, bundle)
        service.materialize_mapped_series(s)
        from app.risk.engine import seed_default_thresholds

        seed_default_thresholds(s)
        s.commit()
    return True
