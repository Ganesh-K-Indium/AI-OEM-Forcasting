"""Deterministic synthetic enterprise dataset (clearly labelled [SYNTHETIC DEMO MODE] in the UI).

Models a components supplier selling to 5 OEMs through direct accounts *and* distributors:
 * 36 months of ERP history (+12 months of hidden latent future used only to build backlog)
 * Sold-To / distributor / end-customer entity chain with messy names, decoys and partial identifiers
 * 2,000+ SFDC opportunities with Markov stage progression, rep bias, close-date pushing and monthly snapshots
 * embedded scenarios: steady growth, quarter-end spikes, annual seasonality, intermittent demand,
   trend break, supply bottleneck, over-optimistic pipeline, under-covered backlog
Everything derives from `seed`; no wall-clock or global RNG state is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from app.core.calendar import add_months, month_range

REGIONS = {"AMER": "Americas", "EMEA": "Europe, Middle East & Africa", "APAC": "Asia Pacific"}
REGION_CCY = {"AMER": "USD", "EMEA": "EUR", "APAC": "JPY"}
REF_FX = {"USD": 1.0, "EUR": 1.08, "JPY": 0.0067}

OEM_SPEC = {
    "APPLE": dict(name="Apple Inc.", size=1.8, disc=0.90, aliases=["Apple", "Apple Inc", "APPLE COMPUTER INC", "Apple Computer"],
                  domain="apple-synth.example", mix=dict(DISP=1.6, MEM=1.6, RF=1.4, MCU=0.9, PWR_IC=1.0, MEMS=1.1, CONN=0.8, PWR_MOD=0.3)),
    "DELL": dict(name="Dell Technologies Inc.", size=1.2, disc=0.95, aliases=["Dell", "Dell Inc", "Dell Computer Corp", "Dell EMC"],
                 domain="dell-synth.example", mix=dict(DISP=0.7, MEM=1.7, RF=0.6, MCU=0.8, PWR_IC=1.3, MEMS=0.4, CONN=1.4, PWR_MOD=0.7)),
    "SIEMENS": dict(name="Siemens AG", size=0.9, disc=1.05, aliases=["Siemens", "Siemens Aktiengesellschaft", "Siemens Industry"],
                    domain="siemens-synth.example", mix=dict(DISP=0.3, MEM=0.5, RF=0.7, MCU=1.4, PWR_IC=1.0, MEMS=1.1, CONN=1.3, PWR_MOD=1.6)),
    "BOSCH": dict(name="Robert Bosch GmbH", size=1.0, disc=1.02, aliases=["Bosch", "Robert Bosch", "Bosch GmbH", "Bosch Group"],
                  domain="bosch-synth.example", mix=dict(DISP=0.4, MEM=0.5, RF=0.8, MCU=1.6, PWR_IC=1.1, MEMS=1.7, CONN=1.0, PWR_MOD=1.2)),
    "TOYOTA": dict(name="Toyota Motor Corporation", size=1.4, disc=0.98, aliases=["Toyota", "Toyota Motor Corp", "Toyota Motors", "TMC"],
                   domain="toyota-synth.example", mix=dict(DISP=0.6, MEM=0.5, RF=0.6, MCU=1.6, PWR_IC=1.2, MEMS=1.5, CONN=1.2, PWR_MOD=1.5)),
}
OEM_REGION_W = {  # relative regional weight per OEM
    "APPLE": dict(AMER=1.0, EMEA=0.6, APAC=1.4), "DELL": dict(AMER=1.4, EMEA=0.8, APAC=0.7),
    "SIEMENS": dict(AMER=0.6, EMEA=1.5, APAC=0.7), "BOSCH": dict(AMER=0.7, EMEA=1.4, APAC=0.8),
    "TOYOTA": dict(AMER=0.9, EMEA=0.5, APAC=1.6),
}
PRODUCTS = {
    "MCU": ("Microcontrollers", "Compute", 2, 3200, -0.03), "PWR_IC": ("Power Management ICs", "Power", 2, 1800, -0.035),
    "MEMS": ("MEMS Sensors", "Sensing", 3, 2600, -0.02), "RF": ("RF Modules", "Connectivity", 3, 4100, -0.025),
    "DISP": ("Display Drivers", "Display", 2, 2200, -0.05), "MEM": ("Memory Modules", "Memory", 1, 5200, -0.07),
    "CONN": ("Connectors", "Interconnect", 1, 900, -0.01), "PWR_MOD": ("Power Modules", "Power", 4, 7800, -0.015),
}
REGION_ASP = dict(AMER=1.0, EMEA=1.01, APAC=0.97)
# fraction of an eventual month's demand that is already booked h months ahead: a * exp(-k*(h-1))
BACKLOG_CURVE = {"MCU": (0.85, 0.55), "PWR_IC": (0.80, 0.55), "MEMS": (0.88, 0.45), "RF": (0.90, 0.42), "DISP": (0.78, 0.60),
                 "MEM": (0.55, 0.75), "CONN": (0.50, 0.80), "PWR_MOD": (0.97, 0.33)}
STAGE_PROB = {1: 0.10, 2: 0.25, 3: 0.40, 4: 0.60, 5: 0.80}

# dirty / partially identified accounts per OEM x region (idx 2 in the list), decoys and distributors
DIRTY_NAMES = {
    ("APPLE", "AMER"): "APPLE COMPUTER INC - AUSTIN TX", ("APPLE", "EMEA"): "Apple Opns Europe Ltd.", ("APPLE", "APAC"): "APPLE COMPUTER TRADING SHANGHAI CO LTD",
    ("DELL", "AMER"): "Dell Computer Corp (Round Rock)", ("DELL", "EMEA"): "DELL EMC Ireland Ops Ltd", ("DELL", "APAC"): "Dell Asia Pacific Sdn Bhd",
    ("SIEMENS", "AMER"): "Siemens Industry Inc Alpharetta", ("SIEMENS", "EMEA"): "SIEMENS AKTIENGESELLSCHAFT MUNICH", ("SIEMENS", "APAC"): "Siemens Ltd China Beijing",
    ("BOSCH", "AMER"): "Bosch Automotive Svc Solutions NA", ("BOSCH", "EMEA"): "ROBERT BOSCH GMBH STUTTGART", ("BOSCH", "APAC"): "Bosch Automotive Products Suzhou",
    ("TOYOTA", "AMER"): "Toyota Motor Mfg Kentucky", ("TOYOTA", "EMEA"): "Toyota Motor Europe NV/SA", ("TOYOTA", "APAC"): "TOYOTA MOTOR CORPORATION NAGOYA",
}
DECOYS = [("Applegate Industrial Supply LLC", "AMER", "US"), ("Boschung Mecatronics AG", "EMEA", "CH"), ("Dell'Orto SpA", "EMEA", "IT"),
          ("Siemon Cabling Systems Inc", "AMER", "US"), ("Toyoda Gosei Trading Co", "APAC", "JP"), ("Delta Dell Logistics Pte", "APAC", "SG")]
DISTRIBUTORS = {"AMER": ["Meridian Electronic Components Distribution Inc", "Northgate Semiconductor Supply Corp"],
                "EMEA": ["Eurotek Components Distribution GmbH", "Continental Electronic Parts BV"],
                "APAC": ["Pacifica Semiconductor Distribution Pte Ltd", "Orient Chip Trading Co Ltd"]}
COUNTRY = dict(AMER=["US", "US", "MX"], EMEA=["IE", "DE", "NL"], APAC=["SG", "JP", "CN"])


@dataclass
class SyntheticBundle:
    months: list[date]
    n_hist: int
    oems: pd.DataFrame
    identifiers: pd.DataFrame
    aliases: pd.DataFrame
    products: pd.DataFrame
    accounts: pd.DataFrame  # includes ground-truth columns true_oem/true_region
    reps: pd.DataFrame
    sales: pd.DataFrame
    backlog: pd.DataFrame
    capacity: pd.DataFrame
    opportunities: pd.DataFrame
    snapshots: pd.DataFrame
    contracts: pd.DataFrame
    fx: pd.DataFrame
    manual_mappings: pd.DataFrame  # admin-curated distributor allocations (seeded into DB tables)
    scenarios: dict[str, str]
    truth: pd.DataFrame  # latent shipped units per series x month incl. future (tests only)
    meta: dict = field(default_factory=dict)


def sk(o: str, r: str, p: str) -> str:
    return f"{o}|{r}|{p}"


def _assign_scenarios(rng, series: list[tuple], base: dict) -> dict[str, str]:
    tags: dict[str, str] = {}
    fixed = {
        ("DELL", "APAC", "MEM"): "supply_bottleneck", ("APPLE", "APAC", "MEM"): "supply_bottleneck",
        ("TOYOTA", "APAC", "MEM"): "supply_bottleneck",
        ("BOSCH", "AMER", "MCU"): "pipeline_push", ("BOSCH", "AMER", "MEMS"): "pipeline_push", ("DELL", "AMER", "PWR_IC"): "pipeline_push",
        ("SIEMENS", "EMEA", "CONN"): "trend_break", ("DELL", "EMEA", "DISP"): "trend_break",
        ("TOYOTA", "EMEA", "PWR_MOD"): "under_coverage", ("SIEMENS", "AMER", "PWR_MOD"): "under_coverage",
        ("BOSCH", "EMEA", "PWR_MOD"): "steady_growth", ("APPLE", "AMER", "RF"): "quarter_end_spike",
    }
    for s in series:
        if s in fixed:
            tags[sk(*s)] = fixed[s]
    # intermittent: the 10 smallest remaining series
    rest = sorted((s for s in series if sk(*s) not in tags), key=lambda s: base[s])
    for s in rest[:10]:
        tags[sk(*s)] = "intermittent"
    others = [s for s in series if sk(*s) not in tags]
    kinds = rng.choice(["steady_growth", "quarter_end_spike", "annual_seasonal", "flat"], size=len(others), p=[0.45, 0.25, 0.25, 0.05])
    for s, k in zip(others, kinds):
        tags[sk(*s)] = str(k)
    return tags


def generate(seed: int = 42, end_month: date = date(2026, 9, 1), n_hist: int = 36, n_opps: int = 2400, n_future: int = 12) -> SyntheticBundle:
    rng = np.random.default_rng(seed)
    T = n_hist + n_future
    months = month_range(add_months(end_month, -(n_hist - 1)), add_months(end_month, n_future))
    assert len(months) == T
    tt = np.arange(T)
    oem_codes, reg_codes, prod_codes = list(OEM_SPEC), list(REGIONS), list(PRODUCTS)
    series = [(o, r, p) for o in oem_codes for r in reg_codes for p in prod_codes]
    S = len(series)

    # ---------------------------------------------------------------- base levels & scenarios
    base = {}
    for (o, r, p) in series:
        mix = OEM_SPEC[o]["mix"][p]
        base[(o, r, p)] = 62.0 * OEM_SPEC[o]["size"] * mix * OEM_REGION_W[o][r] * float(rng.lognormal(0, 0.18))
    scen = _assign_scenarios(rng, series, base)

    # shared latent factors give the hierarchy genuine cross-series covariance
    def ar1(n, phi, sig):
        x = np.zeros(n)
        for i in range(1, n):
            x[i] = phi * x[i - 1] + rng.normal(0, sig)
        return x

    f_oem = {o: ar1(T, 0.6, 0.035) for o in oem_codes}
    f_prod = {p: ar1(T, 0.6, 0.03) for p in prod_codes}
    f_reg = {r: ar1(T, 0.6, 0.02) for r in reg_codes}
    shock = -0.07 * np.exp(-0.5 * ((tt - 15) / 1.6) ** 2)  # component-shortage dip ~ month 15

    demand = np.zeros((S, T))
    sidx = {s: i for i, s in enumerate(series)}
    for s in series:
        o, r, p = s
        tag = scen[sk(*s)]
        b = base[s]
        trend = np.ones(T)
        season = np.ones(T)
        if tag in ("steady_growth",):
            trend = (1 + float(rng.uniform(0.07, 0.15))) ** (tt / 12)
        elif tag == "flat":
            trend = 1 + 0.0 * tt
        elif tag == "quarter_end_spike":
            trend = (1 + float(rng.uniform(0.02, 0.07))) ** (tt / 12)
            amp = float(rng.uniform(0.8, 1.2))
            qpos = np.array([(m.month - 1) % 3 for m in months])
            season = 1 + amp * np.array([-0.12, -0.03, 0.17])[qpos]
        elif tag == "annual_seasonal":
            trend = (1 + float(rng.uniform(0.0, 0.06))) ** (tt / 12)
            ph = float(rng.uniform(-1, 1))
            mm = np.array([m.month for m in months])
            season = 1 + 0.22 * np.sin(2 * np.pi * (mm - 8 + ph) / 12) + 0.06 * np.sin(4 * np.pi * (mm - 2) / 12)
        elif tag == "supply_bottleneck":
            trend = (1.02) ** np.maximum(tt - 14, 0) * (1 + 0.004 * tt)
        elif tag == "trend_break":
            trend = (1 + 0.06) ** (tt / 12) * (1 - 0.38 / (1 + np.exp(-(tt - 25) / 1.5)))
        elif tag == "pipeline_push":
            trend = (1 + 0.02) ** (tt / 12)
        elif tag == "under_coverage":
            trend = (1 + 0.08) ** (tt / 12)
        elif tag == "intermittent":
            trend = np.ones(T)
        sig = 0.10 if tag != "intermittent" else 0
        noise = rng.normal(0, sig, T)
        lvl = b * trend * season * np.exp(noise + f_oem[o] + f_prod[p] + f_reg[r] + shock)
        if tag == "intermittent":
            occ = rng.random(T) < 0.45
            size = rng.gamma(shape=1.3, scale=b * 1.4 / 1.3, size=T)
            lvl = np.where(occ, size, 0.0)
        demand[sidx[s]] = lvl

    # ---------------------------------------------------------------- reps, accounts
    reps = []
    for r_i, r in enumerate(reg_codes):
        for k in range(8):
            reps.append(dict(id=r_i * 8 + k + 1, name=f"Rep {r}-{k + 1:02d}", region_code=r,
                             optimism=float(rng.uniform(-0.05, 0.25)) if k < 6 else float(rng.uniform(0.45, 0.8)),
                             skill=float(rng.uniform(0.75, 1.25))))
    reps = pd.DataFrame(reps)

    accounts, ident, aliases, oems_rows = [], [], [], []
    acct_id = 0
    duns_ctr = 990000000

    def new_acct(name, typ, region, country, true_oem=None, true_region=None, **kw):
        nonlocal acct_id
        acct_id += 1
        row = dict(id=acct_id, erp_customer_id=f"C{acct_id:06d}", name=name, account_type=typ, region_code=region, country=country,
                   true_oem=true_oem, true_region=true_region, duns=None, global_ultimate_duns=None, tax_id=None, erp_parent_id=None, domain=None)
        row.update(kw)
        accounts.append(row)
        return acct_id

    oem_meta = {}
    for i, (o, spec) in enumerate(OEM_SPEC.items(), start=1):
        oems_rows.append(dict(id=i, code=o, name=spec["name"]))
        gdun = f"{duns_ctr + i * 1000:09d}"
        oem_meta[o] = dict(id=i, gduns=gdun, tax=f"99-{i:07d}", erp_parent=f"PARENT-{o}")
        ident += [dict(oem_id=i, id_type="GLOBAL_DUNS", id_value=gdun), dict(oem_id=i, id_type="TAX_ID", id_value=oem_meta[o]["tax"]),
                  dict(oem_id=i, id_type="ERP_PARENT_ID", id_value=oem_meta[o]["erp_parent"]), dict(oem_id=i, id_type="DOMAIN", id_value=spec["domain"])]
        for a in [spec["name"]] + spec["aliases"]:
            aliases.append(dict(oem_id=i, alias=a))

    direct_w = np.array([0.58, 0.34, 0.08])
    acct_of: dict[tuple, dict] = {}
    for o in oem_codes:
        m = oem_meta[o]
        for r in reg_codes:
            cc = COUNTRY[r]
            nm = OEM_SPEC[o]["name"].replace(" Inc.", "").replace(" AG", "").replace(" GmbH", "").replace(" Technologies", "").replace(" Motor Corporation", "")
            a0 = new_acct(f"{nm} Operations {REGIONS[r].split(',')[0]} Ltd", "DIRECT", r, cc[0], o, r,
                          duns=f"{duns_ctr + 7000 + acct_id:09d}", global_ultimate_duns=m["gduns"], erp_parent_id=m["erp_parent"])
            a1 = new_acct(f"{nm} Sales International ({r})", "DIRECT", r, cc[1], o, r, tax_id=m["tax"], domain=OEM_SPEC[o]["domain"])
            a2 = new_acct(DIRTY_NAMES[(o, r)], "DIRECT", r, cc[2], o, r)
            ends = [new_acct(f"{nm} - {site} Assembly Plant", "END_CUSTOMER", r, cc[j % 3], o, r,
                             erp_parent_id=m["erp_parent"] if j == 0 else None)
                    for j, site in enumerate(["Plant-A " + cc[0], "Plant-B " + cc[1]])]
            acct_of[(o, r)] = dict(direct=[a0, a1, a2], ends=ends)
    dist_ids: dict[str, list[int]] = {}
    for r in reg_codes:
        dist_ids[r] = [new_acct(n, "DISTRIBUTOR", r, COUNTRY[r][0]) for n in DISTRIBUTORS[r]]
    decoy_ids = [new_acct(n, "DIRECT", r, c, None, None) for n, r, c in DECOYS]
    accounts = pd.DataFrame(accounts)

    # ---------------------------------------------------------------- opportunity simulation
    prod_lead = {p: v[2] for p, v in PRODUCTS.items()}
    sim_start, rate = -8, n_opps / (n_hist + 3)
    opp_rows, snap_rows, proj = [], [], np.zeros((S, T))
    w_series = np.array([base[s] if scen[sk(*s)] != "intermittent" else 0.0 for s in series])
    w_series = w_series / w_series.sum()
    oid = 0
    for cm in range(sim_start, n_hist):
        for _ in range(rng.poisson(rate)):
            si = int(rng.choice(S, p=w_series))
            o, r, p = series[si]
            tag = scen[sk(o, r, p)]
            rep = reps[reps.region_code == r].sample(1, random_state=int(rng.integers(1 << 30))).iloc[0]
            push = tag == "pipeline_push" and rep["optimism"] > 0.4
            p_true = 0.30 * rep["skill"] * (0.55 if tag == "pipeline_push" else 1.0)
            if push:
                p_true = 0.10
            will_win = rng.random() < min(p_true, 0.9)
            lose_stage = int(rng.choice([1, 2, 3, 4, 5], p=[0.25, 0.25, 0.2, 0.18, 0.12])) if not will_win else 0
            if not will_win and push:
                lose_stage = int(rng.choice([4, 5], p=[0.5, 0.5]))
            n_st = 5 if will_win else lose_stage
            durs = [1 + int(rng.poisson(0.5 if st < 4 else (1.5 if push else 0.7))) for st in range(1, n_st + 1)]
            close_cm = cm + sum(durs)
            ramp = int(rng.choice([3, 6, 9, 12], p=[0.2, 0.4, 0.25, 0.15]))
            rate_units = base[(o, r, p)] * float(rng.lognormal(np.log(0.11), 0.55))
            qty = rate_units * ramp
            price = 1.0  # amount set below once ASP known
            planned = max(2, int(round(sum(durs) * (1 - 0.35 * rep["optimism"]))))
            exp_close = cm + planned
            inflate = max(0.0, rep["optimism"] - 0.1) * 0.8
            # account routing
            u = rng.random()
            if u < 0.55:
                acc, end = acct_of[(o, r)]["direct"][int(rng.choice(2))], None
            else:
                acc, end = int(rng.choice(dist_ids[r])), int(rng.choice(acct_of[(o, r)]["ends"]))
            first_delivery = None
            if will_win:
                first_delivery = close_cm + prod_lead[p] + int(rng.choice([0, 1, 2], p=[0.55, 0.3, 0.15]))
                for k in range(ramp):
                    j = first_delivery + k
                    if 0 <= j < T:
                        proj[si, j] += rate_units
            if close_cm < 0:
                continue  # fully historic opp before the CRM window: only its deliveries matter
            oid += 1
            stage_by_month, st, left = {}, 1, durs[0]
            mis, pc = 0, 0
            stage_idx = 0
            elapsed_in_stage = 0
            for m in range(cm, min(close_cm, n_hist - 1) + 1):
                if m == close_cm:
                    stage_now = 6 if will_win else 7
                else:
                    while stage_idx < n_st - 1 and m - cm >= sum(durs[: stage_idx + 1]):
                        stage_idx += 1
                    stage_now = stage_idx + 1
                if m == close_cm:
                    exp_m = m
                else:
                    if m >= exp_close:
                        pc += 1
                        exp_close = m + 1 + (1 if rng.random() < 0.35 else 0)
                    exp_m = exp_close
                start_of_stage = cm + sum(durs[: min(stage_now - 1, n_st)]) if stage_now <= 5 else close_cm
                if m >= 0:
                    snap_rows.append(dict(opportunity_id=oid, snapshot_month=months[m], stage=stage_now,
                                          qty_units=qty * (1 + inflate if stage_now <= 5 else 1.0), expected_close_month=months[min(exp_m, T - 1)],
                                          months_in_stage=max(0, m - start_of_stage), push_count=pc,
                                          rep_probability=min(0.97, STAGE_PROB.get(stage_now, 1.0 if stage_now == 6 else 0.0) * (1 + rep["optimism"])),
                                          quote_issued=stage_now >= 3))
            is_closed = close_cm <= n_hist - 1
            opp_rows.append(dict(id=oid, sfdc_id=f"006{oid:08d}", name=f"{o} {r} {PRODUCTS[p][0]} program #{oid}", account_id=acc, end_account_id=end,
                                 product_code=p, rep_id=int(rep["id"]), created_month=months[max(cm, 0)] if cm >= 0 else months[0],
                                 is_closed=is_closed, is_won=bool(will_win and is_closed), closed_month=months[close_cm] if is_closed else None,
                                 first_delivery_month=months[first_delivery] if (will_win and is_closed and first_delivery is not None and first_delivery < T) else None,
                                 quantity_units=qty, ramp_months=ramp, _si=si, _price=price, _will_win=bool(will_win), _close_cm=close_cm))

    # ---------------------------------------------------------------- capacity & shipments
    total_demand = demand + proj
    cap = np.zeros((len(reg_codes), len(prod_codes), T))
    ship = total_demand.copy()
    for ri, r in enumerate(reg_codes):
        for pi, p in enumerate(prod_codes):
            idxs = [sidx[(o, r, p)] for o in oem_codes]
            agg = total_demand[idxs].sum(0)
            c = 1.45 * agg.max() * np.ones(T)
            if (r, p) == ("APAC", "MEM"):
                c = np.full(T, 1.03 * agg[24:28].mean())
                c[T - 4:] *= 1.12
            if (r, p) == ("EMEA", "PWR_MOD"):
                c = 1.30 * agg[:n_hist].max() * np.ones(T)
                c[n_hist + 2: n_hist + 4] *= 0.80
            if (r, p) == ("AMER", "MCU"):
                c = 1.12 * agg[n_hist - 6:n_hist].max() * np.ones(T)
                c[n_hist + 5:] *= 0.94
            cap[ri, pi] = c
            f = np.minimum(1.0, c / np.maximum(agg, 1e-9))
            for i in idxs:
                ship[i] = total_demand[i] * f
    ship = np.round(ship, 3)

    # ---------------------------------------------------------------- ASP, FX, revenue (local)
    mo_fx = {}
    for ccy, ref in REF_FX.items():
        if ccy == "USD":
            mo_fx[ccy] = np.ones(T)
        else:
            walk = np.cumsum(rng.normal(0, 0.012, T))
            mo_fx[ccy] = ref * np.exp(walk - walk[n_hist - 1] * 0.5)
    fx_rows = [dict(month=months[t], currency=c, rate_to_usd=float(mo_fx[c][t])) for c in mo_fx for t in range(T)]
    contract_rows, contract_combo = [], {}
    for o in oem_codes:
        for r in reg_codes:
            for p in prod_codes:
                if scen[sk(o, r, p)] == "intermittent":
                    continue
                if OEM_SPEC[o]["mix"][p] >= 1.3 and rng.random() < 0.7:
                    start = int(rng.integers(-6, 6))
                    chg = float(rng.uniform(-0.05, -0.02))
                    contract_combo[(o, r, p)] = (start, chg)
    price_local = np.zeros((S, T))
    mem_walk = np.cumsum(rng.normal(0, 0.012, T))
    for s in series:
        o, r, p = s
        _, _, _, p0, g = PRODUCTS[p]
        ccy = REGION_CCY[r]
        usd = p0 * OEM_SPEC[o]["disc"] * REGION_ASP[r] * (1 + g) ** (tt / 12)
        if p == "MEM":
            usd = usd * np.exp(mem_walk)
        if s in contract_combo:
            st0, chg = contract_combo[s]
            step = np.floor((tt - st0) / 12).clip(0)
            usd = p0 * OEM_SPEC[o]["disc"] * REGION_ASP[r] * (1 + chg) ** step
        else:
            usd = usd * np.exp(rng.normal(0, 0.012, T))
        price_local[sidx[s]] = usd / REF_FX[ccy]
    for (o, r, p), (st0, chg) in contract_combo.items():
        si = sidx[(o, r, p)]
        acc = acct_of[(o, r)]["direct"][0]
        avg_units = float(ship[si, :n_hist].mean())
        contract_rows.append(dict(account_id=acc, product_code=p, start_month=months[max(st0, 0)] if st0 >= 0 else months[0],
                                  end_month=months[min(T - 1, max(st0, 0) + 11 + 12 * ((T - max(st0, 0)) // 12))],
                                  committed_units_per_month=round(0.65 * avg_units, 2),
                                  contract_price_local=float(price_local[si, max(st0, 0)]), currency=REGION_CCY[r],
                                  annual_price_change_pct=chg))
    contracts = pd.DataFrame(contract_rows)

    # opportunity amounts (total deal value in local currency at close-ish price)
    for row in opp_rows:
        si = row["_si"]
        ccy = REGION_CCY[series[si][1]]
        t_ref = min(max(row["_close_cm"], 0), T - 1)
        row["amount_local"] = row["quantity_units"] * float(price_local[si, t_ref])
        row["currency"] = ccy
    opps = pd.DataFrame(opp_rows)
    snaps = pd.DataFrame(snap_rows)
    si_of = opps.set_index("id")["_si"]
    snaps["amount_local"] = [q * float(price_local[si_of[i], min(months.index(m), T - 1)]) for i, q, m in
                             zip(snaps.opportunity_id, snaps.qty_units, snaps.snapshot_month)]
    snaps = snaps.rename(columns={"qty_units": "quantity_units"})
    opps = opps.drop(columns=["_si", "_price", "_will_win", "_close_cm"])

    # ---------------------------------------------------------------- account-level splits
    # per (o,p): share via direct / end-customer distributor / allocated distributor
    split = {}
    for o in oem_codes:
        for p in prod_codes:
            e = float(rng.uniform(0.06, 0.16))
            a = float(rng.uniform(0.08, 0.22))
            split[(o, p)] = (1 - e - a, e, a)
    sales_rows, flow = [], {}  # flow[(dist, oem)] -> units total (for allocation pct)
    alloc_units: dict[tuple, float] = {}
    dist_w = {r: np.array([0.6, 0.4]) for r in reg_codes}
    row_defs = []  # (series idx, account, end, share, kind)
    for s in series:
        o, r, p = s
        si = sidx[s]
        d, e, a = split[(o, p)]
        if scen[sk(*s)] == "intermittent":
            d, e, a = 1.0, 0.0, 0.0
        for acc, w in zip(acct_of[(o, r)]["direct"], direct_w):
            row_defs.append((si, acc, None, d * float(w), "direct"))
        for k, dacc in enumerate(dist_ids[r]):
            if e > 0:
                for j, endc in enumerate(acct_of[(o, r)]["ends"]):
                    row_defs.append((si, dacc, endc, e * float(dist_w[r][k]) * (0.5 if j == 0 else 0.5), "end"))
            if a > 0:
                row_defs.append((si, dacc, None, a * float(dist_w[r][k]), "alloc"))
    rd = pd.DataFrame(row_defs, columns=["si", "acc", "end", "share", "kind"])
    # sales (history only)
    ts_hist = np.arange(n_hist)
    sales_parts = []
    for rdrow in rd.itertuples(index=False):
        o, r, p = series[rdrow.si]
        u = ship[rdrow.si, :n_hist] * rdrow.share
        loc = u * price_local[rdrow.si, :n_hist]
        sales_parts.append(pd.DataFrame(dict(month=[months[t] for t in ts_hist], account_id=rdrow.acc, end_account_id=rdrow.end,
                                             product_code=p, units=u, revenue_local=loc, currency=REGION_CCY[r], _kind=rdrow.kind, _oem=o, _si=rdrow.si)))
    sales = pd.concat(sales_parts, ignore_index=True)
    sales = sales[sales.units > 1e-6].copy()
    # decoys: small independent flows that do NOT belong to any forecast OEM
    dec_parts = []
    for did, (nm, r, _) in zip(decoy_ids, DECOYS):
        for p in rng.choice(prod_codes, size=2, replace=False):
            u = np.maximum(0, rng.normal(3.0, 1.0, n_hist))
            dec_parts.append(pd.DataFrame(dict(month=months[:n_hist], account_id=did, end_account_id=None, product_code=p, units=u,
                                               revenue_local=u * 2500 / REF_FX[REGION_CCY[r]], currency=REGION_CCY[r], _kind="decoy", _oem="NONE", _si=-1)))
    sales = pd.concat([sales] + [d.astype({"end_account_id": "float"}) for d in dec_parts], ignore_index=True) if False else pd.concat([sales.assign(end_account_id=sales.end_account_id.astype("float"))] + [d.assign(end_account_id=np.nan) for d in dec_parts], ignore_index=True)

    # allocation pct for distributors without end-customer (curated by admins in the DB)
    alloc = sales[sales._kind == "alloc"].groupby(["account_id", "_oem"]).units.sum().reset_index().rename(columns={"_oem": "oem"})
    alloc["pct"] = alloc.units / alloc.groupby("account_id").units.transform("sum")
    reg_of_oem = {}
    mans = []
    for rr in alloc.itertuples(index=False):
        dreg = accounts.set_index("id").loc[rr.account_id, "region_code"]
        mans.append(dict(account_id=int(rr.account_id), oem_code=rr.oem, region_code=dreg, allocation_pct=float(round(rr.pct, 6))))
    manual = pd.DataFrame(mans)
    # normalise exactly to 1.0 per distributor
    manual["allocation_pct"] = manual.allocation_pct / manual.groupby("account_id").allocation_pct.transform("sum")

    # ---------------------------------------------------------------- backlog (last 18 snapshots)
    bl_parts = []
    n_snap = 18
    for c in range(n_hist - n_snap, n_hist):
        for h in range(1, 7):
            d = c + h
            for rdrow in rd.itertuples(index=False):
                o, r, p = series[rdrow.si]
                a_, k_ = BACKLOG_CURVE[p]
                frac = a_ * np.exp(-k_ * (h - 1))
                if scen[sk(o, r, p)] == "under_coverage" and c == n_hist - 1:
                    frac *= 0.45
                frac = float(np.clip(frac * rng.normal(1.0, 0.06), 0, 1.0))
                u = ship[rdrow.si, d] * rdrow.share * frac if d < T else 0.0
                if u <= 1e-6:
                    continue
                bl_parts.append((months[c], months[d], rdrow.acc, rdrow.end, p, u, u * price_local[rdrow.si, d], REGION_CCY[r]))
    backlog = pd.DataFrame(bl_parts, columns=["snapshot_month", "delivery_month", "account_id", "end_account_id", "product_code", "units", "value_local", "currency"])

    cap_rows = [dict(month=months[t], region_code=r, product_code=p, capacity_units=float(round(cap[ri, pi, t], 3)))
                for ri, r in enumerate(reg_codes) for pi, p in enumerate(prod_codes) for t in range(T)]
    truth = pd.DataFrame([dict(series=sk(*s), month=months[t], units=float(ship[sidx[s], t]), demand=float(total_demand[sidx[s], t]),
                               price_local=float(price_local[sidx[s], t])) for s in series for t in range(T)])

    sales = sales.drop(columns=["_kind", "_oem", "_si"])
    accounts_out = accounts.copy()
    return SyntheticBundle(
        months=months, n_hist=n_hist,
        oems=pd.DataFrame(oems_rows), identifiers=pd.DataFrame(ident), aliases=pd.DataFrame(aliases),
        products=pd.DataFrame([dict(code=c, name=v[0], family=v[1], lead_time_months=v[2]) for c, v in PRODUCTS.items()]),
        accounts=accounts_out, reps=reps[["id", "name", "region_code"]], sales=sales, backlog=backlog,
        capacity=pd.DataFrame(cap_rows), opportunities=opps.drop(columns=["created_month"]).assign(created_month=opps.created_month),
        snapshots=snaps, contracts=contracts, fx=pd.DataFrame(fx_rows), manual_mappings=manual, scenarios=scen, truth=truth,
        meta=dict(seed=seed, project_share=float(proj[:, :n_hist].sum() / total_demand[:, :n_hist].sum()), series=[sk(*s) for s in series], oem_meta=oem_meta, rep_optimism=reps.set_index("id")["optimism"].to_dict(),
                  dist_ids=dist_ids, decoy_ids=decoy_ids, ref_fx=REF_FX),
    )
