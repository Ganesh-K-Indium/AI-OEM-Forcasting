# Architecture

```
ERP actuals ─► Entity resolution (Sold-To → End Customer → OEM, effective-dated, steward queue)
            ─► T1 Baseline (units): segmentation → rolling-origin backtest → champion per segment×horizon bucket
                 models: Naive/SeasonalNaive/AutoETS/ARIMA/Croston-SBA/TSB, LightGBM, Chronos-2 (default FM), TiRex (opt-in)
                 CQR-conformal P10/P90
CRM snapshots ─► T2 Commercial: win-prob (LightGBM+isotonic) × slip/delay timing, Monte-Carlo; net-of-baseline uplift × β
            Hybrid = baseline + β·uplift
            ─► T3 Grouped-hierarchy MinT (shrinkage; fallback wls_var → ols), non-negative, probabilistic sample projection
            ─► ASP engine (AutoETS on log-ASP + contract blend + FX policy) → Revenue = Units × ASP
            ─► T4 Overrides (append-only) → consensus allocation (finest-first, pins, conflicts) → cycle lock (frozen) → FVA
            ─► T5 Risk: Coverage = Backlog / Consensus; supply bottleneck; pipeline concentration
```

## Hierarchy
Node id `OEM|REGION|PRODUCT`, `ALL` for aggregates. Levels TOTAL, REGION, OEM, PRODUCT, OEM_REGION, BOTTOM. `(oem, ALL, product)` is invalid.

## Async model
Every endpoint is `async def`. I/O CRUD uses `AsyncSession` (`in_session`); CPU-bound pandas/numpy work uses `in_thread` with its own sync Session; forecast/seed/mapping rebuilds are jobs (Celery or thread). Auth crypto is off-loop. Services remain plain sync functions shared by workers and tests.

## Governance
AI baseline immutable; overrides are revisions; deviation > 60% requires a ≥15-char comment; cycle OPEN→FORECASTED→CONSENSUS→LOCKED; lock writes frozen `ConsensusPoint` rows; FVA vs AI and vs naive with bootstrap CI; SHA-256 hash-chained audit log (Postgres advisory lock serialises writers).

## Data stores
Postgres + pgvector (embeddings for fuzzy mapping, HNSW index) — the only supported database, including tests; Redis for Celery.

## Frontend
Next.js 14 App Router, Tailwind, Radix, Recharts, TanStack Query. Types: `frontend/src/lib/types.ts` (hand-mirrored) and `npm run gen:types` (openapi-typescript → `api-schema.d.ts`).
