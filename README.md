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

### Prerequisites

| For | You need |
|---|---|
| **Docker path** (recommended) | Docker Desktop / Engine with Compose v2. Give Docker **≥ 6 GB RAM** for the full (Chronos-2) image; the lean image needs far less. Internet access on first build (and, for Chronos-2, on first use to download the model weights). |
| **Local path** | Python **3.13** (3.11+ may work; 3.14 lacks wheels for parts of the ML stack), Node **20+**, `make`. |

### Option A — Docker (recommended)

```bash
git clone <this repo> && cd <this repo>
cp .env.example .env                     # then set JWT_SECRET to a long random string
# lean image (no torch; Chronos-2 shows as UNAVAILABLE) — fastest build:
WITH_FOUNDATION=0 ENABLE_CHRONOS=false docker compose up --build
# full image (torch + Chronos-2) — bigger, slower build:
docker compose up --build
```

Wait until `docker compose ps` shows every service `healthy`/`Up` (API takes ~30 s after start: it applies the database migration, then serves).

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API + Swagger docs | http://localhost:8000/docs |
| Prometheus metrics | http://localhost:8000/metrics |

> The browser talks to the API at `PUBLIC_API_URL` (default `http://localhost:8000`), which is **baked into the frontend at build time** — change it in `.env` and rebuild if you deploy on another host.

### Option B — local dev (Postgres in Docker, app on your machine)

PostgreSQL + pgvector is the only supported database. Run just the database in Docker and the app natively:

```bash
docker compose up -d postgres redis      # DB on :5432 (stop any local Postgres that owns that port first)
make setup                               # python venv + deps (incl. torch/Chronos), npm install
export DATABASE_URL=postgresql+psycopg://oem:oem@localhost:5432/oem
make api                                 # terminal 1 → http://localhost:8000/docs
make web                                 # terminal 2 → http://localhost:3000
```
Jobs run in a background thread when Celery is off, so Redis is optional locally.

### 🌱 Load data (required on first run)

Everything lives in a **workspace** — an isolated research context with its own data, mappings, forecasts, overrides and audit log (one PostgreSQL schema each). A fresh install creates one empty workspace, **Synthetic demo**. Pick a data source per workspace:

