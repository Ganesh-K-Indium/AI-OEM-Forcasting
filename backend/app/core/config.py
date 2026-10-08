"""Central configuration (env-driven; nothing business-specific is hardcoded here)."""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "OEM Revenue Forecasting Platform"
    environment: str = "dev"  # dev | test | prod
    database_url: str = "postgresql+psycopg://oem:oem@localhost:5432/oem"  # PostgreSQL + pgvector only
    redis_url: str = "redis://localhost:6379/0"
    use_celery: bool = False  # False -> in-process thread runner (single-node / dev)
    artifact_dir: Path = BACKEND_DIR / "data" / "artifacts"
    cors_origins: str = "http://localhost:3000"

    # --- auth ---
    auth_mode: str = "local"  # local | oidc
    jwt_secret: str = "change-me-in-production-please-32+chars"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 8
    oidc_jwks_url: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_role_claim: str = "roles"

    # --- demo / synthetic ---
    synthetic_mode: bool = True
    synth_seed: int = 42
    synth_end_month: date = date(2026, 9, 1)  # last fully-closed month of history
    synth_months: int = 36
    synth_opportunities: int = 2400
    bootstrap_demo_users: bool = True
    bootstrap_demo_workspace: bool = True  # create an empty "Synthetic demo" workspace on first start
    import_dir: Path = BACKEND_DIR / "data" / "import"  # server-side folder for large datasets (e.g. M5) and uploaded files

    # --- forecasting ---
    horizon: int = 12
    embedding_dim: int = 256
    enable_chronos: bool = True
    chronos_model_id: str = "amazon/chronos-2"
    enable_tirex: bool = False  # NXAI Community License: opt-in only after legal review
    tirex_model_id: str = "NX-AI/TiRex"
    reconcile_method: str = "mint_shrink"
    mc_samples: int = 400
    random_seed: int = 7


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.artifact_dir.mkdir(parents=True, exist_ok=True)
    s.import_dir.mkdir(parents=True, exist_ok=True)
    return s
