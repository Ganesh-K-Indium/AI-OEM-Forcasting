from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.aio import in_session, in_thread
from app.api.deps import run_or_404
from app.core.config import get_settings
from app.core.db import get_adb
from app.core.security import current_user
from app.core.settings_store import get_setting
from app.forecasting import service
from app.models.forecast import ForecastRun, ModelBenchmark
from app.models.governance import PlanningCycle
from app.models.reference import Oem, ProductLine, Region
from app.schemas.common import FilterOptions, Meta, RunOut
from app.schemas.forecast import BenchmarkOut, BenchmarkRow, DetailOut, ExplorerOut

router = APIRouter(tags=["forecast"], dependencies=[Depends(current_user)])
public = APIRouter(tags=["meta"])


@public.get("/health")
async def health(db: AsyncSession = Depends(get_adb)):
    await db.execute(select(1))
    return {"status": "ok"}


def _meta(s):
    cfg = get_settings()
    run = service.latest_run(s)
    cyc = s.execute(select(PlanningCycle).where(PlanningCycle.cycle_month == run.cycle_month)).scalar_one_or_none() if run else None
    syn = bool(run.is_synthetic) if run else cfg.synthetic_mode
    return Meta(synthetic_mode=syn, label="[SYNTHETIC DEMO MODE]" if syn else "PRODUCTION DATA", current_run_id=run.id if run else None,
                cycle_month=run.cycle_month if run else None, cycle_status=cyc.status if cyc else None, horizon=cfg.horizon, fx_policy=get_setting(s, "fx_policy"), version="1.0.0")


@public.get("/meta", response_model=Meta)
async def meta(db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _meta)


@router.get("/runs", response_model=list[RunOut])
async def runs(kind: str | None = None, limit: int = 30, db: AsyncSession = Depends(get_adb)):
    q = select(ForecastRun).order_by(ForecastRun.cycle_month.desc(), ForecastRun.created_at.desc()).limit(limit)
    if kind:
        q = q.where(ForecastRun.kind == kind)
    return list((await db.execute(q)).scalars())


@router.get("/filters", response_model=FilterOptions)
async def filters(db: AsyncSession = Depends(get_adb)):
    oems = [c for (c,) in (await db.execute(select(Oem.code).order_by(Oem.code))).all()]
    regs = [c for (c,) in (await db.execute(select(Region.code).order_by(Region.code))).all()]
    prods = (await db.execute(select(ProductLine).order_by(ProductLine.code))).scalars().all()
    return FilterOptions(oems=oems, regions=regs, products=[dict(code=p.code, name=p.name, family=p.family) for p in prods], horizons=list(range(1, get_settings().horizon + 1)))


def _dashboard(s, run_id):
    return service.dashboard(s, run_or_404(s, run_id))


@router.get("/dashboard")
async def dashboard(run_id: str | None = None):
    return await in_thread(_dashboard, run_id)


def _explorer(s, run_id, oem, region, product):
    try:
        return service.explorer(s, run_or_404(s, run_id), oem, region, product)
    except service.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/forecast/explorer", response_model=ExplorerOut)
async def explorer(run_id: str | None = None, oem: str = "ALL", region: str = "ALL", product: str = "ALL"):
    return await in_thread(_explorer, run_id, oem, region, product)


def _detail(s, run_id, oem, region, product):
    try:
        return service.detail(s, run_or_404(s, run_id), oem, region, product)
    except service.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/forecast/detail", response_model=DetailOut)
async def detail(run_id: str | None = None, oem: str = "ALL", region: str = "ALL", product: str = "ALL"):
    return await in_thread(_detail, run_id, oem, region, product)


def _benchmark(s, run_id):
    run = run_or_404(s, run_id)
    rows = s.execute(select(ModelBenchmark).where(ModelBenchmark.run_id == run.id)).scalars().all()
    out = [BenchmarkRow.model_validate(r, from_attributes=True) for r in rows]
    champs = [dict(segment=k.split("|")[0], bucket=int(k.split("|b")[1]), **v) for k, v in ((run.summary or {}).get("champions") or {}).items()]
    notes = ["Backtest: rolling-origin expanding windows (3-month step). With 36 months of history the h=12 column rests on only 3 folds - treat it as indicative.",
             "wMAPE is pooled (sum |error| / sum actual) within each segment; bias = sum(actual - forecast)/sum(actual), so positive = under-forecast.",
             "Champion = best pooled wMAPE per segment x horizon bucket; the segment prior wins ties within the configured tolerance."]
    if run.is_synthetic:
        notes.append("[SYNTHETIC DEMO MODE] model ranking reflects synthetic data and must not be read as evidence of real-world accuracy.")
    return BenchmarkOut(run_id=run.id, rows=out, champions=champs, segments=(run.summary or {}).get("segments") or {}, backtest_origins=(run.summary or {}).get("backtest_origins") or [], notes=notes)


@router.get("/benchmark", response_model=BenchmarkOut)
async def benchmark(run_id: str | None = None, db: AsyncSession = Depends(get_adb)):
    return await in_session(db, _benchmark, run_id)
