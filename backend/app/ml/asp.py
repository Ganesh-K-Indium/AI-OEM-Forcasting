"""ASP engine: converts the operational unit forecast to revenue (Revenue = Units x ASP).
Log-ASP ETS per bottom series + contract-price blending + explicit FX policy; backtested like any other model."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import AutoETS

from app.core.calendar import add_months


@dataclass
class AspForecast:
    mean: pd.DataFrame  # index = future months, columns = bottom ids (USD per kunit)
    sigma_log: pd.DataFrame  # same shape: log-scale uncertainty
    stat_mean: pd.DataFrame  # statistical-only ASP (before contract blending)
    contract_weight: pd.DataFrame
    diagnostics: dict


def asp_history(units_wide: pd.DataFrame, rev_wide: pd.DataFrame, ffill_limit: int = 6) -> pd.DataFrame:
    """Observed ASP; months without volume are carried forward (no price signal) up to `ffill_limit` months."""
    asp = (rev_wide / units_wide.where(units_wide > 1e-9)).replace([np.inf, -np.inf], np.nan)
    asp = asp.ffill(limit=ffill_limit).bfill(limit=ffill_limit)
    return asp


def _stat_forecast(asp_wide: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """AutoETS on log(ASP); series with < 12 valid months use their trailing mean with a wider sigma."""
    idx = pd.date_range(asp_wide.index[-1] + pd.offsets.MonthBegin(1), periods=horizon, freq="MS")
    mean = pd.DataFrame(index=idx, columns=asp_wide.columns, dtype=float)
    sig = pd.DataFrame(index=idx, columns=asp_wide.columns, dtype=float)
    rows = []
    for c in asp_wide.columns:
        s = asp_wide[c].dropna()
        if len(s) >= 12:
            rows.append(pd.DataFrame({"unique_id": c, "ds": s.index, "y": np.log(s.clip(lower=1e-6)).to_numpy()}))
        else:
            last = float(s.tail(6).mean()) if len(s) else np.nan
            mean[c] = last
            sig[c] = 0.12
    if rows:
        df = pd.concat(rows, ignore_index=True)
        sf = StatsForecast(models=[AutoETS(season_length=1, model="ZZN")], freq="MS", n_jobs=1)
        fc = sf.forecast(df=df, h=horizon, fitted=True)
        fc = fc.reset_index() if "unique_id" not in fc.columns else fc
        fv = sf.forecast_fitted_values()
        fv = fv.reset_index() if "unique_id" not in fv.columns else fv
        col = [x for x in fc.columns if x not in ("unique_id", "ds")][0]
        fcol = [x for x in fv.columns if x not in ("unique_id", "ds", "y")][0]
        resid_sd = (fv.assign(r=fv["y"] - fv[fcol]).groupby("unique_id")["r"].std().fillna(0.03)).clip(lower=0.01)
        for u, g in fc.groupby("unique_id"):
            mean[u] = np.exp(g[col].to_numpy())
            h = np.arange(1, horizon + 1)
            sig[u] = np.minimum(resid_sd[u] * (1 + 0.12 * (h - 1)), 3 * resid_sd[u])
    return mean, sig


def _fx_rate_future(fx_wide: pd.DataFrame, policy: str, base_month: pd.Timestamp | None, months: pd.DatetimeIndex) -> pd.DataFrame:
    """Future FX by currency: constant -> base-month rate; actual -> last observed rate held flat."""
    if policy == "constant" and base_month is not None:
        r = fx_wide.loc[fx_wide.index <= base_month].iloc[-1]
    else:
        r = fx_wide.iloc[-1]
    return pd.DataFrame({c: [float(r[c])] * len(months) for c in fx_wide.columns}, index=months)


def contract_price_panel(contracts: pd.DataFrame, acct_map: pd.DataFrame, months: pd.DatetimeIndex, fx_wide: pd.DataFrame, policy: str,
                         base_month: pd.Timestamp | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (price_usd[months x bottom ids], committed_units[months x bottom ids]) for months covered by contracts.
    contracts: [account_id, product_code, start_month, end_month, committed_units_per_month, contract_price_local, currency, annual_price_change_pct]
    acct_map : [account_id, oem_code, region_code, pct] as-of the cycle month."""
    price, commit = {}, {}
    fx = _fx_rate_future(fx_wide, policy, base_month, months)
    for c in contracts.itertuples(index=False):
        for am in acct_map[acct_map.account_id == c.account_id].itertuples(index=False):
            bid = f"{am.oem_code}|{am.region_code}|{c.product_code}"
            for m in months:
                s, e = pd.Timestamp(c.start_month), pd.Timestamp(c.end_month)
                if s <= m <= e:
                    steps = max(0, (m.year - s.year) * 12 + m.month - s.month) // 12
                    p_local = c.contract_price_local * (1 + c.annual_price_change_pct) ** steps
                    price.setdefault(bid, {})[m] = p_local * float(fx.loc[m, c.currency])
                    commit.setdefault(bid, {})[m] = commit.get(bid, {}).get(m, 0.0) + c.committed_units_per_month * am.pct
    return pd.DataFrame(price).reindex(months), pd.DataFrame(commit).reindex(months)


def forecast_asp(asp_wide: pd.DataFrame, horizon: int, contract_price: pd.DataFrame | None = None, committed_units: pd.DataFrame | None = None,
                 baseline_units: pd.DataFrame | None = None) -> AspForecast:
    mean, sig = _stat_forecast(asp_wide, horizon)
    w = pd.DataFrame(0.0, index=mean.index, columns=mean.columns)
    out = mean.copy()
    if contract_price is not None and not contract_price.empty and committed_units is not None and baseline_units is not None:
        for c in contract_price.columns.intersection(mean.columns):
            cov = (committed_units[c].reindex(mean.index) / baseline_units[c].reindex(mean.index).clip(lower=1e-6)).clip(0, 1).fillna(0.0)
            has = contract_price[c].reindex(mean.index).notna()
            w[c] = np.where(has, cov, 0.0)
            out[c] = np.where(has, w[c] * contract_price[c].reindex(mean.index) + (1 - w[c]) * mean[c], mean[c])
            sig[c] = sig[c] * (1 - 0.7 * w[c])  # contract-covered volume has little price risk
    return AspForecast(out, sig, mean, w, {"n_series": int(mean.shape[1]), "contract_series": int((w.sum() > 0).sum())})


def backtest_asp(asp_wide: pd.DataFrame, units_wide: pd.DataFrame, horizon: int = 6, min_train: int = 18, step: int = 3, max_origins: int = 4) -> dict:
    """Volume-weighted ASP error on realised months (rolling origin). Returns pooled and per-horizon wMAPE."""
    T = len(asp_wide)
    origins = list(range(min_train, T - 1, step))[-max_origins:]
    num = {h: 0.0 for h in range(1, horizon + 1)}
    den = {h: 0.0 for h in range(1, horizon + 1)}
    for o in origins:
        mean, _ = _stat_forecast(asp_wide.iloc[:o], min(horizon, T - o))
        for h, m in enumerate(mean.index, start=1):
            real = asp_wide.iloc[o + h - 1]
            u = units_wide.iloc[o + h - 1]
            ok = real.notna() & mean.loc[m].notna() & (u > 0)
            num[h] += float((u[ok] * (real[ok] - mean.loc[m][ok]).abs()).sum())
            den[h] += float((u[ok] * real[ok]).sum())
    per_h = {h: (num[h] / den[h] if den[h] else None) for h in num}
    tn, td = sum(num.values()), sum(den.values())
    return {"wmape": tn / td if td else None, "by_horizon": per_h, "origins": len(origins)}