| Workspace type | What goes in | How |
|---|---|---|
| **Synthetic demo** | Generated ERP + CRM + backlog + capacity estate with known ground truth | *Data → Generate* (below) |
| **Your own data** | Your sales-history file (+ optional customer→OEM mapping, backlog, capacity) | *Data → Import wizard* — [details](#-bring-your-own-data) |
| **M5 benchmark** | Walmart retail data (store → OEM, state → region, department → product) | *Data → Load M5* — [details](#-m5-benchmark) |

Seeding generates a deterministic synthetic company, maps it to OEMs, replays past planning cycles (so FVA has history), forecasts the current cycle and computes risk.

**What it creates** (same every time, `seed=42`): ~87 ERP accounts (direct, distributors, look-alike decoys) · 36 months of history for ~120 OEM×Region×Product series · ~2,400 CRM opportunities with monthly snapshots · backlog, capacity, contracts, FX · simulated rep overrides in the past cycles · a current-cycle forecast with risk alerts. Currency USD, units in kunits.

**Way 1 — in the UI** (works for Docker and local):
1. Open http://localhost:3000 → click the **Admin** role card (`admin@demo.local` / `demo1234`) → **Sign in**
2. The app opens on the empty **Synthetic demo** workspace → **Generate synthetic data** → choose *Quick* → **Generate data**
3. Watch the progress bar and message (*Generating… → Mapping… → Forecast cycles… → Risk…*). **Quick takes ≈ 2–3 minutes** (full ≈ 10+ min). When it reads **Done**, open **Executive Dashboard**.

**Way 2 — command line, no server** (local path; writes to the same Postgres):
```bash
cd backend && . .venv/bin/activate
PYTHONPATH=. python -m app.cli seed                 # fast (≈2–3 min)
PYTHONPATH=. python -m app.cli seed --full          # full model zoo, 6 replay cycles (slow)
PYTHONPATH=. python -m app.cli seed --workspace my-second-demo   # seed into another (auto-created) workspace
PYTHONPATH=. python -m app.cli workspaces           # list workspaces
```
Inside Docker: `docker compose exec api python -m app.cli seed`

**Way 3 — HTTP API** (scripts/CI):
```bash
TOKEN=$(curl -s localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
        -d '{"email":"admin@demo.local","password":"demo1234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
# every data call is scoped to a workspace by the X-Workspace header (omitted = oldest workspace)
curl -s -X POST localhost:8000/api/v1/admin/jobs -H "Authorization: Bearer $TOKEN" -H 'X-Workspace: synthetic-demo' -H 'content-type: application/json' \
     -d '{"job_type":"seed_demo","params":{"fast":true,"replay_cycles":2}}'
curl -s localhost:8000/api/v1/admin/jobs -H "Authorization: Bearer $TOKEN" -H 'X-Workspace: synthetic-demo'   # poll: PENDING → RUNNING → SUCCESS
```

**Check it worked**
- Data shows **Done** (Admin → Jobs: `seed_demo` = **SUCCESS**); the top bar run selector shows a `CURRENT` run for *Sep 26*.
- Dashboard shows roughly **$490 M** consensus revenue over 12 months, a few dozen risk alerts, and 5 OEMs (APPLE, BOSCH, DELL, SIEMENS, TOYOTA) in the Explorer filters.
- Mapping → Review queue holds a few fuzzy matches for you to approve (the look-alike decoys are deliberately *not* auto-merged).
- `curl localhost:8000/api/v1/meta` returns a non-null `current_run_id`.

**Notes**
- **Re-seeding** wipes and regenerates all data **of that workspace only** (users, job history and other workspaces are untouched).
- **Fast vs full:** fast uses Naive, SeasonalNaive, AutoETS, Croston-SBA, TSB and LightGBM. For the whole zoo (adds AutoARIMA, **Chronos-2**, Ensemble) run **`run_forecast`** with *fast* unticked after seeding, or `seed --full`. Chronos-2 needs the full Docker image (or `make setup`, which installs torch) and downloads weights on first use.
- Only **admin** can seed or import data; planners can run `run_forecast`.
- Seeding is CPU-bound; on a laptop the progress bar can sit on "Backtesting" for a minute — that is normal.
- If the job **FAILS**, expand the error in Data → progress panel or Admin → Jobs; common causes are listed under [Troubleshooting](#-troubleshooting).

<p align="center"><img src="docs/screenshots/login.png" alt="Login landing page" width="860"/></p>

### Demo users (password `demo1234`)

`admin@` · `planner@` · `steward@` · `viewer@` · `rep.amer@` · `rep.emea@` · `rep.apac@` — all `@demo.local`. The landing page has one-click role cards (Planner, Admin, Steward, Viewer, three scoped Reps).

### Stop / reset

```bash
docker compose down        # stop, keep data (Postgres volume survives)
docker compose down -v     # stop AND delete all data (fresh install; re-create/seed workspaces afterwards)
```

## 🗂 Workspaces

A **workspace** = one dataset + everything derived from it (mappings, forecast runs, planning cycles, overrides, FVA, risk alerts, settings, audit chain). Use one per research question, client or experiment — nothing leaks between them.

- **Switch** from the top bar; **create / archive / delete** on the *Workspaces* page (planners create, admins delete).
- Technically each workspace is its own **PostgreSQL schema** (`ws_<slug>`); users and the workspace registry are shared. The API selects the schema per request from the `X-Workspace` header. Deleting a workspace is a `DROP SCHEMA`.
- **Pages adapt to the data.** The workspace reports its *capabilities* (sales, CRM, backlog, capacity, contracts). Missing data switches the dependent feature off with an explanation instead of failing: no CRM → no commercial uplift or pipeline-vulnerability alerts; no backlog → no coverage/revenue-gap alerts; no capacity → no supply-bottleneck alerts; every customer is its own OEM → mapping review stays empty.
- Workspace types: **Synthetic**, **Custom data**, **M5** (label in the top bar says which).

## 📥 Bring your own data

*Workspaces → New workspace → "Your own data"* → **Data → Import**:

1. **Upload** your sales-history file (CSV or Parquet, ≤ 800 MB; bigger files can sit in `backend/data/import/`). Optionally add: customer→OEM **mapping**, **backlog** snapshots, **capacity** allocation. Template downloads are on the page.
2. **Map columns** — headers are matched automatically (`date`/`period` → month, `client` → customer, `sku` → product, `qty` → units, `amount` → revenue, `territory` → region …); fix anything off. Only four fields are mandatory: **month, customer, product, units** plus revenue *or* price (or a constant price).
3. **Check** — shows rows, months, customers, OEMs, regions, products, series and flags problems: unreadable dates, < 18 months of history (error), < 30 (warning), negative units, too many series (limit 600 OEM×region×product), daily/weekly data (summed to months; an incomplete last month is dropped).
4. **Import** — a background job loads the data, builds the OEM hierarchy, materialises the history, runs the data-quality gate and (optionally) a first forecast. Importing **replaces** that workspace's data.

How your columns become the OEM hierarchy: no OEM column → each customer *is* an OEM · OEM/region columns → exact (one account per customer/OEM/region combination) · mapping file → distributor-style allocation (a customer can split across OEMs). Currency is USD only for now; convert upstream.

## 🧪 M5 benchmark

M5 is the public Walmart retail benchmark (daily unit sales of 3,049 items in 10 stores, 2011–2016). Good for checking forecast accuracy, MinT reconciliation and revenue conversion on data nobody generated. It has **no** distributors, pipeline, backlog or capacity, so only the forecasting, reconciliation, revenue and governance features apply.

1. Create a workspace of type **M5 benchmark**.
2. Download *M5 Forecasting – Accuracy* from Kaggle (accept the competition rules) and copy `sales_train_evaluation.csv`, `calendar.csv`, `sell_prices.csv` into **`backend/data/import/m5/`** (Docker mounts this folder into the API and worker).
3. **Data → Load M5 and forecast** (or `python -m app.cli import-m5`). Mapping: store → OEM (10), state → region (3), department → product (7) = 70 series; revenue = units × weekly sell price; the incomplete last month is dropped. Tip: *first N items* gives a quick trial.

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
| Workspaces | `GET/POST /workspaces` · `PATCH/DELETE /workspaces/{id}` (scope any other call with header `X-Workspace: <slug>`) |
| Data | `/data/status` · `/data/upload` · `/data/validate` · `POST /data/import` · `/data/clear` · `/data/templates/{role}` |

Typed frontend client: `npm run gen:types` (openapi-typescript) → `frontend/src/lib/api-schema.d.ts`; a hand-maintained mirror lives in `src/lib/types.ts`.

## ⚙️ Configuration

**Environment** (see `.env.example`)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://oem:oem@localhost:5432/oem` | PostgreSQL + pgvector (the only supported DB) |
| `IMPORT_DIR` | `backend/data/import` | Uploads and M5 files |
| `BOOTSTRAP_DEMO_WORKSPACE` | true | Create the empty “Synthetic demo” workspace on first start |
| `USE_CELERY` / `REDIS_URL` | false / localhost | Celery workers vs in-process job thread |
| `JWT_SECRET` | dev value | **Change in production** |
| `AUTH_MODE` | `local` | `oidc` + `OIDC_JWKS_URL`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_ROLE_CLAIM` |
| `ENABLE_CHRONOS` | true | Foundation model (needs the torch image) |
| `ENABLE_TIREX` | **false** | NXAI Community License — legal review first |
| `CORS_ORIGINS` | localhost:3000 | Comma-separated |

**Business settings live in the database** (Admin → Settings, audit-logged): FX policy, fuzzy thresholds (auto ≥ 0.93, review ≥ 0.62), segmentation cut-offs, override deviation limit (60 %), approval requirement, champion tie tolerance, FVA significance, DQ blocking. Rules, OEM identifiers/aliases and risk thresholds are tables, not code.

> Changing a default in code does **not** change an existing database — edit the value in Admin → Settings.

## 🛠 Development

```bash
make test-db         # once: throw-away Postgres+pgvector for tests on :5544
make test-fast       # unit + workspace/import tests ~50 s
make test-e2e        # seeds a workspace through the API + RBAC/async/import tests ~3 min
cd frontend && npm run typecheck && npm run build
```

**Repo layout**

```
backend/app/   core · models · data · mapping · ml · forecasting · governance · risk · quality · tasks · schemas · api
backend/tests/ synthetic · mapping · segmentation/metrics · reconcile · governance · commercial/risk · oidc · workspaces/import/M5 · e2e
frontend/src/  app (pages) · components (ui, charts) · lib (api, auth, types)
docs/          GUIDE.md (full reference) · ARCHITECTURE.md · screenshots/
```

Build status: unit + e2e + OIDC (mock IdP) + Postgres tests pass; frontend `tsc` and `next build` clean; the stack was exercised end-to-end in Docker (Celery worker, full mode with real Chronos-2, browser-driven UI checks). See [`MASTER.md`](MASTER.md) for the precise verification log.

## 🩺 Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard says "No forecast run yet" | Seed it (see [Seed the synthetic demo data](#-seed-the-synthetic-demo-data-required-on-first-run)) |
| `seed_demo` job FAILED | Open the error in Admin → Jobs; check `docker compose logs worker`; make sure the worker container is running (with `USE_CELERY=true` jobs run *only* in the worker) |
| Job stays PENDING forever | Worker not running / Redis unreachable: `docker compose ps`, `docker compose logs worker` |
| Login page shows an error / network failure | API not up yet (wait for `healthy`) or `PUBLIC_API_URL` wrong for your host |
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
