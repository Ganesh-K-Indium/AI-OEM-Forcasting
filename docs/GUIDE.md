# Complete Reference Guide — OEM Revenue Forecasting Platform

*Personal reference: what was built, why each decision was made, and the concepts behind it. Read top-to-bottom once; use the table of contents afterwards.*

## Contents
1. [The business problem in plain language](#1-the-business-problem-in-plain-language)
2. [Vocabulary you need](#2-vocabulary-you-need)
3. [The big picture & decisions we made](#3-the-big-picture--decisions-we-made)
4. [Data model (35 tables)](#4-data-model-35-tables)
5. [Synthetic data generator](#5-synthetic-data-generator)
6. [Entity resolution: Sold-To → End Customer → OEM](#6-entity-resolution-sold-to--end-customer--oem)
7. [Tier 1 — Baseline units](#7-tier-1--baseline-units)
8. [Tier 2 — Commercial (CRM) signal](#8-tier-2--commercial-crm-signal)
9. [Tier 3 — MinT reconciliation](#9-tier-3--mint-reconciliation)
10. [ASP engine: units → revenue](#10-asp-engine-units--revenue)
11. [Tier 4 — Governance: overrides, consensus, cycles, FVA, audit](#11-tier-4--governance)
12. [Tier 5 — Backlog & risk](#12-tier-5--backlog--risk)
13. [The pipeline, step by step](#13-the-pipeline-step-by-step)
14. [Async architecture](#14-async-architecture)
15. [Security & RBAC](#15-security--rbac)
16. [Data quality, drift, jobs, registry](#16-data-quality-drift-jobs-registry)
17. [Frontend](#17-frontend)
18. [Docker, CI, operations](#18-docker-ci-operations)
19. [Testing — what exists, what it proved](#19-testing--what-exists-what-it-proved)
20. [Bugs we hit and what they taught](#20-bugs-we-hit-and-what-they-taught)
21. [Limitations, risks, roadmap](#21-limitations-risks-roadmap)
22. [Cookbook: how do I…?](#22-cookbook-how-do-i)
23. [File map](#23-file-map)
24. [Glossary & FAQ](#24-glossary--faq)
25. [Workspaces and bring-your-own-data](#25-workspaces-and-bring-your-own-data)

---

## 1. The business problem in plain language

You sell **components** (connectors, displays, microcontrollers, memory, power ICs/modules, RF) to **OEMs** — the big brands that build the end product (Apple, Bosch, Dell, Siemens, Toyota in the demo). Finance and supply planning need to know, **each month for the next 12**, how much of each product will ship to each OEM in each region, and what revenue that makes.

Why that is hard:

1. **The ERP doesn't know the OEM.** Invoices go to a *Sold-To* — sometimes the OEM's own entity ("Apple Operations Europe Ltd"), sometimes a **distributor** who resells to several OEMs. You must reconstruct "who is the real OEM, in which home region?".
2. **History is not the whole story.** Past sales repeat (seasonality, trend), but *new wins and delays* in the CRM pipeline change the future. If you add the whole pipeline on top of a baseline that already embeds the average effect of past pipeline, you **double count**.
3. **Revenue ≠ units.** Price (ASP), contracts, and FX move revenue independently of volume.
4. **Numbers must add up.** A forecast per region, per OEM, per product and in total are usually produced separately and disagree.
5. **Humans override the model.** Sometimes they are right (they know a project slipped), often they are biased. You need to *measure* whether overrides add value, and keep a tamper-evident record.
6. **Planning is about exposure.** "Do we have enough backlog behind next quarter's number? Is the factory capable? Does one early-stage deal carry the whole upside?"

The platform answers all six, end to end, on synthetic data (so it runs without SAP/Salesforce).

## 2. Vocabulary you need

| Term | Meaning here |
|---|---|
| **OEM** | Original Equipment Manufacturer — the end brand you ultimately supply |
| **Home Region** | The OEM's region of consumption/ownership (AMER, EMEA, APAC), not necessarily where the invoice goes |
| **Sold-To** | The ERP customer account on the invoice (may be a distributor) |
| **Distributor** | Sold-To that resells to several OEMs; mapping uses **allocation %** (must sum to 100 %) |
| **End customer** | The real buyer if the ERP records it; takes precedence over the Sold-To mapping |
| **Series** | One OEM × Region × Product monthly time series (≈120 in the demo) |
| **kunits** | Thousands of units (all unit figures) |
| **ASP** | Average Selling Price per unit; here per kunit in USD |
| **ERP actuals** | Historical shipped units/revenue (the baseline's training data) |
| **CRM / SFDC** | Salesforce-style opportunity pipeline (stages 1–5, won=6, lost=7) |
| **Snapshot** | Monthly freeze of every opportunity's state — enables *point-in-time* training |
| **Backlog** | Confirmed open purchase orders scheduled for future delivery months |
| **Coverage** | **Backlog ÷ Consensus** forecast for a future month (higher = safer) |
| **Baseline** | Statistical/ML forecast from ERP history only |
| **Uplift** | Expected *incremental* units from the CRM pipeline, net of what the baseline already contains |
| **Hybrid** | Baseline + β · uplift |
| **β (beta)** | Fraction of gross pipeline uplift that is genuinely incremental (0–1), learned from backtests |
| **Reconciliation** | Adjusting forecasts at all hierarchy levels so children sum to parents |
| **MinT** | Minimum Trace reconciliation (Wickramasuriya et al.) — statistically optimal linear reconciliation |
| **wMAPE** | Σ|actual − forecast| ÷ Σ actual (pooled; robust to zeros) |
| **Bias** | Σ(actual − forecast) ÷ Σ actual → **positive = under-forecast** |
| **P10/P50/P90** | 10th/50th/90th percentile of the predictive distribution; P10–P90 is an 80 % interval |
| **Conformal calibration** | Post-hoc widening/narrowing of intervals so empirical coverage matches the target |
| **Champion** | Model that wins the backtest for a segment × horizon bucket |
| **FVA** | Forecast Value Added: does a step (AI vs naive, override vs AI) reduce error? |
| **Consensus** | Final plan = immutable AI baseline + approved overrides |
| **Cycle** | Monthly planning round with status OPEN→FORECASTED→CONSENSUS→LOCKED |
| **ADI / CV²** | Average inter-Demand Interval / squared coefficient of variation of non-zero demand — the Syntetos-Boylan segmentation axes |

## 3. The big picture & decisions we made

```
ERP actuals ─► Entity resolution ─► MappedSeries (OEM×Region×Product×Month, units + USD)
                                          │
                       Segmentation ◄─────┤
                       Backtest (rolling origin) ─► champion per segment×bucket + conformal
                       Baseline units (P10/P50/P90) ─┐
CRM snapshots ─► Commercial engine (win × timing, MC) ─ × β ─► Hybrid units
                                                     │
                       MinT (point + samples) ◄──────┘
                       ASP engine (× FX) ─► Revenue
                       Overrides ─► Consensus ─► Lock ─► FVA
                       Risk engine (coverage / capacity / concentration)
```

### The decisions, and *why*

| # | Decision | Why |
|---|---|---|
| 1 | **Forecast units first, then convert to revenue with an ASP engine** | Operations (capacity, supply) think in units; price/FX/contract effects are a *different* statistical process. Forecasting revenue directly mixes both and hides the cause of errors. Gold-standard in B2B manufacturing. |
| 2 | **Sold-To → OEM chain in v1, fully data-driven** | If the target (OEM) is wrong, every downstream number is wrong. Mappings, rules, identifiers and thresholds are DB rows with audit — no business logic keyed on hard-coded names. |
| 3 | **Internal use first; no multi-tenancy** | Avoids premature complexity; customers later (documented roadmap). |
| 4 | **Chronos-2 default, TiRex opt-in** | Chronos-2 is Apache-2.0. TiRex is under the NXAI Community License → needs legal review before commercial use, so it ships *off*. |
| 5 | **Net-of-baseline uplift with point-in-time CRM features** | Prevents double counting and data leakage (training on information not known at the time). |
| 6 | **Grouped hierarchy, probabilistic MinT** | Coherent numbers at every level *and* coherent uncertainty. |
| 7 | **Immutable AI baseline; append-only overrides; hash-chained audit** | Audit/governance: you can always show what the model said, who changed it, and prove the log wasn't edited. |
| 8 | **Coverage = Backlog ÷ Consensus** | The draft inverted it; backlog-over-forecast is the standard (≥ 1 means fully covered). |
| 9 | **Segmentation gives a *prior*; the backtest picks the champion** | Textbook rules (e.g. "use Croston for intermittent") are only hypotheses on *your* data. Prior wins only when models are within 2 %. |
| 10 | **All endpoints async** | Production requirement: the web edge never blocks on DB or crypto; heavy compute is off the request path. |
| 11 | **Honesty by design** | UNAVAILABLE models are shown with a reason; backtest-fold caveat is displayed; a banner marks every synthetic workspace. |

## 4. Data model (35 tables)

Grouped by purpose (`backend/app/models/`):

| Group | Tables | Purpose |
|---|---|---|
| **Reference** | `regions`, `oems`, `oem_identifiers`, `oem_aliases`, `product_lines`, `accounts`, `mapping_rules`, `account_oem_mappings`, `fx_rates`, `system_settings` | Master data and *all* configurable behaviour. `oem_identifiers` hold DUNS/Tax-ID/ERP-parent/domain; `account_oem_mappings` are **effective-dated** with allocation % and status (ACTIVE / PENDING_REVIEW / …). Accounts and aliases carry **embedding vectors** (pgvector on Postgres). |
| **Facts (source-grain)** | `sales_actuals`, `backlog_snapshots`, `capacity_allocations`, `opportunities`, `opportunity_snapshots`, `contracts`, `sales_reps` | What ERP/CRM would deliver. Snapshots allow *as-of* reconstruction. |
| **Materialised** | `mapped_series` | Output of the mapping layer: OEM×Region×Product×Month units + USD revenue (under the FX policy). The forecaster reads *only* this. |
| **Forecast outputs** | `forecast_runs`, `series_segments`, `forecast_points`, `uplift_details`, `model_benchmarks`, `model_registry` | A run has points at every hierarchy level, segmentation stats, per-model benchmark rows, opportunity-level uplift evidence. |
| **Governance** | `overrides`, `consensus_points`, `fva_results`, `planning_cycles`, `audit_log` | Append-only overrides; frozen consensus at lock; FVA; hash-chained log. |
| **Risk** | `risk_thresholds`, `risk_alerts` | Configurable thresholds, generated alerts with status/owner/note. |
| **Ops** | `jobs`, `dq_results`, `drift_reports`, `users` | Background jobs, data-quality gate results, drift, accounts & roles. |

Conventions: units in **kunits**; revenue **USD**; hierarchy node id = `"OEM|REGION|PRODUCT"` with literal `ALL` for aggregates.

## 5. Synthetic data generator

`backend/app/data/synthetic.py` — fully deterministic (`seed=42`): same seed ⇒ identical dataset (no wall-clock, no global RNG).

- 36 months of ERP history ending **2026-09**; **plus 12 months of hidden "latent future"** used *only* to build realistic backlog (so backlog is consistent with what will actually happen).
- ~120 series; **87 accounts** including distributors, end customers and **decoys** (e.g. "Dell'Orto SpA", "Delta Dell Logistics" that *look* like Dell but aren't).
- ~2,400 opportunities with Markov stage progression, rep bias, close-date pushing and monthly snapshots; ~14 % of units come from won projects.
- **Embedded scenarios** so the system can be checked against known truth: `steady_growth`, `flat`, `quarter_end_spike`, `annual_seasonal`, `intermittent`, `supply_bottleneck`, `pipeline_push` (over-optimistic pipeline), `trend_break`, `under_coverage`. Ground-truth tags are stored so tests can assert e.g. "the bottleneck scenario triggers a SUPPLY_BOTTLENECK alert".
- Backlog (last 18 snapshots), capacity per region×product, contracts (with annual price change), FX rates.

**Why synthetic?** To run without SAP/Salesforce *and* to have ground truth. **Caveat:** synthetic accuracy proves the plumbing, not real-world performance.

`seed_demo` (`data/seed.py`) orchestrates: generate → load → mapping → steward replay → DQ → **replay cycles** (past monthly runs with simulated rep overrides so FVA has history) → current cycle run → FVA & risk.

## 6. Entity resolution: Sold-To → End Customer → OEM

Package `backend/app/mapping/`.

**Goal:** assign each ERP row to `(OEM, Home Region)` — possibly split across OEMs for a distributor.

**Layer 1 — Deterministic rules** (`rules.py`): rule types `GLOBAL_DUNS`, `DUNS_EXACT`, `TAX_ID`, `ERP_PARENT`, `DOMAIN`, `ALIAS_EXACT`, `NAME_REGEX`; each has **priority** and **confidence**, stored in `mapping_rules` and matched against `oem_identifiers`. Lowest priority number evaluated first. Editable in the UI.

**Layer 2 — Fuzzy ML mapper** (`fuzzy.py`) for what rules can't resolve. Score = weighted blend (weights in settings: name 0.55, vector 0.25, kNN 0.20):
- **Name similarity** — token-aligned Levenshtein, tolerant to typos, *penalised by extra distinctive tokens* (generic words like "operations", "europe", "holdings" are ignored via a settings list). "Apple Operations Europe Ltd" ≈ Apple; "Applegate Industrial Supply" is not.
- **Embedding cosine** — default embedder is deterministic **feature-hashing of character n-grams + words** (no network, no model download, reproducible). pgvector `<=>` on Postgres; numpy elsewhere. Swappable: `EMBEDDER=st:<sentence-transformer>`.
- **kNN over already-mapped accounts** — accounts that look like previously approved ones inherit their mapping.

**Thresholds** (DB settings): score **≥ 0.93** auto-activates; **0.62–0.93** goes to the **steward review queue**; below → unmapped. *Why:* silently merging a look-alike corrupts forecasts invisibly; a human queue is cheap by comparison. In the demo the two Dell decoys correctly land in review.

**Distributors** need **allocation %** across OEMs; the editor refuses anything not totalling 100 %. **End-customer mapping beats sold-to mapping** (more specific).

**Effective dating:** mappings have `valid_from/valid_to`; each fact row is resolved **as of its own month**, and "restate" re-materialises history after a mapping change. *Why:* a distributor's customer mix changes; restating prevents rewriting history wrongly or leaving it stale.

`materialize_mapped_series` converts to USD under the FX policy: `constant` (all months at the base-month rate → removes FX noise from the *volume/price* story) or `actual` (each month's rate). Unmapped remainder is kept as `UNMAPPED` and excluded from forecasting; the DQ gate reports its share (≈1.2 % after replay in the demo).

## 7. Tier 1 — Baseline units

Package `backend/app/ml/`.

### 7.1 Segmentation (`segmentation.py`)
Per series compute:
- **ADI** (average gap between non-zero demands) and **CV²** (variability of non-zero demand). Cut-offs **1.32** and **0.49** (Syntetos-Boylan): *smooth* (low/low), *erratic* (low ADI, high CV²), *intermittent* (high ADI, low CV²), *lumpy* (high/high).
- **Seasonal strength** from STL decomposition: Fs = max(0, 1 − Var(R)/Var(S+R)).
- **ACF(12)** — autocorrelation at lag 12.
- **Exogenous score** — max |corr| between differenced demand and (lagged) CRM drivers.

Order of tests: ADI ≥ cut → lumpy/intermittent; else exogenous score ≥ 0.55 → **complex**; else seasonal (needs ≥ 30 obs, Fs ≥ 0.70 **and** ACF(12) ≥ 0.25) → **seasonal**; else erratic/smooth.

*Why the extra ACF check?* Our own test found that STL on ~36 noisy points gives seasonal strength ≈ 0.46 median and ≈ 0.68 at the 90th percentile for pure white noise (STL overfits). Requiring autocorrelation at lag 12 as corroboration removed false "seasonal" labels. Thresholds are DB settings (`segment_seasonality_min`, `segment_seasonal_acf_min`); remember stored settings override new code defaults.

The segment is a **prior** (which models are *expected* to do well), not a decision.

### 7.2 Models (`ml/models/`, all behind `BaseForecastModel`)
| Family | Models | Notes |
|---|---|---|
| Naive | Naive, SeasonalNaive | The honesty benchmark every other model must beat |
| Statistical (StatsForecast) | AutoETS, AutoARIMA, CrostonSBA, TSB | Croston-SBA/TSB are for intermittent demand |
| ML | **LightGBM** | One *global* model across series; **direct multi-horizon** (horizon is a feature); scale-normalised target; lag/rolling/calendar features; separate quantile models for P10/P90 |
| Foundation | **Chronos-2** (default), TiRex (opt-in) | Zero-shot pretrained; degrade gracefully via `ModelUnavailable` if weights/licence/torch missing |
| Combination | **Ensemble** | Mean of the top 3 models by backtest |

*Fast mode* (used for quick seeds/tests) runs Naive, SeasonalNaive, AutoETS, Croston-SBA, TSB, LightGBM. *Full mode* adds ARIMA, Chronos-2 and the ensemble.

### 7.3 Rolling-origin backtest (`backtest.py`)
Expanding windows: minimum 18 months of training, step 3 months; at each origin forecast the next 12 months and compare with actuals. Horizons are bucketed **1–3, 4–6, 7–12**; the benchmark UI shows h = 1/3/6/12. Metrics: wMAPE, MAE, MASE, bias, pinball loss, 80 % coverage.

*Why rolling-origin?* A single train/test split depends on one lucky/unlucky period and ignores that accuracy decays with horizon. **Caveat:** 36 months give only ~3 folds at h = 12, so long-horizon rankings are indicative — the UI says so.

### 7.4 Champion selection
Per **segment × horizon bucket**: best pooled wMAPE wins; the **segment prior wins if within 2 %** (`champion_prior_tolerance`). *Why:* prevents flip-flopping on noise and keeps domain knowledge as tie-breaker.

### 7.5 Conformal calibration (CQR)
Raw quantile models are usually over- or under-confident. **Conformalized Quantile Regression** uses backtest errors to adjust the P10/P90 so that empirical coverage hits the 80 % target. Coverage is reported per model in the benchmark (e.g., Chronos-2 ≈ 84 % in our full run).

### 7.6 Sign conventions to remember
bias = Σ(actual − forecast)/Σ actual → **positive means the forecast was too low**.

## 8. Tier 2 — Commercial (CRM) signal

`backend/app/ml/commercial.py`.

**Formula**

`E[uplift units in month m] = Σ_i  Qty_i · f_rep · P(win_i | stage, age, push-count, rep accuracy…) · P(deliver in m | slip(stage), delay(product), ramp)`

Components:
- **Win model** — LightGBM classifier trained on closed opportunities, with **isotonic calibration** so 0.7 really means ~70 %. Features include stage, months-in-stage, push count, quote issued, amount, and **rep point-in-time stats** (historical win rate/bias of the rep *as of that month*).
- **Point-in-time discipline** — every feature and every distribution uses only snapshots with `snapshot_month ≤ as-of`. Training on end-state data would leak the answer (an opportunity that eventually closed-won *looks* different from the start).
- **Timing** — empirical **slip(stage)** (how far close dates actually move) and **delay(product)** (close → first delivery lead time); a ramp over `ramp_months`. Samples drawn by **Monte-Carlo** → distribution of uplift per series/month.
- **Rep quantity-realisation factor** `f_rep` — reps' quoted quantities are not always realised.
- **Net-of-baseline factor β** (`estimate_net_factor`): history already contains the *average* effect of past pipeline, so adding gross uplift double counts. β ∈ [0, 1] is estimated **per horizon bucket** from backtest residuals ("how much of the pipeline uplift explains what the baseline missed?"). **If the hybrid doesn't improve backtest error, β = 0** — the system refuses to add noise.

Output per series: gross uplift, net uplift (β-scaled), per-opportunity evidence (`uplift_details`) shown in the Detail panel (win-prob, rep-prob, expected units, *share of uplift* — which also feeds the concentration risk).

## 9. Tier 3 — MinT reconciliation

`ml/hierarchy.py`, `ml/reconcile.py`.

**Hierarchy:** Total → Region → OEM → Product, plus **OEM×Region** and **Product** aggregates (a *grouped* hierarchy because OEM and Product cross-cut). Levels: `TOTAL, REGION, OEM, PRODUCT, OEM_REGION, BOTTOM`. `(OEM, ALL, Product)` is **not** a node (invalid).

**Why reconcile?** Forecasts produced independently at each level don't add up. Simple bottom-up discards good top-level information; top-down ignores bottom detail. **MinT** finds the coherent forecasts closest to the originals with minimum total variance:

`ỹ = S (S' W⁻¹ S)⁻¹ S' W⁻¹ ŷ`

- `S` — summing matrix (which bottom series add up to each node)
- `W` — covariance of forecast errors across nodes, estimated from backtest residuals and **shrunk** (Schäfer-Strimmer) toward its diagonal because there are few observations vs many nodes
- Implementation: `hierarchicalforecast.MinTrace` as the primary engine **and** a numpy implementation used as parity check/fallback. Fallback chain **mint_shrink → wls_var → ols** if the covariance is ill-conditioned.
- **Non-negativity:** clip bottom series ≥ 0 then re-aggregate (so coherence is preserved).

**Probabilistic coherence:** generate joint sample paths for all bottom series using a **Gaussian copula** (keeps cross-series correlation) with **two-piece-normal** marginals (matches asymmetric P10/P90), then project **each sample** with the same `P = (S'W⁻¹S)⁻¹S'W⁻¹` matrix (Panagiotelis et al. 2023). Node quantiles come from the projected samples. **Note:** the node P50 is the coherent *point* forecast; P10/P90 are *marginal* sample quantiles per node, so they are **not additive** across nodes (displayed as such in the UI).

## 10. ASP engine: units → revenue

`ml/asp.py`. `Revenue = Units × ASP`.

- ASP history per series = revenue_usd ÷ units under the FX policy.
- **AutoETS on log(ASP)** (multiplicative price dynamics, always positive).
- **Contract blending:** where contracts exist, contract price (with annual price change %) is blended with the statistical ASP, **weighted by committed units ÷ baseline units** — contracts dominate only where they cover most of the volume.
- **ASP uncertainty σ** from backtest, combined with unit uncertainty for revenue bands.
- The ASP model is **backtested like any other model**.

**FX policy:** `constant` (default) converts every month at one base month's rate so forecast changes reflect volume/price, not currency; `actual` uses each month's rate.

## 11. Tier 4 — Governance

### 11.1 Overrides (`governance/overrides.py`)
- Anyone permitted (planner, sales_rep within scope) can override **any valid node**, in **UNITS or REVENUE**, with a **reason code**: `PROJECT_DELAY`, `NEW_WIN`, `CAPACITY_CAP`, `CUSTOMER_DIRECT_GUIDANCE`.
- **Never mutates the AI forecast.** Each change creates a new **revision**; the previous one becomes `SUPERSEDED`; withdrawal is itself a new revision. Nothing is deleted.
- **Guardrail:** deviation from AI P50 > **60 %** requires a comment ≥ 15 characters (setting `override_max_deviation_pct`). The UI computes the deviation live and blocks submit.
- Optional approval workflow (`override_requires_approval`): overrides enter consensus only after a planner approves.
- **Rep row-level scope:** a rep's scope (OEMs/regions) limits where they can override.

### 11.2 Consensus (`governance/consensus.py`)
Consensus = AI baseline + active overrides, **coherent by construction**. Resolution is **finest-first**:
1. A bottom-level override **pins** that leaf.
2. A coarser override (e.g., OEM×Region) is **spread across the unpinned leaves proportionally to the AI forecast** so the node total equals the override exactly and all finer decisions are preserved.
3. Infeasible combinations (e.g., parent override smaller than the sum of pinned children) are reported as **conflicts** rather than silently resolved.

### 11.3 Planning cycles (`governance/cycles.py`)
`OPEN → FORECASTED → CONSENSUS → LOCKED`. **Lock** writes **frozen `consensus_points`** (AI, consensus, naive units, ASP, who/why) and prevents change. *Why freeze?* To score forecasts later against actuals **at the same lead time** without hindsight.

### 11.4 FVA (`governance/fva.py`)
- `FVA_AI = wMAPE(naive) − wMAPE(AI)` — is the model better than naive?
- `FVA_Sales = wMAPE(AI) − wMAPE(consensus)` — did overrides help? (restricted to overridden rows)
- Computed only on **matured months** (actuals exist) and by **lead time**; sliced by OEM, region, product, user, reason code and run.
- **Bootstrap 95 % CI** (needs n ≥ 12; otherwise only n is reported) and a *significance* flag when the CI excludes 0. Positive = value added.

### 11.5 Audit log (`core/audit.py`)
Each entry stores `hash = SHA-256(previous_hash + canonical entry)`. Editing or deleting any past row breaks the chain; `GET /audit/verify` recomputes and reports the first broken id (a test tampers with a row to prove detection). On Postgres, **`pg_advisory_xact_lock`** serialises appends so concurrent writers (API workers, Celery) cannot fork the chain — verified with 6 concurrent writers × 15 entries.

## 12. Tier 5 — Backlog & risk

`risk/engine.py`. Coverage per OEM×Region×Product×delivery month for **T+1…T+3**.

1. **REVENUE_GAP** — `coverage = backlog ÷ consensus < threshold`. Threshold = **min(configured floor, historical P10 coverage for that product at that lead time)**. *Why:* short-lead products legitimately carry little backlog; using one global floor would alarm constantly. Impact = `forecast − backlog` in USD.
2. **SUPPLY_BOTTLENECK** — consensus units summed over OEMs for a region×product exceed the capacity allocation.
3. **PIPELINE_VULNERABILITY** — one unclosed **early-stage (≤ 3)** opportunity carries ≥ 70 % of the series' net uplift (and uplift is ≥ 5 % of the forecast) → the upside rests on a single deal.

Severity by dollar impact: **HIGH ≥ $1M**, **MEDIUM ≥ $250k**. Alerts have status OPEN/ACKNOWLEDGED/RESOLVED, owner and note, and are sortable by money. Thresholds are rows in `risk_thresholds` (per product/region) editable in the UI.

## 13. The pipeline, step by step

`forecasting/pipeline.py::run_forecast(session, cycle_month, kind, mode, user, horizon, progress)`:

| Progress | Step | Detail |
|---|---|---|
| — | **DQ gate** | `quality/checks.run_dq`; ERROR failures raise `DataQualityBlocked` (setting `dq_block_on_error`) |
| 3 % | Loading panel | `data_access.load_panel` → units/revenue for every hierarchy node |
| 6 % | Segmenting | Per-series ADI/CV²/STL/ACF/exog → segment |
| 10 % | Backtesting | All available models, rolling origins; scores; champions; conformal |
| 45 % | Fitting champions | Final fit per series, calibrated P10/P50/P90 |
| 60 % | ASP engine | Log-ASP ETS + contract blend |
| 68 % | Commercial engine | Train win model, compute uplift (MC), estimate β |
| 78 % | MinT | Reconcile point forecast + sample paths |
| 88 % | Persisting | `forecast_points` at all levels, benchmark rows, registry, drift |
| 95 % | Risk engine | Build consensus view, coverage table, alerts |

`kind` is `CURRENT` or `REPLAY` (historical cycles used to build FVA history). A run is `RUNNING → COMPLETED/FAILED`.

## 14. Async architecture

**Rule:** *async edge, sync core.*

- Every FastAPI route is `async def`. DB access uses SQLAlchemy **`AsyncSession`** (`postgresql+psycopg` async).
- `api/aio.py` gives two bridges:
  - `in_session(db, fn, …)` — runs a **sync service function** inside the async session via `run_sync` (I/O-bound CRUD).
  - `in_thread(fn, …)` — runs CPU-heavy pandas/numpy work (and mutations) in a **worker thread with its own sync Session** so the event loop never blocks.
- **Heavy jobs** (forecast run, seeding, mapping rebuild, FVA) are *never* in the request path: they become `Job` rows executed by **Celery** (production) or a **job thread** (dev). The UI does not poll: job state changes are pushed over Server-Sent Events (§25).
- bcrypt hashing, JWT decoding and JWKS fetching are blocking → off-loaded to threads.
- Service functions stay **plain sync** and are shared by API, workers and tests. *Why:* pandas/numpy are CPU-bound — making them `async` adds complexity without benefit; threads are the right tool.
- Startup bootstrap (tables for dev, default settings/rules/thresholds, demo users) runs under a **Postgres advisory lock** so multiple workers can't race (a real bug we hit).

## 15. Security & RBAC

- **Local auth:** bcrypt-hashed passwords, HS256 JWT (8 h). **OIDC mode:** bearer tokens verified against the IdP's **JWKS** (RS256/ES256, issuer + audience checked); first login **JIT-provisions** a user from the `email`/`sub` claim and role claim — an unknown role falls back to **viewer** (least privilege).
- **Roles:** admin (everything), planner, steward, sales_rep, viewer — see the matrix in the README. Enforced by `require_roles(...)` dependencies.
- **Row-level scope** for reps via `in_scope(user, oem, region)`.
- All state changes are audit-logged. Settings, rules, thresholds are editable only by the right roles.
- **Tested:** viewer override → 403, viewer mapping edit → 403, invalid/expired/wrong-audience/garbage tokens → 401.
- **Not done:** UI redirect flow for OIDC; secrets management; rate limiting; the demo password is for demos only.

## 16. Data quality, drift, jobs, registry

- **DQ gate** (`quality/checks.py`): checks such as negative values, FX coverage, ASP outliers, current CRM/backlog snapshots, capacity coverage for the horizon, unmapped share. ERROR-severity failures **block** a run; WARN is advisory. Results persist and appear in Admin.
- **Drift** (`quality/drift.py`): **PSI** (population stability index) of recent vs reference demand, and *live error vs backtest error* — a model that degrades in production is flagged.
- **Jobs** (`tasks/jobs.py`): `seed_demo, run_forecast, mapping_pipeline, materialize, refresh_risk, compute_fva, run_dq`; states PENDING → RUNNING → SUCCESS/FAILED with progress and message; single-flight for seed/forecast.
- **Model registry** (`model_registry`): per run, which model/version/metrics were used — supports audit and reproducibility.
- **Metrics:** Prometheus text at `/metrics` (request counts/latency).

## 17. Frontend

Next.js 14 App Router, TypeScript, Tailwind, Radix Dialog (sheets), Recharts, TanStack Query.

- **Shell:** only *Workspaces* and *Admin* are global; every other page lives inside a workspace (sidebar, top bar with workspace switcher, a banner on synthetic workspaces only), run selector (shared via `RunProvider`), cycle status, theme toggle, user menu. Unauthenticated → `/login`. Token in memory with try/catch localStorage persistence; 401 triggers logout.
- **Pages:** Workspaces (list/create/open), Data (generate · import · M5), Dashboard, Forecast Explorer (+ Detail slide-over, override form), Benchmark, Risk Center, Governance (overrides/FVA/cycles/audit), Mapping (queue/accounts/rules/OEMs), Admin (jobs/DQ-drift/settings/users).
- **Role-aware UI:** buttons for actions you cannot perform are hidden (server still enforces).
- **Types:** `lib/types.ts` mirrors Pydantic schemas; `npm run gen:types` generates the authoritative OpenAPI version.
- **Data-visualisation rules applied** (from the dataviz method): fixed-order categorical palette (never cycled), CSS custom properties with light/dark selection, thin marks, no dual axis, a legend whenever ≥ 2 series, hover tooltip + crosshair, **table view** for every chart, status = icon + text, recessive grid.
- **Fan-chart layers:** actuals (solid blue) · P10–P90 (translucent blue band) · AI P50 (dashed blue) · commercial uplift (aqua bars, *net*) · overrides (orange diamonds) · consensus (ink line).
- **Verified** by a headless browser against the live Dockerised API: all pages/tabs, override creation, steward approval, scoped rep login — zero console/HTTP errors. (One bug found this way: rendering an object as text.)

## 18. Docker, CI, operations

- **Compose services:** `postgres` (pgvector/pg16), `redis`, `api` (alembic upgrade then uvicorn, 2 workers), `worker` (Celery), `frontend` (Next standalone). Build arg `WITH_FOUNDATION=0|1` toggles torch + Chronos.
- **Healthcheck:** `/api/v1/health`.
- **Alembic:** baseline migration creates tables, the `vector` extension and HNSW indexes. In dev (`ENVIRONMENT != prod`) tables are also created at startup for convenience.
- **CI** (`.github/workflows/ci.yml`): ruff, unit tests, Postgres tests (service container), e2e, frontend typecheck/lint/build.
- **Makefile:** `setup, api, web, test-db, test-fast, test-e2e, types, build, docker-up|down|restart|reset|backend|web|logs|ps, dev-up|dev-down|dev-logs`.
- **Dev loop without rebuilds:** `docker-compose.dev.yml` bind-mounts `backend/app`, runs `uvicorn --reload` and `watchfiles` for the Celery worker, and a Next dev server in a Node container (`make dev-up`). Rebuild images only for dependency changes (`pyproject.toml` → `make docker-backend`) and production checks.
- **Tests need Postgres:** `make test-db` starts a throwaway pgvector container on port 5544; tests use schema `ws_test`.
- **Operational notes:** change `JWT_SECRET`; the DB (not code) holds settings; a locked cycle is frozen; to re-seed use Admin → Jobs (wipes domain data, keeps users and jobs).

## 19. Testing — what exists, what it proved

| Suite | What it proves |
|---|---|
| `test_synthetic` | Determinism, scenario tags, entity chain present |
| `test_mapping` | Rules, fuzzy ranking true match > look-alike, decoys not auto-applied, units conserved, effective dating |
| `test_segmentation_metrics` | Quadrants classify correctly; metric math & bias sign |
| `test_reconcile` | MinT coherence (parent = Σ children), library vs numpy parity, non-negativity, sample projection |
| `test_governance` | Override math, immutability of AI baseline, revisions, finest-first consensus, conflicts, lock, **FVA hand-computed**, audit tamper detection |
| `test_commercial_risk` | No-leakage, β behaviour, each alert type triggers |
| `test_oidc` | Mock IdP: valid token + role JIT; bad aud/iss/exp/garbage → 401 |
| `test_e2e_api` (slow) | Seed → full pipeline → every route, RBAC, **async concurrency** |
| `test_postgres` | pgvector ranking, 6 concurrent audit writers keep the chain intact |

Current: 57 passed (unit + e2e + OIDC) + 2 Postgres; frontend `tsc`/`next build` clean. Beyond tests: Docker stack, Celery seed, full-mode run with real Chronos-2, browser walkthrough.

**What testing does *not* show:** accuracy on real customer data; behaviour at production scale; a real IdP.

## 20. Bugs we hit and what they taught

| Bug | Cause | Lesson |
|---|---|---|
| Noise classified as "seasonal" | STL strength biased upward on short noisy series | Corroborate statistical features; test with pure noise |
| Segmentation fix "didn't apply" in the pipeline | DB setting (0.55) overrides the code default (0.70) | Defaults live in two places; stored settings win → added a setting + docs |
| DQ check crashed | `m.product` is a *DataFrame method* (`prod`), not the column | Use `m["product"]` |
| Risk engine crash | `dict` records accessed as attributes | Be explicit about row types |
| 60 % override rejected | Float: `(160/100−1)*100 = 60.00000000000001` | Tolerance in threshold comparisons |
| FVA missing rows | Required ≥ 3 observations to even report | Report n always; gate only the CI/significance |
| 2 uvicorn workers crashed at startup | Both inserted default settings concurrently | Advisory lock around bootstrap |
| API "unhealthy" forever | Healthcheck hit `/health` not `/api/v1/health` | Test the healthcheck |
| Seed job failed at 60 % | Seed wiped the `jobs` table that tracked itself | Don't delete your own bookkeeping |
| Detail panel blank | React cannot render an object (`commercial_model`) | Verify UI against live data, not mocks |
| macOS silent crash (LightGBM + torch) | Duplicate OpenMP runtimes | `KMP_DUPLICATE_LIB_OK=TRUE` (did not reproduce later) |

## 21. Limitations, risks, roadmap

**Limitations**
- Synthetic only; no SAP/Salesforce connectors. Rankings are not evidence.
- 36 months → few folds at long horizons; β and champions are noisy.
- Single tenant; OIDC untested with a real IdP and no UI redirect flow.
- Node P10/P90 not additive. Full mode is slow on CPU; no sizing study.
- Embeddings default to hashing n-grams (good for names, weaker semantically).
- `analytics` use pandas; DuckDB is installed but not yet used for heavy aggregation.

**Risks to watch in real deployments**
- Mapping errors silently propagate → keep the steward queue small and reviewed.
- Overriding the hard thresholds (auto ≥ 0.93) trades safety for convenience.
- Concept drift → watch the drift page and FVA by user/reason.
- Licence: do not enable TiRex commercially without review.

**Roadmap:** multi-tenancy · OIDC auth-code + PKCE in UI · real connectors · sentence-transformer embeddings · scenario what-ifs · alert notifications · model promotion workflow · GPU/scale benchmarking.

## 22. Cookbook: how do I…?

| Goal | How |
|---|---|
| Run everything | `docker compose up --build`, seed in Admin → Jobs |
| Add a new OEM | Mapping → OEM identifiers (add identifiers/aliases) → *Run mapping* → *Restate history* |
| Fix a wrong mapping | Mapping → Accounts → Edit (allocations total 100 %) → Restate |
| Add a mapping rule | Mapping → Rules → add; lower priority = earlier |
| Change coverage floor | Risk Center → thresholds table (per product/region) |
| Change override limit / FX policy / fuzzy thresholds | Admin → Settings (JSON values) |
| Lock a cycle | Governance → Cycles & lock → Lock (irreversible) |
| See if overrides help | Governance → FVA (needs matured, locked cycles) |
| Add a forecasting model | Implement `BaseForecastModel` in `ml/models/`, register in `registry.py::model_catalog` |
| Enable TiRex | `ENABLE_TIREX=true` + install extra `tirex` **after licence review** |
| Use real data | Workspaces → New workspace → *Your own data* → Data → upload file/zip/URL → map columns → Import (§25). No code changes needed. |
| Regenerate API types | `cd frontend && npm run gen:types` |
| Verify audit integrity | Governance → Audit log (badge) or `GET /api/v1/audit/verify` |

## 23. File map

```
backend/app/
  core/        config · db (per-schema engines, current_schema contextvar) · workspace · events (LISTEN/NOTIFY hub) · security · audit · settings_store · calendar
  models/      reference · facts · forecast · governance · ops   (35 tables)
  data/        synthetic · loader · seed · importer · m5
  mapping/     normalize · embeddings · vector_store · rules · fuzzy · service
  ml/          base · metrics · segmentation · backtest · hierarchy · reconcile · asp · commercial
  ml/models/   stats · lgbm · foundation · registry
  forecasting/ data_access · pipeline · service
  governance/  consensus · overrides · cycles · fva
  risk/        engine
  quality/     checks · drift
  tasks/       jobs · celery_app
  schemas/     common · forecast · risk · mapping · admin
  api/         aio · deps · auth · forecast · governance · risk · mapping · admin · workspaces · data · events
  cli.py       seed · import-m5 · workspaces
  main.py      app factory, lifespan bootstrap (starts the events hub), /metrics
backend/alembic/   env · versions/0001_initial
backend/tests/     (see §19)
frontend/src/app/  login · (app)/{workspaces,data,dashboard,explorer,benchmark,risk,governance,mapping,admin}
frontend/src/components/  ui/index.tsx · charts/{FanChart,Bars} · shell.tsx
frontend/src/lib/  api · auth · events (SSE client) · workspace-context · run-context · types · utils
docs/              GUIDE.md · ARCHITECTURE.md · screenshots/
MASTER.md          working log & verification status
```

## 24. Glossary & FAQ

**Why not forecast revenue directly?** Price/FX and volume are different processes; separating them makes errors diagnosable and lets operations use units.

**Why does β sometimes equal 0?** If adding CRM uplift does not reduce backtest error, the pipeline is already explained by history (or CRM is noisy) — the system refuses to add it.

**Why can the Ensemble beat every single model?** Errors of different models are partly independent; averaging the top three cancels some. It is the best pooled h=3 model in the full-mode synthetic run — which says nothing about real data.

**Why is Naive hard to beat at h=3?** Short-horizon demand is highly persistent; naive is a strong benchmark. That is exactly why FVA vs naive is reported.

**Why are bands "not additive"?** Quantiles of sums ≠ sums of quantiles. The P50 is made coherent; P10/P90 per node come from the sampled joint distribution.

**What does "coverage 84 %" mean for an 80 % interval?** In backtests, 84 % of actuals fell inside the P10–P90 band — slightly conservative. Conformal calibration aims for ~80 %.

**What is "point-in-time"?** Using only information that existed at the forecast date — essential to avoid leakage in the commercial model.

**What is an advisory lock?** A database-level mutex (`pg_advisory_xact_lock`) used here to serialise audit-chain appends and startup seeding across processes.

**What's frozen at lock?** AI units, consensus units, naive units, ASP, who/why — per series × month × horizon — so FVA can be computed later fairly.

**Where is the single source of truth for project status?** `MASTER.md` (checklist + verification log).

---

## 25. Workspaces and bring-your-own-data

**Why.** The platform started as one synthetic demo in one database. Real use needs several datasets side by side (your company data, a public benchmark, experiments) without mixing forecasts, overrides or audit trails. A **workspace** is that boundary.

**How isolation works (concept).** One PostgreSQL *schema* per workspace (`ws_<slug>`). Every domain table — accounts, sales, mappings, forecast runs, overrides, audit log, settings — exists once per schema. Only three tables are shared in `public`: `users` (people), `workspaces` (the registry) and `jobs` (background work, tagged with a workspace id).
*Why schemas and not a `workspace_id` column:* a column would need a filter in ~25 tables and every query; one forgotten filter leaks data between research projects. With schemas the database connection itself is pinned: `search_path = ws_x, public`, so unqualified table names resolve inside the workspace and the same code runs unchanged. Dropping a workspace is `DROP SCHEMA … CASCADE`.

**Per request (code).** `get_adb` reads `X-Workspace` (slug) → looks it up (cached ~5 s) → opens an `AsyncSession` on that workspace's engine and stores the schema in a `ContextVar` (`current_schema`). Worker threads started with `anyio` inherit it; background jobs set it from the job row (`use_workspace`). `SessionLocal()` / `AsyncSessionLocal()` read the contextvar, so no service function ever receives a "workspace" argument. Engines are cached per schema (small pools). Schema names are validated against `^ws_[a-z0-9_]{1,48}$` before they reach SQL.

**Provisioning.** `create_workspace` creates the schema, all workspace tables (`Base.metadata.create_all` limited to non-shared tables), HNSW indexes for pgvector, and the default settings, mapping rules and risk thresholds; the registry row is written last, so a workspace is never visible half-built. Alembic migrates only the shared tables; each workspace row carries `schema_version` for future per-schema upgrades.

**Capabilities.** `compute_capabilities` counts rows per source table (sales, CRM snapshots, backlog, capacity, contracts, accounts vs OEMs). The API returns it in `/meta`; the UI shows what is present/missing and explains the disabled features. The engines honour the same facts: no CRM → `no_crm_result` (zero uplift, β = 0); no backlog → no revenue-gap alerts and a `n/a` coverage KPI; no capacity → no supply alerts; the DQ gate downgrades these checks to INFO.

**Import (`app/data/importer.py`).** Required: month, customer, product, units, and revenue-or-price. Optional: region, OEM, family, a customer→OEM mapping file, backlog, capacity. Steps: read → canonicalise (dates to month starts; sub-monthly data aggregated, a partial last month dropped) → validate (≥18 months, series ≤ 600, numeric checks) → wipe the workspace → write reference data, accounts and 100 % mappings (or the supplied allocations) → `materialize_mapped_series` → DQ → optional first forecast. USD only for now (a `USD` FX row per month is created).
**M5 preset (`app/data/m5.py`).** Store → OEM, state → region, department → product; revenue = units × weekly price; processed store by store to keep memory flat.

**Bring data in: file, zip or URL.** `/data/upload` accepts CSV/TSV/Parquet or a zip (the largest csv/tsv/parquet inside is used; names are flattened, so path traversal is impossible). `/data/fetch` downloads a public http(s) URL server-side: private, loopback, link-local and reserved addresses are refused, redirects are followed manually with the check repeated per hop, and the size cap is 800 MB. Uploaded files land in `data/import/<workspace>/` (mounted into the api and worker containers) so the import job can read them.

**M5 from the UI.** `/data/m5/upload` takes the Kaggle zip or the three CSVs (only `calendar.csv`, `sales_train_evaluation.csv`/`validation`, `sell_prices.csv` are extracted); `/data/m5/fetch` takes a URL. The page ticks each file as it arrives, then *Load M5 and forecast* starts the job. The workspace must be of kind `m5`.

**Live updates (SSE).** Why not polling: every open tab would hit the API on a timer and still lag behind. Instead the API pushes. Writers call `pg_notify('oem_events', json)` inside their transaction (delivered on commit). Each API process runs one listener (`core/events.py`, a psycopg `LISTEN` connection with reconnect) that fans events to subscribers of `GET /api/v1/events` (`StreamingResponse`, keep-alive comment every 15 s, `retry: 3000`). Event types: `job` (progress/state), `data_changed` (carries the schema, so only the matching workspace refetches), `workspaces` (registry changed), `resync` (after a listener reconnect: refetch everything). The browser uses a fetch-based client (`lib/events.ts`) rather than `EventSource`, because the JWT travels in the `Authorization` header, not in a URL. `workspace-context.tsx` turns events into TanStack Query invalidations. A reverse proxy must not buffer the stream (`X-Accel-Buffering: no` is set).

**Working on the code.** `make dev-up` runs the stack with hot reload (no image rebuilds); see the README dev loop. Because the schema layout changed, an old database from before workspaces needs `make docker-reset` (drops volumes). Adding a table later requires a per-workspace migration (use `workspaces.schema_version`); new workspaces pick up new tables automatically, existing ones do not.

**Limits worth knowing.** Importing replaces a workspace's data (no merge/append yet). CRM opportunity files cannot be imported (their point-in-time snapshots need a richer wizard); CRM stays a synthetic-workspace feature for now. Mixed currencies need conversion before upload. A customer split across OEMs is only supported through the mapping file.

