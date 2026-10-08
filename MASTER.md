# MASTER.md — OEM Revenue Forecasting Platform (single source of truth)

> **How to resume:** tell Claude *"Read MASTER.md and continue from §6 NEXT STEPS."*
> Legend: `[x]` written **and verified** · `[w]` written, **not yet executed/tested** · `[ ]` not started.
> Working agreement with the user: **implement everything first → then run the full test & fix pass.** Do not start debugging mid-build. Keep this file updated after every milestone.

---

## 1. Master prompt (condensed, with every agreed decision)

You are a Senior ML Engineer + Full-Stack Architect building a **production-grade AI-Assisted OEM Revenue Forecasting Platform** in this repo. It forecasts monthly demand at **OEM × Home Region × Product Line × Month**, decoupling historical ERP patterns from forward-looking CRM signals, reconciling hierarchies mathematically, allowing governed sales overrides, and flagging backlog risk. Use synthetic enterprise data (clearly labelled `[SYNTHETIC DEMO MODE]`) so it runs without SAP/SFDC.

### Decisions made with the user (do not re-litigate)
1. **Dual-track: forecast Units first, then convert to Revenue with an ASP engine** (`Revenue = Units × ASP`; explicit FX policy, default constant-currency USD).
2. **Sold-To → (End Customer) → OEM chain is core in v1**: distributors, end-customer precedence, allocation % across OEMs, effective-dated mappings, steward review queue. Nothing hardcoded — mappings, rules, identifiers, thresholds are DB rows with admin edit + audit.
3. **Internal now, customers later** → no multi-tenancy in v1 (roadmap). **TiRex = opt-in plugin** (NXAI Community License; legal review) — off by default. **Chronos-2 (Apache-2.0) is the default foundation model.**
4. **Hybrid must not double count**: commercial layer = *net incremental* uplift, β∈[0,1] calibrated on backtest residuals; trained on **point-in-time opportunity snapshots** (no leakage).
5. **Reconciliation**: grouped hierarchy (Global→Region→OEM→Product + OEM-total & Product-total aggregates), MinT (shrinkage) via `hierarchicalforecast`, fallback chain mint_shrink→wls_var→ols, non-negative, **probabilistic** (project joint sample paths), overrides propagate top-down with finest-level pins.
6. **Governance**: AI baseline immutable; overrides are append-only revisions; frozen forecast versions per planning cycle; FVA at matched lead time vs AI **and** vs naive with bootstrap CI; hash-chained audit log; RBAC + row-level scope for reps.
7. **Risk**: Coverage = **Backlog / Consensus** (inverse of the draft's wording); configurable per product/region thresholds with historical-P10 relaxation; supply bottleneck vs capacity; concentration = top-1 share of net uplift.
8. **Segmentation**: Syntetos-Boylan 4 classes (smooth/erratic/intermittent/lumpy) + seasonal + complex; segment gives a *prior*, **champion chosen by backtest** (prior wins ties within tolerance).
9. **Everything in v1, "picture perfect"**; synthetic data validates the pipeline — never present synthetic accuracy as customer evidence.
10. **ASYNC (user requirement: "all async in production")** — see §3.

### Stack
Python 3.11+ (local venv **3.13** at `backend/.venv`; Docker target 3.12), FastAPI, SQLAlchemy 2 (async + sync), Alembic, PostgreSQL+pgvector (SQLite for dev/tests), DuckDB (available; analytics currently pandas), statsforecast, lightgbm, chronos-forecasting 2.3.2 (torch), optional tirex-ts, hierarchicalforecast 1.5.3, Celery+Redis (thread fallback), Next.js 14 App Router + TypeScript + Tailwind + shadcn/ui + Recharts, Docker Compose (api, worker, postgres+pgvector, redis, frontend).

---

## 2. Architecture (tiers)
```
ERP actuals → T1 Baseline (units; Chronos-2 / TiRex / LightGBM / StatsForecast; champion per segment×horizon-bucket; CQR-calibrated P10/P90) ┐
CRM snapshots → T2 Commercial realization (win prob × slip/delay timing; MC) × β (net of baseline) ─────────────────────────────────────────┴→ Hybrid
→ T3 MinT (point + sample projection) → ASP engine → Revenue → T4 Overrides/Consensus/FVA (frozen on lock) → T5 Backlog/Capacity/Concentration risk
```
Hierarchy node ids are `"OEM|REGION|PRODUCT"` with literal `ALL` for aggregates. Levels: TOTAL, REGION, OEM, PRODUCT, OEM_REGION, BOTTOM (valid override/explorer nodes; e.g. `(o,ALL,p)` is INVALID).
Units are **kunits**; revenue **USD**.

## 3. Async architecture (implemented)
- **Async edge, sync core.** Every endpoint is `async def`. Async engine (`postgresql+psycopg` async / `sqlite+aiosqlite`) + `AsyncSession` via `get_adb` (`app/core/db.py`; needs `greenlet`).
- `app/api/aio.py`: `in_session(db, fn, *args, commit=)` (I/O-bound CRUD via `AsyncSession.run_sync`) and `in_thread(fn, *args, commit=)` (CPU-bound pandas/numpy analytics + mutations in a worker thread with its own sync Session; `UserCtx` = detached user snapshot).
- Heavy compute (forecast run, seeding, mapping rebuild, FVA) → Celery workers / job threads, never in request path. Auth dependency is async; bcrypt/JWT/JWKS crypto offloaded to threads. Audit hash-chain uses `pg_advisory_xact_lock` for concurrent writers.
- Services stay **plain sync functions** (shared by workers/tests). Don't add `async` to pandas-heavy code.

---

## 4. File map (all under `/Users/I8798/Desktop/AI OEM Forcasting/`)
```
MASTER.md  (this)   backend/pyproject.toml  backend/alembic.ini  backend/alembic/{env.py,script.py.mako,versions/0001_initial.py}
backend/app/__init__.py              sets KMP_DUPLICATE_LIB_OK (macOS lightgbm+torch libomp clash)
backend/app/core/                    config.py (env settings) · db.py (sync+async engines, EmbeddingType→pgvector) · security.py (JWT/bcrypt/OIDC, async deps, in_scope)
                                     audit.py (hash-chained log) · settings_store.py (DB-editable settings + DEFAULTS) · calendar.py
backend/app/models/                  reference.py facts.py forecast.py governance.py ops.py  (35 tables)
backend/app/data/                    synthetic.py (generator) · loader.py (persist + steward_replay + demo users) · seed.py (seed_demo orchestration)
backend/app/mapping/                 normalize.py embeddings.py vector_store.py rules.py fuzzy.py service.py (set_mapping, review_mapping, map_fact_frame, materialize_mapped_series, to_usd)
backend/app/ml/                      base.py metrics.py segmentation.py backtest.py hierarchy.py reconcile.py asp.py commercial.py
backend/app/ml/models/               stats.py lgbm.py foundation.py (Chronos2Model, TiRexModel) registry.py (model_catalog)
backend/app/forecasting/             data_access.py pipeline.py (run_forecast) service.py (explorer/detail/dashboard read-side)
backend/app/governance/              consensus.py overrides.py cycles.py fva.py
backend/app/risk/engine.py           coverage_table, refresh_alerts, historical_coverage, thresholds
backend/app/quality/                 checks.py (DQ gate) drift.py
backend/app/tasks/                   jobs.py (submit_job/execute_job; thread or Celery) celery_app.py
backend/app/schemas/                 common.py forecast.py risk.py mapping.py admin.py (Pydantic contracts)
backend/app/api/                     aio.py deps.py auth.py forecast.py governance.py risk.py mapping.py admin.py
backend/app/main.py                  create_app, lifespan bootstrap (tables+defaults+demo users), /metrics, CORS
backend/tests/                       conftest.py helpers.py test_synthetic.py test_mapping.py test_segmentation_metrics.py test_reconcile.py
                                     test_governance.py test_commercial_risk.py test_e2e_api.py (slow; seeds demo fast mode + async concurrency test)
frontend/  Next.js 14 app (src/app/(app)/{dashboard,explorer,benchmark,risk,governance,mapping,admin}, src/components, src/lib/{api,auth,types,run-context}.ts[x]) — `tsc` + `next build` PASS
docker-compose.yml, backend/Dockerfile, frontend/Dockerfile, .env.example, Makefile, .github/workflows/ci.yml, README.md, docs/ARCHITECTURE.md (written, docker not run)
```

## 5. Backend API surface (prefix `/api/v1`; Bearer JWT; demo password `demo1234`)
Demo users: `admin@`, `planner@`, `steward@`, `viewer@`, `rep.amer@`, `rep.emea@`, `rep.apac@` + `demo.local`. Roles: admin passes all; planner (overrides, approve, lock, jobs), steward (mapping edits), sales_rep (own-scope overrides, alert updates), viewer (read).
- **Public**: `GET /health`, `GET /meta` (synthetic flag/label, current_run_id, cycle_month/status, horizon, fx_policy), `/metrics` (root). `POST /auth/login`, `GET /auth/me`.
- **Read**: `GET /runs`, `/filters`, `/dashboard?run_id`, `/forecast/explorer?run_id&oem&region&product` (history[], forecast[] incl. p10/p50/p90 units+revenue, baseline_units, uplift_units/revenue, override_*, consensus_*, backlog_value, coverage, capacity_units), `/forecast/detail` (explorer + opportunities + overrides + fva + audit + coverage + drivers), `/benchmark` (rows by model×segment×horizon 1/3/6/12, champions, notes, UNAVAILABLE models with reason).
- **Governance**: `POST /overrides` (planner, sales_rep; body: run_id, oem/region/product ('ALL'), month, basis UNITS|REVENUE, value, reason_code ∈ PROJECT_DELAY|NEW_WIN|CAPACITY_CAP|CUSTOMER_DIRECT_GUIDANCE, comment), `GET /overrides`, `DELETE /overrides/{id}`, `POST /overrides/{id}/review`, `GET /consensus/conflicts`, `GET /cycles`, `POST /runs/{id}/lock`, `POST /cycles/{month}/consensus`, `GET /fva`, `/fva/by-run`, `POST /fva/refresh`, `GET /audit`, `/audit/verify`.
- **Risk**: `GET /risk/alerts` (sorted by $), `PATCH /risk/alerts/{id}`, `/risk/summary`, `/risk/coverage`, `POST /risk/refresh`, `GET|PUT /risk/thresholds`.
- **Mapping** (steward): `GET /mapping/accounts`, `/queue`, `POST /mapping/{id}/review`, `PUT /mapping/accounts/{id}` (allocations), `POST /mapping/run|restate` (jobs), rules CRUD `/mapping/rules`, `/mapping/oems` (+identifiers, aliases).
- **Admin**: `POST|GET /admin/jobs`, `/admin/jobs/{id}`, `/admin/dq`, `/admin/drift`, `GET|PUT /admin/settings`, `GET|POST /admin/users`. Job types: seed_demo, run_forecast, mapping_pipeline, materialize, refresh_risk, compute_fva, run_dq.
- Start dev API: `cd backend && . .venv/bin/activate && export PYTHONPATH=. && uvicorn app.main:app --reload`, then as admin `POST /admin/jobs {"job_type":"seed_demo","params":{"fast":true,"replay_cycles":2}}` and poll the job (full seed takes minutes).

## 6. Progress checklist

### Done & verified `[x]`
- [x] venv + deps; synthetic generator smoke-run (36m, 120 series, 2,567 opps, 14k snapshots, 36k ERP rows, 14% of units from won projects)
- [x] Mapping on synthetic data: 45 rule-mapped, 28 fuzzy→review (decoys "Dell'Orto SpA"/"Delta Dell Logistics" correctly land in review, none auto-activated), 1.2% unmapped after steward replay, units conserved
- [x] Models smoke-tested individually: Naive, SeasonalNaive, AutoETS, AutoARIMA, CrostonSBA, TSB, LightGBM, **Chronos-2 (real weights, CPU; wMAPE 0.158, 84% cov on a holdout)**; TiRex correctly gated off
- [x] App imports; OpenAPI generates (48 paths)

### Test pass 1 results (this session)
- [x] Unit suites: 41 pass. E2E (`-m slow`, fast-mode seed → full pipeline → API/RBAC/async concurrency): 14 pass in ~140s. libomp crash did NOT reproduce on macOS.
- Fixes made: STL seasonal-strength bias on short noise (now also needs ACF(12)≥0.25, Fs≥0.70); DQ `m.product` DataFrame-method bug; risk `hist.to_dict` attr access; override deviation float tolerance; FVA aggregate rows allow n≥1 (CI needs n≥12); 2 loose tests corrected.
- STILL UNVERIFIED: Postgres/pgvector/alembic/advisory lock (needs Docker), Docker images, frontend against live API (shape mismatches likely), `mode=full` pipeline with Chronos, OIDC.

### Written, NOT yet run `[w]` (superseded where noted above)
- [w] Core/db/security/audit/settings; ORM (35 tables); Alembic baseline (create_all + pgvector ext + HNSW)
- [w] Backtest harness, champions, conformal; hierarchy + MinT (library + numpy), ASP engine, commercial engine, pipeline orchestrator, read-side service
- [w] Governance (consensus/overrides/cycles/FVA), risk engine, DQ, drift, jobs/Celery, seed_demo
- [w] All async API routes + schemas; test suite (7 files) — **none executed**

### Not started `[ ]`
- [x] Frontend (§7): all 7 pages + login; `tsc --noEmit` and `next build` pass. NOT yet exercised against a live API (response-shape mismatches likely: risk summary `by_type` keys, audit entity_type filter values, mapping `mapped_to` keys, cycle_month for consensus POST).
- [w] Dockerfiles, docker-compose, .env.example, Makefile, CI, README, docs/ARCHITECTURE.md (written, never run). OpenAPI→TS: `npm run gen:types` (hand-written `src/lib/types.ts` used meanwhile)
- [ ] **Full test & fix pass** (§8), then e2e manual run (seed → UI), Postgres+pgvector integration (docker), `alembic upgrade head` on Postgres

### NEXT STEPS (in order)
1. (done) frontend. 2. (done) Docker/CI/docs. 3. **Test & fix pass (§8) — NOW.** Then run API + UI together and fix shape mismatches. 4. Update this file; final summary to user (be honest about anything unverified).

## 7. Frontend spec (Next.js 14 App Router + TS + Tailwind + shadcn/ui + Recharts)
- Layout: sidebar nav + top bar with **`[SYNTHETIC DEMO MODE]` banner** (from `/meta`), run selector, user menu, light/dark toggle (`data-theme`). Auth: login page → JWT kept in memory + localStorage fallback wrapped in try/catch; typed client generated from OpenAPI (`openapi-typescript`); React Query for data; role-aware UI (viewer read-only; reps limited scope; steward mapping; planner lock/approve; admin everything).
- Pages: **Executive Dashboard** (KPIs: Total Consensus Revenue, AI vs Actual, Backlog Coverage %, Revenue at Risk, Upside Potential, Overall wMAPE; trend chart; top risks; segment mix) · **Forecast Explorer** (filters OEM/Region/Product/Horizon; fan chart: actuals, P10–P90 band, P50, commercial-uplift layer, override markers, consensus; override form w/ reason codes) with **Detail slide-over** (history drivers incl. ADI/CV²/segment, active SFDC opps, override history w/ reason codes, FVA metrics, audit log, coverage) · **Model Benchmark Matrix** (Chronos-2, TiRex-2, LightGBM, StatsForecast × h=1/3/6/12: wMAPE, Bias, champion badge per segment; UNAVAILABLE models shown with reason) · **Risk Center** (queue sorted by $; REVENUE_GAP / SUPPLY_BOTTLENECK / PIPELINE_VULNERABILITY; ack/resolve/owner/note) · **Mapping admin** (review queue approve/reject with evidence, accounts, distributor allocation editor, rules, OEM identifiers/aliases, run/restate jobs) · **Governance** (overrides list/approve/withdraw, FVA table with CI + significance, cycle lock, audit log + chain verify) · **Admin** (jobs w/ progress, DQ, drift, settings, users, seed).
- Honest UX: show UNAVAILABLE models + API `notes`; show backtest-fold caveat; legends for ≥2 series; table-view toggle for charts; hover tooltips; no dual axes.
- **Dataviz rules (from the `dataviz` skill — values embedded so it need not be re-read):** surfaces light `#fcfcfb` / dark `#1a1a19`; text primary `#0b0b0b`/`#ffffff`, secondary `#52514e`/`#c3c2b7`. Categorical order (light|dark): 1 blue `#2a78d6`|`#3987e5`, 2 orange `#eb6834`|`#d95926`, 3 aqua `#1baf7a`|`#199e70`, 4 yellow `#eda100`|`#c98500`, 5 magenta `#e87ba4`|`#d55181`, 6 green `#008300`|`#008300`, 7 violet `#4a3aa7`|`#9085e9`, 8 red `#e34948`|`#e66767` (fixed order, never cycled; only first 3 for scatter/all-pairs; fold the rest to "Other"). Sequential blue 100→700: `#cde2fb #b7d3f6 #9ec5f4 #86b6ef #6da7ec #5598e7 #3987e5 #2a78d6 #256abf #1c5cab #184f95 #104281 #0d366b`. Diverging blue↔red, neutral `#f0efec`(light)/`#383835`(dark). Status (fixed; always icon+label): good `#0ca30c`, warning `#fab219`, serious `#ec835a`, critical `#d03b3b`. Rules: colours as CSS custom properties with `prefers-color-scheme` + `[data-theme]` overrides; thin marks, 2px lines, ≥8px markers, 2px surface gap/ring; recessive grid; legend always for ≥2 series (direct-label ≤4); never colour-only identity; text uses text tokens, not series colour; no dual-axis; tooltip + crosshair on line/area; table view; render and eyeball the result. (Skill can be re-invoked via the `dataviz` skill if new hues are introduced.)
- Fan-chart layer mapping: actuals = series-1 solid; P10–P90 = series-1 translucent band; AI P50 = series-1 dashed; commercial uplift = series-3 (aqua) bars/area; override = series-2 (orange) markers; consensus = text-primary solid line.

## 8. Test & fix pass plan (run only after everything is written)
```
cd backend && . .venv/bin/activate && export PYTHONPATH=.
pytest -q tests/test_synthetic.py tests/test_segmentation_metrics.py tests/test_reconcile.py tests/test_mapping.py tests/test_governance.py tests/test_commercial_risk.py
pytest -q -m slow tests/test_e2e_api.py     # seeds the demo (fast mode) — slowest; also validates MinT coherence, async endpoints, risk alerts, FVA
```
Expect bugs in untested code; likely hot-spots: `pipeline.py` (end-to-end wiring), `reconcile.fit_reconciler` (hierarchicalforecast `MinTrace.fit` signature/orientation), `StatsModel.fitted_residuals`, `asp._stat_forecast`, `commercial.*` merges/dtypes, `risk.engine` merges, `forecasting/service.py`, async `in_session` rollback semantics, `loader.load_bundle` dtype conversions, `seed.simulate_overrides`, loosely-written test assertions (e.g. the total-consensus line in `test_override_flow…`).
**KNOWN ISSUE:** on macOS, LightGBM + torch loaded in one process crashed silently (duplicate libomp). `app/__init__.py` sets `KMP_DUPLICATE_LIB_OK=TRUE` — verify. If still unstable: LightGBM `n_jobs=1`, run Chronos in a subprocess, or skip Chronos on macOS dev (Docker/Linux unaffected).
Also verify on Postgres (docker): pgvector `<=>` SQL in `vector_store.py`, `alembic upgrade head`, async psycopg engine, advisory lock in `audit()`.

## 9. Known limitations / honest notes (put in README too)
- Multi-tenancy deferred. TiRex off by default (licence). 120 series is small → foundation models need not win; benchmark reports truthfully; synthetic ranking ≠ evidence.
- Backtest folds are few with 36 months (h=12 → 3 folds); UI states this. Fuzzy mapper cannot separate name look-alikes (e.g. "Dell'Orto") — by design they go to human review; auto-apply threshold 0.93.
- Probabilistic bands: node P50 is the coherent point forecast (sum-coherent); P10/P90 are marginal sample quantiles per node (not additive).
- OIDC path implemented but untested against a real IdP. Embeddings default = deterministic hashing n-grams (swap via `EMBEDDER=st:<model>`).
- Python 3.14 (host default) lacks wheels for parts of the ML stack → use the 3.13 venv / Docker 3.12.
