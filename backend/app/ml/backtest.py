"""Rolling-origin (expanding window) backtesting, per-segment scoring, champion selection, conformal calibration."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.ml import metrics as M
from app.ml.base import ModelUnavailable, qcol
from app.ml.models.registry import ModelFactory

log = logging.getLogger(__name__)
BENCH_HORIZONS = (1, 3, 6, 12)
BUCKETS = {1: (1, 3), 2: (4, 6), 3: (7, 12)}
EXCLUDED_CHAMPIONS = {"Naive"}


def bucket_of(h: int) -> int:
    return 1 if h <= 3 else 2 if h <= 6 else 3


def rolling_origins(T: int, min_train: int = 18, step: int = 3) -> list[int]:
    """Training-set sizes (observations) of each expanding window: 18, 21, 24 ... up to T-1."""
    return list(range(min_train, T, step))


@dataclass
class BacktestResult:
    frame: pd.DataFrame  # model, unique_id, origin, h, ds, y, mean, q10, q50, q90, scale
    status: dict[str, tuple[str, str]] = field(default_factory=dict)  # model -> (OK|UNAVAILABLE|FAILED, note)
    timings: dict[str, float] = field(default_factory=dict)
    origins: list[pd.Timestamp] = field(default_factory=list)
    ensemble_members: list[str] = field(default_factory=list)


def run_backtest(long_df: pd.DataFrame, factories: dict[str, ModelFactory], horizon: int = 12, min_train: int = 18, step: int = 3,
                 availability: dict[str, tuple[bool, str]] | None = None, on_step=None) -> BacktestResult:
    """long_df: [unique_id, ds, y] on a common monthly index (months with no sales must be explicit zeros)."""
    dates = np.sort(long_df["ds"].unique())
    T = len(dates)
    origins = rolling_origins(T, min_train, step)
    piv = long_df.pivot(index="ds", columns="unique_id", values="y").loc[dates]
    rows, status, timings = [], {}, {}
    total, done = max(1, len(factories) * len(origins)), 0
    for name, fac in factories.items():
        if availability and not availability.get(name, (True, ""))[0]:
            status[name] = ("UNAVAILABLE", availability[name][1])
            continue
        t0, ok_any, err = time.time(), False, ""
        for k, o in enumerate(origins):
            if on_step:
                on_step(done / total, f"Backtesting {name} (origin {k + 1}/{len(origins)})")
            done += 1
            tr = long_df[long_df["ds"] < dates[o]]
            h = min(horizon, T - o)
            try:
                out = fac().fit(tr, "y", "ds").predict(h)
            except ModelUnavailable as e:
                status[name], err = ("UNAVAILABLE", str(e)), str(e)
                break
            except Exception as e:  # keep the platform running; record the failure honestly
                log.warning("backtest %s failed at origin %s: %s", name, o, e)
                err = f"{type(e).__name__}: {e}"
                continue
            f = out.frame.copy()
            f["model"], f["origin"] = name, pd.Timestamp(dates[o - 1])
            tmat = piv.iloc[:o]
            sc = tmat.apply(lambda c: M.mase_scale(c.to_numpy()), axis=0)
            f["scale"] = f["unique_id"].map(sc)
            rows.append(f)
            ok_any = True
        if name in status and status[name][0] == "UNAVAILABLE":
            continue
        status[name] = ("OK", "") if ok_any else ("FAILED", err)
        timings[name] = time.time() - t0
        log.info("backtest %s: %s in %.0fs", name, status[name][0], timings[name])
    if not rows:
        raise RuntimeError("no model produced backtest forecasts")
    bt = pd.concat(rows, ignore_index=True)
    act = long_df.rename(columns={"y": "actual"})[["unique_id", "ds", "actual"]]
    bt = bt.merge(act, on=["unique_id", "ds"], how="inner").rename(columns={"actual": "y"})
    return BacktestResult(bt, status, timings, [pd.Timestamp(dates[o - 1]) for o in origins])


def add_ensemble(bt: BacktestResult, members: int = 3, name: str = "Ensemble") -> BacktestResult:
    """Mean of the `members` best (pooled wMAPE) non-naive models - a robust, cheap champion candidate."""
    f = bt.frame
    cand = f[~f.model.isin(["Naive", "SeasonalNaive", name])]
    if cand.model.nunique() < 2:
        return bt
    rank = cand.groupby("model").apply(lambda g: M.wmape(g.y, g["mean"]), include_groups=False).sort_values()
    use = list(rank.index[:members])
    qc = [c for c in f.columns if c.startswith("q") and c[1:].isdigit()]
    g = cand[cand.model.isin(use)].groupby(["unique_id", "origin", "h", "ds", "y"], as_index=False)[["mean", *qc, "scale"]].mean()
    g["model"] = name
    bt.frame = pd.concat([f, g], ignore_index=True)
    bt.status[name] = ("OK", "mean of " + ", ".join(use))
    bt.ensemble_members = use
    return bt


def _agg(g: pd.DataFrame) -> dict:
    q = {0.1: g["q10"], 0.5: g["q50"], 0.9: g["q90"]}
    scaled = (g["y"] - g["mean"]).abs() / g["scale"].where(g["scale"] > 0)
    return dict(wmape=M.wmape(g.y, g["mean"]), mae=M.mae(g.y, g["mean"]), mase=float(scaled.mean()) if scaled.notna().any() else np.nan,
                bias=M.bias(g.y, g["mean"]), pinball=M.mean_pinball(g.y, q) / max(float(g.y.abs().mean()), 1e-9),
                coverage80=M.interval_coverage(g.y, g.q10, g.q90), n=len(g))


def score_backtest(bt: BacktestResult, segments: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (by_horizon[1,3,6,12 exact], by_bucket[1-3,4-6,7-12]) each with columns model, segment, key, metrics...
    pinball is normalised by mean |actual| so segments are comparable."""
    f = bt.frame.copy()
    f["segment"] = f["unique_id"].map(segments)
    by_h, by_b = [], []
    for model, gm in f.groupby("model"):
        for seg_name, gs in [("ALL", gm)] + [(s, g) for s, g in gm.groupby("segment")]:
            for h in BENCH_HORIZONS:
                g = gs[gs.h == h]
                if len(g):
                    by_h.append(dict(model=model, segment=seg_name, horizon=h, **_agg(g)))
            for b, (lo, hi) in BUCKETS.items():
                g = gs[(gs.h >= lo) & (gs.h <= hi)]
                if len(g):
                    by_b.append(dict(model=model, segment=seg_name, bucket=b, **_agg(g)))
    return pd.DataFrame(by_h), pd.DataFrame(by_b)


