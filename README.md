# AI-Assisted OEM Revenue Forecasting Platform

Forecasts monthly demand at **OEM × Home Region × Product Line**, decoupling ERP actuals (baseline) from CRM signals (net incremental uplift), reconciling the hierarchy with probabilistic MinT, converting units → revenue with an ASP engine, and governing sales overrides with FVA and a tamper-evident audit trail. Ships with a synthetic data generator; the UI is labelled **[SYNTHETIC DEMO MODE]**.

> Status: all components are implemented. See `MASTER.md` for the exact verification status of each module and the open test-and-fix list — do not assume untested code is correct.

## Quickstart (Docker)
```bash
cp .env.example .env          # set JWT_SECRET
docker compose up --build     # api :8000, web :3000, postgres+pgvector, redis, celery worker
```
Open http://localhost:3000, sign in `admin@demo.local` / `demo1234` → **Admin → Jobs → seed_demo (fast)**. Other demo users: `planner@`, `steward@`, `viewer@`, `rep.amer@`, `rep.emea@`, `rep.apac@demo.local`.

## Local dev (no Docker; SQLite, thread job runner)
```bash
make setup && make api        # http://localhost:8000/docs
make web                      # http://localhost:3000
make test-fast
```

## Architecture
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Async FastAPI edge over a sync analytics core; heavy work runs in Celery/job threads.

## Honest limitations
- Single tenant; multi-tenancy is roadmap. OIDC code path untested against a real IdP.
- TiRex is opt-in (NXAI Community License). Chronos-2 (Apache-2.0) is the default foundation model.
- Synthetic accuracy is not customer evidence. With 36 months of history, long-horizon backtests have few folds.
- Fuzzy mapping never auto-applies below 0.93; look-alike names go to steward review by design.
- Probabilistic bands: P50 is sum-coherent; P10/P90 are per-node marginal quantiles (not additive).
