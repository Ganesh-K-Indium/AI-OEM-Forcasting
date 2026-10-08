<div align="center">

# AI-Assisted OEM Revenue Forecasting Platform

**Forecast monthly demand by OEM × Home Region × Product Line — with ERP actuals and CRM pipeline kept separate, hierarchies reconciled, overrides governed, and backlog risk flagged.**

`FastAPI (async)` · `Postgres + pgvector` · `Celery + Redis` · `Chronos-2 / LightGBM / StatsForecast` · `Next.js 14` · `Docker Compose`

[Quickstart](#-quickstart) · [Product tour](#-product-tour) · [How it works](#-how-it-works) · [Roles](#-roles--permissions) · [API](#-api-at-a-glance) · [Configuration](#-configuration) · [Development](#-development) · [Troubleshooting](#-troubleshooting) · [Limitations](#-honest-limitations)

> **[SYNTHETIC DEMO MODE]** — the repo ships with a deterministic synthetic data generator so everything runs without SAP or Salesforce. Accuracy numbers you see are properties of fake data, **not evidence of real-world performance.**

</div>

---

## What problem does this solve?

A components supplier sells to a handful of OEMs (Apple, Bosch, …) — partly directly, partly **through distributors**. Planning needs a number per **OEM × Region × Product × Month**, but:

| Pain | What the platform does |
|---|---|
| ERP only knows the *Sold-To* (often a distributor), not the end OEM | A governed **Sold-To → End Customer → OEM** mapping chain (rules + fuzzy ML + steward review, effective-dated) |
| History repeats; the pipeline changes the future — mixing them double counts | **Baseline** from ERP actuals + **net-incremental uplift** from CRM, never gross |
| Revenue forecasts hide price/FX effects | **Units first**, then an **ASP engine** → revenue (constant-currency by default) |
| Region, OEM and product forecasts don't add up | **MinT reconciliation** so every level sums coherently, with probabilistic bands |
| Sales overrides are uncontrolled and unmeasured | **Append-only overrides**, reason codes, cycle lock, **Forecast Value Added** (does the override beat the AI?), hash-chained audit log |
| Nobody knows where the plan is exposed | **Risk Center**: revenue gap (backlog ÷ consensus), supply bottleneck, pipeline concentration |

## ✨ Feature highlights

- **Dual-track forecast** — units → ASP → revenue, with explicit FX policy (`constant` / `actual`)
- **Model zoo with honest selection** — Naive, SeasonalNaive, AutoETS, AutoARIMA, Croston-SBA, TSB, LightGBM, **Chronos-2** (default foundation model), TiRex (opt-in, licence), Ensemble; the **champion is chosen by rolling-origin backtest**, per demand segment × horizon bucket
- **Demand segmentation** — smooth / erratic / intermittent / lumpy (Syntetos-Boylan) plus seasonal and complex
- **Calibrated uncertainty** — conformal-calibrated P10/P50/P90, coverage reported
- **Commercial signal** — LightGBM win-probability, slip/delay timing distributions, Monte-Carlo, β-scaled so the uplift is *net of baseline*
- **MinT reconciliation** — grouped hierarchy, shrinkage covariance, non-negative, sample-path projection
- **Entity resolution** — deterministic rules + fuzzy matcher (Levenshtein + embeddings via pgvector + kNN); look-alikes ("Dell'Orto" vs "Dell") go to a **steward review queue**, never auto-merged
- **Governance** — planning cycles `OPEN → FORECASTED → CONSENSUS → LOCKED`, frozen versions, FVA vs AI **and** vs naive with bootstrap CI
- **Production shape** — async API, Celery workers, DB-stored settings/rules/thresholds, RBAC with row-level scope, OIDC-ready auth, DQ gate, drift monitors, Prometheus `/metrics`

## 🚀 Quickstart

### Option A — Docker (recommended)

```bash
cp .env.example .env                     # set JWT_SECRET
# lean image (no torch, Chronos-2 shows as UNAVAILABLE) — fastest to build:
WITH_FOUNDATION=0 ENABLE_CHRONOS=false docker compose up --build
# full image (torch + Chronos-2, larger/slower build):
docker compose up --build
```

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API + Swagger docs | http://localhost:8000/docs |
| Prometheus metrics | http://localhost:8000/metrics |

**First run — seed the demo (≈2–3 min, fast mode):**

1. Open http://localhost:3000 and sign in as **`admin@demo.local` / `demo1234`**
2. **Admin → Jobs** → choose `seed_demo`, keep **fast** ticked → **Run**
3. Watch the progress bar; when it reads `SUCCESS`, open **Executive Dashboard**

> Want the *full* model zoo (Chronos-2, ARIMA…)? Run job `run_forecast` with **fast unticked**. Several minutes on CPU.

### Option B — local dev (SQLite, no Docker)

```bash
make setup           # python 3.13 venv + deps, npm install
make api             # http://localhost:8000/docs      (terminal 1)
make web             # http://localhost:3000           (terminal 2)
```
Then seed from **Admin → Jobs** exactly as above. Jobs run in a background thread when Celery is off.

<p align="center"><img src="docs/screenshots/login.png" alt="Login landing page" width="860"/></p>

### Demo users (password `demo1234`)

`admin@` · `planner@` · `steward@` · `viewer@` · `rep.amer@` · `rep.emea@` · `rep.apac@` — all `@demo.local`. The landing page has one-click role cards (Planner, Admin, Steward, Viewer, three scoped Reps).

## 🧭 Product tour

<table>
<tr>
<td width="50%"><b>Executive Dashboard</b><br/>Consensus revenue, AI-vs-actual, backlog coverage, revenue at risk, upside (P90), realised wMAPE; trend + fan chart; top risks; segment mix.<br/><img src="docs/screenshots/dashboard.png" alt="Executive dashboard"/></td>
<td width="50%"><b>Forecast Explorer</b><br/>Pick any node of the hierarchy (<code>ALL</code> = aggregate). Actuals, P10–P90 band, AI P50, commercial uplift bars, overrides, consensus. Table view toggle.<br/><img src="docs/screenshots/explorer-node.png" alt="Forecast explorer"/></td>
</tr>
<tr>
<td><b>Detail slide-over</b><br/>Segment & ADI/CV², champion model, active SFDC opportunities (win-prob, uplift share), overrides, FVA, coverage, audit log.<br/><img src="docs/screenshots/explorer-detail.png" alt="Detail panel"/></td>
<td><b>Model Benchmark Matrix</b><br/>wMAPE / bias / interval coverage by model × segment × horizon, champion badges, UNAVAILABLE models with the reason.<br/><img src="docs/screenshots/benchmark.png" alt="Benchmark"/></td>
</tr>
<tr>
<td><b>Risk Center</b><br/>Alerts sorted by dollar impact: revenue gap, supply bottleneck, pipeline vulnerability. Acknowledge / resolve / assign.<br/><img src="docs/screenshots/risk.png" alt="Risk center"/></td>
<td><b>Governance</b><br/>Overrides (approve / withdraw), FVA with confidence intervals, cycle lock, audit log with hash-chain verification.<br/><img src="docs/screenshots/governance.png" alt="Governance"/></td>
</tr>
<tr>
<td><b>Mapping</b><br/>Steward review queue with evidence, accounts, distributor allocation editor (must total 100 %), rules, OEM identifiers & aliases.<br/><img src="docs/screenshots/mapping.png" alt="Mapping"/></td>
<td><b>Admin</b><br/>Jobs with live progress, data-quality gate, drift, DB-stored settings, users.<br/><img src="docs/screenshots/admin.png" alt="Admin"/></td>
</tr>
</table>

**UX conventions** — a banner always shows the synthetic-mode label; light/dark theme (follows OS, toggle in top bar); every chart has a legend, hover tooltip and a **table view**; status uses icon **+** label (never colour alone); errors are shown inline, never swallowed.

### A 3-minute walkthrough

1. **Dashboard** → note *Backlog Coverage* and *Revenue at Risk*.
2. **Risk Center** → open the top alert; the evidence shows coverage vs. the effective threshold.
3. **Forecast Explorer** → pick that OEM / region / product → **Details → Coverage**.
4. Log in as `planner@` → **Add override** (try +80 % without a comment → the form requires ≥ 15 characters).
5. **Governance → Overrides** → withdraw it (a new revision is appended; nothing is deleted) → **Audit log** → *Chain intact*.
6. Log in as `steward@` → **Mapping → Review queue** → approve a fuzzy match.

## 🔬 How it works

```mermaid
flowchart LR
  ERP[(ERP actuals)] --> MAP[Entity resolution<br/>Sold-To → End → OEM]
  MAP --> SEG[Segmentation]
  SEG --> BT[Rolling-origin backtest<br/>champion per segment × bucket]
  BT --> BASE[Baseline units<br/>conformal P10/P50/P90]
  CRM[(CRM snapshots)] --> COM[Commercial engine<br/>win-prob × timing, MC]
  COM -- "× β (net of baseline)" --> HYB[Hybrid units]
  BASE --> HYB
  HYB --> MINT[MinT reconciliation<br/>+ sample projection]
  MINT --> ASP[ASP engine × FX]
  ASP --> REV[Revenue]
  REV --> OVR[Overrides → consensus<br/>cycle lock → FVA]
  OVR --> RISK[Risk: coverage, capacity,<br/>concentration]
```

| Tier | Output | Key idea |
|---|---|---|
| **T1 Baseline** | units P10/P50/P90 per series | Champion chosen by backtest; segment gives only a *prior* (wins ties within 2 %) |
| **T2 Commercial** | expected incremental units | `Qty × P(win) × P(deliver in month)`, scaled by **β ∈ [0,1]** fitted on backtest residuals (β = 0 if the hybrid doesn't help) |
| **T3 Reconcile** | coherent hierarchy | MinT with shrunk covariance; fallback `mint_shrink → wls_var → ols` |
| **ASP** | revenue | `Revenue = Units × ASP`; ETS on log-ASP blended with contract prices |
| **T4 Governance** | consensus | AI baseline immutable; overrides allocate finest-first with pins |
| **T5 Risk** | alerts | Coverage = **Backlog ÷ Consensus** |

📘 **Deep dive:** [`docs/GUIDE.md`](docs/GUIDE.md) explains every concept, design decision and file. Short architecture summary: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 👥 Roles & permissions

| Capability | viewer | sales_rep | steward | planner | admin |
|---|:--:|:--:|:--:|:--:|:--:|
| Read dashboards, forecasts, risk, audit | ✅ | ✅ | ✅ | ✅ | ✅ |
| Create / withdraw overrides | – | ✅ *(own OEM/region scope)* | – | ✅ | ✅ |
| Approve overrides, lock cycles, run jobs, edit risk thresholds | – | – | – | ✅ | ✅ |
| Update alerts (ack / resolve / owner / note) | – | ✅ | – | ✅ | ✅ |
| Edit mappings, rules, OEM identifiers | – | – | ✅ | – | ✅ |
| Seed demo, edit settings, manage users | – | – | – | – | ✅ |

## 🔌 API at a glance

Base `/api/v1` · Bearer JWT · interactive docs at **/docs**. Every endpoint is `async`.

| Area | Endpoints |
|---|---|
| Public | `GET /health` · `GET /meta` · `POST /auth/login` · `GET /auth/me` · `GET /metrics` (root) |
| Read | `/runs` · `/filters` · `/dashboard` · `/forecast/explorer` · `/forecast/detail` · `/benchmark` |
| Governance | `/overrides` (+ `/review`) · `/consensus/conflicts` · `/cycles` · `/runs/{id}/lock` · `/fva` · `/audit` · `/audit/verify` |
| Risk | `/risk/alerts` · `/risk/summary` · `/risk/coverage` · `/risk/thresholds` · `POST /risk/refresh` |
| Mapping | `/mapping/accounts` · `/queue` · `/{id}/review` · `/rules` · `/oems` · `POST /run` · `/restate` |
| Admin | `/admin/jobs` · `/dq` · `/drift` · `/settings` · `/users` |

Typed frontend client: `npm run gen:types` (openapi-typescript) → `frontend/src/lib/api-schema.d.ts`; a hand-maintained mirror lives in `src/lib/types.ts`.

## ⚙️ Configuration

**Environment** (see `.env.example`)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | SQLite file | `postgresql+psycopg://user:pass@host/db` in production |
| `USE_CELERY` / `REDIS_URL` | false / localhost | Celery workers vs in-process job thread |
| `JWT_SECRET` | dev value | **Change in production** |
| `AUTH_MODE` | `local` | `oidc` + `OIDC_JWKS_URL`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_ROLE_CLAIM` |
| `ENABLE_CHRONOS` | true | Foundation model (needs the torch image) |
| `ENABLE_TIREX` | **false** | NXAI Community License — legal review first |
| `SYNTHETIC_MODE` | true | Shows the banner; labels every run |
| `CORS_ORIGINS` | localhost:3000 | Comma-separated |

**Business settings live in the database** (Admin → Settings, audit-logged): FX policy, fuzzy thresholds (auto ≥ 0.93, review ≥ 0.62), segmentation cut-offs, override deviation limit (60 %), approval requirement, champion tie tolerance, FVA significance, DQ blocking. Rules, OEM identifiers/aliases and risk thresholds are tables, not code.

> Changing a default in code does **not** change an existing database — edit the value in Admin → Settings.

## 🛠 Development

```bash
make test-fast       # unit tests (SQLite) ~15 s
make test-e2e        # seeds demo (fast) + full API/RBAC/async tests ~2.5 min
# Postgres/pgvector tests (use a free port; a local Postgres may already own 5432):
docker run -d --name pgtest -e POSTGRES_USER=oem -e POSTGRES_PASSWORD=oem -e POSTGRES_DB=oem_test -p 5544:5432 pgvector/pgvector:pg16
TEST_POSTGRES_URL=postgresql+psycopg://oem:oem@localhost:5544/oem_test pytest -m postgres
cd frontend && npm run typecheck && npm run build
```

**Repo layout**

```
backend/app/   core · models · data · mapping · ml · forecasting · governance · risk · quality · tasks · schemas · api
backend/tests/ synthetic · mapping · segmentation/metrics · reconcile · governance · commercial/risk · oidc · e2e · postgres
frontend/src/  app (pages) · components (ui, charts) · lib (api, auth, types)
docs/          GUIDE.md (full reference) · ARCHITECTURE.md · screenshots/
```

Build status: unit + e2e + OIDC (mock IdP) + Postgres tests pass; frontend `tsc` and `next build` clean; the stack was exercised end-to-end in Docker (Celery worker, full mode with real Chronos-2, browser-driven UI checks). See [`MASTER.md`](MASTER.md) for the precise verification log.

## 🩺 Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard says "No forecast run yet" | Seed it: **Admin → Jobs → seed_demo** |
| API container `unhealthy` | `docker compose logs api`; healthcheck is `/api/v1/health` |
| `Chronos-2 UNAVAILABLE` in Benchmark | Lean image or `ENABLE_CHRONOS=false`; rebuild without `WITH_FOUNDATION=0` |
| Tests can't reach Postgres on `localhost:5432` | A local Postgres is shadowing the container port — use another host port |
| Segments look different after upgrading | Stored settings win over new defaults — edit in Admin → Settings |
| macOS: silent crash importing LightGBM + torch | `KMP_DUPLICATE_LIB_OK=TRUE` is set in `app/__init__.py`; did not reproduce in testing, Docker/Linux unaffected |
| Login redirects to `/login` repeatedly | Token expired (8 h) or storage blocked; sign in again |

## ⚠️ Honest limitations

- **Single tenant.** Multi-tenancy is roadmap. **OIDC** passes tests against a *mock* identity provider only; the UI has no IdP redirect flow yet (local login only).
- **Synthetic data.** Model rankings and wMAPE are properties of the generator; never present them as customer evidence. With 36 months of history, long-horizon backtests have few folds (h=12 → 3).
- **TiRex** is opt-in because of its licence; **Chronos-2** (Apache-2.0) is the default foundation model.
- **Fuzzy mapping** cannot separate true name look-alikes — by design they go to human review (auto-apply ≥ 0.93).
- **Probabilistic bands:** node P50 is the coherent point forecast; P10/P90 are *marginal* quantiles per node (not additive across nodes).
- Full-mode forecasts take minutes on CPU; production sizing/GPU is not benchmarked.

## 🗺 Roadmap

Multi-tenancy · OIDC code-flow in the UI · real SAP/Salesforce connectors · sentence-transformer embeddings (`EMBEDDER=st:<model>`) · scenario planning (what-if) · notifications (email/Slack) for alerts · model-registry promotion workflow.

---

<div align="center"><sub>Built with FastAPI · SQLAlchemy · statsforecast · hierarchicalforecast · LightGBM · Chronos · Next.js · Recharts</sub></div>
