import os
import tempfile
import warnings
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="oem_tests_"))
os.environ.update(DATABASE_URL=f"sqlite:///{_TMP / 'test.db'}", ENVIRONMENT="test", ARTIFACT_DIR=str(_TMP / "artifacts"), ENABLE_CHRONOS="false",
                  USE_CELERY="false", MC_SAMPLES="120", SYNTH_OPPORTUNITIES="1200", JWT_SECRET="test-secret-test-secret-test-secret-123")
warnings.filterwarnings("ignore")

import pytest  # noqa: E402

from app.core.db import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401


@pytest.fixture(scope="session")
def bundle():
    from app.core.config import get_settings
    from app.data.synthetic import generate

    s = get_settings()
    return generate(s.synth_seed, s.synth_end_month, s.synth_months, s.synth_opportunities)


@pytest.fixture()
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        yield s
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def loaded_db(bundle):
    """Synthetic data loaded + entity-resolved + materialised (no forecasts)."""
    from app.data import loader
    from app.mapping import service

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
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