def select_champions(by_bucket: pd.DataFrame, priors: dict[str, list[str]], tol: float = 0.02,
                     fallback: str = "AutoETS") -> dict[tuple[str, int], dict]:
    """Champion per (segment, horizon bucket): best pooled wMAPE; the segment prior wins ties within `tol` (relative)."""
    out: dict[tuple[str, int], dict] = {}
    df = by_bucket[~by_bucket.model.isin(EXCLUDED_CHAMPIONS)]
    segs = set(df.segment.unique()) - {"ALL"}
    for seg in segs | {"ALL"}:
        for b in BUCKETS:
            g = df[(df.segment == seg) & (df.bucket == b) & df.wmape.notna()]
            if g.empty:
                continue
            best = g.loc[g.wmape.idxmin()]
            near = g[g.wmape <= best.wmape * (1 + tol)]
            prior = [m for m in priors.get(seg, []) if m in set(near.model)]
            pick = prior[0] if prior else best.model
            row = g[g.model == pick].iloc[0]
            out[(seg, b)] = dict(model=pick, wmape=float(row.wmape), best_model=best.model, best_wmape=float(best.wmape),
                                 prior_agrees=pick in priors.get(seg, []), n=int(row.n))
    return out


def champion_for(champs: dict, segment: str, h: int, fallback: str = "AutoETS") -> str:
    b = bucket_of(h)
    return (champs.get((segment, b)) or champs.get(("ALL", b)) or {}).get("model", fallback)


def champion_backtest_frame(bt: BacktestResult, segments: dict[str, str], champs: dict) -> pd.DataFrame:
    """Backtest rows produced by the champion model of each series' segment / horizon bucket (what the platform would have shipped)."""
    f = bt.frame.copy()
    f["segment"] = f["unique_id"].map(segments)
    f["champion"] = [champion_for(champs, s, h) for s, h in zip(f.segment, f.h)]
    return f[f.model == f.champion].reset_index(drop=True)


# ----------------------------------------------------------------------------- conformal (CQR) calibration
def fit_conformal(champ_bt: pd.DataFrame, target: float = 0.8, min_n: int = 30) -> dict[tuple[str, int], dict]:
    """Per (segment, bucket) multiplicative CQR adjustment q so that [q10 - q*w, q90 + q*w] attains `target` coverage."""
    cal = {}
    f = champ_bt.copy()
    f["bucket"] = f.h.map(bucket_of)
    w = np.maximum(f["mean"].to_numpy(), 0.05 * np.maximum(f.groupby("unique_id")["y"].transform("mean").to_numpy(), 1e-9))
    f["s"] = np.maximum(f.q10 - f.y, f.y - f.q90) / w
    for keyset in [(seg, b) for seg in f.segment.unique() for b in BUCKETS] + [("ALL", b) for b in BUCKETS]:
        seg, b = keyset
        g = f[(f.bucket == b) & ((f.segment == seg) if seg != "ALL" else True)]
        if len(g) < min_n:
            continue
        n = len(g)
        level = min(1.0, target * (1 + 1 / n))
        cal[keyset] = dict(q=float(np.quantile(g.s, level)), n=n, raw_coverage=float(((g.y >= g.q10) & (g.y <= g.q90)).mean()))
    return cal


def apply_conformal(frame: pd.DataFrame, segments: dict[str, str], cal: dict, level_cols=("q10", "q90")) -> pd.DataFrame:
    out = frame.copy()
    seg = out["unique_id"].map(segments)
    q = np.array([(cal.get((s, bucket_of(h))) or cal.get(("ALL", bucket_of(h))) or {"q": 0.0})["q"] for s, h in zip(seg, out["h"])])
    w = np.maximum(out["mean"].to_numpy(), 1e-6)
    out["q10"] = np.clip(out["q10"] - q * w, 0, None)
    out["q90"] = np.maximum(out["q90"] + q * w, out["mean"])
    out["q10"] = np.minimum(out["q10"], out["mean"])
    return out
