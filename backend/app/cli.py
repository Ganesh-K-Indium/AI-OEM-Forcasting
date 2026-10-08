"""Command-line entry points (no server needed):  python -m app.cli seed [--full] [--replay-cycles N]"""
from __future__ import annotations

import argparse
import json
import sys
import time

from app.core.db import Base, SessionLocal, engine


def seed(fast: bool, replay_cycles: int | None) -> dict:
    import app.models  # noqa: F401  (register tables)
    from app.core.settings_store import seed_defaults
    from app.data.loader import ensure_demo_users
    from app.data.seed import seed_demo
    from app.mapping.rules import seed_default_rules
    from app.risk.engine import seed_default_thresholds

    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed_defaults(s); seed_default_rules(s); seed_default_thresholds(s); ensure_demo_users(s); s.commit()
    t0, last = time.time(), [""]

    def progress(frac: float, msg: str) -> None:
        if msg != last[0]:
            last[0] = msg
            print(f"[{time.time() - t0:6.0f}s] {frac * 100:3.0f}%  {msg}", flush=True)

    with SessionLocal() as s:
        out = seed_demo(s, progress, replay_cycles=replay_cycles if replay_cycles is not None else (2 if fast else 6), fast=fast)
        s.commit()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("seed", help="generate synthetic data, map it, run forecast cycles")
    sp.add_argument("--full", action="store_true", help="full model zoo + 6 replay cycles (slow; Chronos-2 needs torch)")
    sp.add_argument("--replay-cycles", type=int, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "seed":
        print(json.dumps(seed(not a.full, a.replay_cycles), default=str, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
