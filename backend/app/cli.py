"""Command-line entry points (no server needed):
     python -m app.cli seed   [--full] [--replay-cycles N] [--workspace SLUG]   synthetic data -> mapping -> forecasts
     python -m app.cli import-m5 [--folder DIR] [--workspace SLUG] [--limit-items N]   load the M5 files from a folder
     python -m app.cli workspaces                                                 list workspaces"""
from __future__ import annotations

import argparse
import json
import sys
import time

from sqlalchemy import select


def _progress():
    t0, last = time.time(), [""]

    def progress(frac: float, msg: str) -> None:
        if msg != last[0]:
            last[0] = msg
            print(f"[{time.time() - t0:6.0f}s] {frac * 100:3.0f}%  {msg}", flush=True)

    return progress


def _bootstrap_shared() -> None:
    import app.models  # noqa: F401  (register tables)
    from app.core import workspace as wsmod
    from app.core.db import Base, SessionLocal, engine, shared_tables
    from app.data.loader import ensure_demo_users

    wsmod.ensure_extensions()
    Base.metadata.create_all(engine, tables=shared_tables())
    with SessionLocal(None) as s:
        ensure_demo_users(s)
        s.commit()


def _workspace(slug: str | None, kind: str, default_name: str):
    from app.core import workspace as wsmod
    from app.core.db import SessionLocal
    from app.models.ops import Workspace

    with SessionLocal(None) as s:
        w = s.execute(select(Workspace).where(Workspace.slug == (slug or wsmod.slugify(default_name)))).scalar_one_or_none()
        if w is None:
            w = wsmod.create_workspace(s, default_name if not slug else slug, kind, None, "cli")
        elif w.kind != kind:
            raise SystemExit(f"workspace '{w.slug}' is of kind '{w.kind}', not '{kind}'")
        return w.id, w.schema_name


def seed(fast: bool, replay_cycles: int | None, slug: str | None = None) -> dict:
    from app.core import workspace as wsmod
    from app.core.db import SessionLocal, use_workspace
    from app.data.seed import seed_demo

    _bootstrap_shared()
    ws_id, schema = _workspace(slug, "synthetic", "Synthetic demo")
    with use_workspace(schema), SessionLocal() as s:
        out = seed_demo(s, _progress(), replay_cycles=replay_cycles if replay_cycles is not None else (2 if fast else 6), fast=fast)
        s.commit()
    wsmod.refresh_capabilities(schema, ws_id)
    return out


def import_m5(folder: str | None, slug: str | None, limit_items: int | None) -> dict:
    from app.core import workspace as wsmod
    from app.core.config import get_settings
    from app.core.db import SessionLocal, use_workspace
    from app.data.importer import run_import

    _bootstrap_shared()
    ws_id, schema = _workspace(slug, "m5", "M5 benchmark")
    params = dict(source="m5", workspace_id=ws_id, m5_dir=folder or str(get_settings().import_dir / "m5"), options=dict(limit_items=limit_items, run_forecast=True))
    with use_workspace(schema), SessionLocal() as s:
        out = run_import(s, params, "cli", _progress())
        s.commit()
    wsmod.refresh_capabilities(schema, ws_id)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("seed", help="generate synthetic data, map it, run forecast cycles")
    sp.add_argument("--full", action="store_true", help="full model zoo + 6 replay cycles (slow; Chronos-2 needs torch)")
    sp.add_argument("--replay-cycles", type=int, default=None)
    sp.add_argument("--workspace", default=None, help="workspace slug (created if missing; default: synthetic-demo)")
    mp = sub.add_parser("import-m5", help="load the M5 (Walmart) files from the import folder into an M5 workspace")
    mp.add_argument("--folder", default=None)
    mp.add_argument("--workspace", default=None)
    mp.add_argument("--limit-items", type=int, default=None, help="only the first N items (quick trial)")
    sub.add_parser("workspaces", help="list workspaces")
    a = ap.parse_args(argv)
    if a.cmd == "seed":
        print(json.dumps(seed(not a.full, a.replay_cycles, a.workspace), default=str, indent=2)[:1500])
    elif a.cmd == "import-m5":
        print(json.dumps(import_m5(a.folder, a.workspace, a.limit_items), default=str, indent=2)[:1500])
    elif a.cmd == "workspaces":
        from app.core.db import SessionLocal
        from app.models.ops import Workspace

        _bootstrap_shared()
        with SessionLocal(None) as s:
            for w in s.execute(select(Workspace).order_by(Workspace.created_at)).scalars():
                print(f"{w.slug:24} {w.kind:10} {w.status:10} {w.schema_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
