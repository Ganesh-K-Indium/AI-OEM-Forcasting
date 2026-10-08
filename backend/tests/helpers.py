"""Builds a tiny, fully-known forecast run for governance tests (2 OEMs x 2 regions x 1 product x 3 months)."""
from datetime import date

from sqlalchemy import insert

from app.api.aio import UserCtx
from app.models.forecast import ForecastPoint, ForecastRun

MONTHS = [date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)]
UNITS = {("A", "X"): 100.0, ("A", "Y"): 50.0, ("B", "X"): 80.0, ("B", "Y"): 20.0}
ASP = 1000.0


def mini_run(s, locked=False) -> str:
    s.add(ForecastRun(id="run-1", cycle_month=date(2026, 9, 1), horizon=3, kind="CURRENT", status="COMPLETED", is_synthetic=True))
    s.flush()
    rows = []

    def add(level, o, r, p, units):
        for h, m in enumerate(MONTHS, start=1):
            rows.append(dict(run_id="run-1", level=level, oem_code=o, region_code=r, product_code=p, month=m, horizon=h, units_p10=units * 0.8, units_p50=units,
                             units_p90=units * 1.2, baseline_units_p50=units, uplift_units=0.0, gross_uplift_units=0.0, asp_usd=ASP, revenue_p10=units * 0.8 * ASP,
                             revenue_p50=units * ASP, revenue_p90=units * 1.2 * ASP, model_name="AutoETS", segment="smooth"))

    for (o, r), u in UNITS.items():
        add("BOTTOM", o, r, "P", u)
        add("OEM_REGION", o, r, "ALL", u)
    for o in ("A", "B"):
        add("OEM", o, "ALL", "ALL", sum(u for (oo, _), u in UNITS.items() if oo == o))
    for r in ("X", "Y"):
        add("REGION", "ALL", r, "ALL", sum(u for (_, rr), u in UNITS.items() if rr == r))
    add("TOTAL", "ALL", "ALL", "ALL", sum(UNITS.values()))
    s.execute(insert(ForecastPoint), rows)
    s.flush()
    return "run-1"


def planner() -> UserCtx:
    return UserCtx(1, "planner@x", "planner", None, None)


def rep(regions=("X",)) -> UserCtx:
    return UserCtx(2, "rep@x", "sales_rep", None, list(regions))
