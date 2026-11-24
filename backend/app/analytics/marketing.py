"""Marketing & ROAS analysis: efficiency, contribution, drivers and inefficiencies.

Ratios (ROAS, CTR, CPC, CPM, CVR, CPA, frequency) are always recomputed from their
components (sums), never averaged, and only when the components exist.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import DatasetView, Finding, ProjectData, fmt_num, fmt_pct
from .stats import correlation, pct_change, share_table

RATIO_DEFS = {
    # name: (numerator role, denominator role, multiplier, unit, higher_is_better)
    "ROAS": ("revenue", "spend", 1, "x", True),
    "CTR": ("clicks", "impressions", 100, "%", True),
    "CPC": ("spend", "clicks", 1, "cur", False),
    "CPM": ("spend", "impressions", 1000, "cur", False),
    "CVR": ("conversions", "clicks", 100, "%", True),
    "CPA": ("spend", "conversions", 1, "cur", False),
    "Frequency": ("impressions", "reach", 1, "", True),
}


def marketing_datasets(pdata: ProjectData) -> list[DatasetView]:
    return [ds for ds in pdata.datasets if ds.first("spend") and (ds.first("revenue") or ds.first("conversions") or ds.first("clicks"))]


def _ratio_table(ds: DatasetView, by: list) -> pd.DataFrame:
    cols = {r: ds.first(r) for r in ("spend", "revenue", "impressions", "clicks", "conversions", "reach") if ds.first(r)}
    g = ds.df.groupby(by)[list(cols.values())].sum(min_count=1) if by else ds.df[list(cols.values())].sum(min_count=1).to_frame().T
    g = g.rename(columns={v: k for k, v in cols.items()})
    for name, (num, den, mult, _, _) in RATIO_DEFS.items():
        if num in g.columns and den in g.columns:
            g[name] = g[num] / g[den].replace(0, np.nan) * mult
    return g


def analyse_marketing(pdata: ProjectData, mode: str) -> list[Finding]:
    out: list[Finding] = []
    for ds in marketing_datasets(pdata):
        cur = ds.unit(ds.first("spend")) or ds.unit(ds.first("revenue") or "")
        has_rev = bool(ds.first("revenue"))
        tot = _ratio_table(ds, []).iloc[0]
        dims = [d for d in ds.dims(max_levels=40, limit=5) if ds.role(d) in ("channel", "campaign", "product", "category", "geography", "customer_segment", "brand", "dimension")]
        # ---- overall KPI summary
        kpis = []
        for name, (_, _, _, unit, _) in RATIO_DEFS.items():
            if name in tot.index and pd.notna(tot[name]):
                kpis.append(f"{name} {fmt_num(float(tot[name]), cur if unit == 'cur' else unit)}")
        summary = f"Total spend {fmt_num(float(tot['spend']), cur)}"
        if has_rev:
            summary += f", attributed revenue {fmt_num(float(tot['revenue']), cur)}"
        out.append(Finding(
            category="marketing", title="Marketing performance summary" + (f": overall ROAS {float(tot['ROAS']):.2f}x" if has_rev and pd.notna(tot.get('ROAS')) else ""),
            fact=summary + ("; " + ", ".join(kpis) if kpis else "") + ". Ratios are recomputed from summed components.",
            interpretation="This is the baseline against which channels and campaigns are compared below.",
            confidence="high" if not ds.approximate else "medium", evidence=[ds.evidence([c for c in (ds.first('spend'), ds.first('revenue'), ds.first('clicks'), ds.first('impressions'), ds.first('conversions')) if c])],
            magnitude=0.35, impact=0.8, tags=["marketing", "roas", "kpi", "media"], key=f"mkt:summary:{ds.id}",
            details={"dataset": ds.name, "totals": {k: float(v) for k, v in tot.items() if pd.notna(v)}}))
        reported = ds.first("roas")
        if reported and has_rev:
            rep = ds.df[reported].mean()
            if pd.notna(rep) and pd.notna(tot["ROAS"]) and abs(rep - tot["ROAS"]) / max(tot["ROAS"], 1e-9) > 0.1:
                out.append(Finding(
                    category="gap", title="Reported ROAS does not match ROAS computed from spend and revenue",
                    fact=f"The average of the '{reported}' column is {rep:.2f}x, but total revenue ÷ total spend is {tot['ROAS']:.2f}x.",
                    interpretation="Averaging row-level ROAS gives small rows the same weight as large ones, or the column uses a different definition.",
                    implication="Decisions based on the reported column could misjudge efficiency.",
                    validation="Confirm the ROAS definition (attribution window, revenue type) used in the source.",
                    confidence="high", evidence=[ds.evidence([reported, ds.first('revenue'), ds.first('spend')])], magnitude=0.5, impact=0.8,
                    tags=["marketing", "roas", "measurement", "gap", "data quality"], key=f"mkt:roasmismatch:{ds.id}",
                    records={"gaps": [{"gap_type": "measurement", "gap": "Inconsistent ROAS definition", "evidence": f"reported {rep:.2f}x vs computed {tot['ROAS']:.2f}x",
                                       "why_it_matters": "ROAS drives budget allocation.", "significance": "high",
                                       "missing_information": "Definition of the reported ROAS metric.",
                                       "recommended_investigation": "Reconcile the reporting definition with the platform/attribution owner.",
                                       "potential_action": "Standardise ROAS = attributed revenue ÷ media spend."}]}))
        for d in dims:
            out += _dimension_efficiency(ds, d, tot, cur, has_rev, mode)
        if has_rev and ds.time_col and dims:
            ch = next((d for d in dims if ds.role(d) == "channel"), dims[0])
            out += _roas_drivers(ds, ch, cur)
            if mode != "quick":
                out += _diminishing_returns(ds, ch, cur)
    return out


def _dimension_efficiency(ds, d, tot, cur, has_rev, mode) -> list[Finding]:
    out = []
    g = _ratio_table(ds, [d])
    g = g[g["spend"] > 0]
    if len(g) < 2:
        return out
    g["spend_share"] = share_table(g["spend"])
    if has_rev:
        g["rev_share"] = share_table(g["revenue"])
        g["efficiency_index"] = g["rev_share"] / g["spend_share"].replace(0, np.nan)
        g = g.sort_values("ROAS", ascending=False)
        best, worst = g.index[0], g.index[-1]
        top_rev = g["rev_share"].idxmax()
        chart = {"type": "scatter", "title": f"Share of spend vs share of revenue by {d}",
                 "question": f"Which {d} values return more revenue than their share of spend?", "x_label": "% of spend", "y_label": "% of revenue",
                 "unit": "%", "points": [{"label": str(i), "x": float(g.loc[i, 'spend_share']), "y": float(g.loc[i, 'rev_share']),
                                          "size": float(g.loc[i, 'spend'])} for i in g.index], "reference_line": "y=x"}
        lines = [f"{i}: ROAS {g.loc[i, 'ROAS']:.2f}x, {g.loc[i, 'spend_share']:.1f}% of spend → {g.loc[i, 'rev_share']:.1f}% of revenue"
                 for i in g.index[:8]]
        excl = {}
        for i in g.index:
            rest = g.drop(index=i)
            if rest["spend"].sum() > 0:
                excl[str(i)] = float(rest["revenue"].sum() / rest["spend"].sum())
        top_dep = g.loc[top_rev, "rev_share"]
        out.append(Finding(
            category="roas", title=f"ROAS by {d}: '{best}' {g.loc[best, 'ROAS']:.2f}x vs '{worst}' {g.loc[worst, 'ROAS']:.2f}x",
            fact=(f"Overall ROAS is {tot['ROAS']:.2f}x. " + "; ".join(lines) + ". "
                  f"'{top_rev}' contributes the largest share of revenue ({top_dep:.1f}%); without it, ROAS would be {excl.get(str(top_rev), float('nan')):.2f}x."),
            interpretation=(f"Efficiency differs widely across {d}: '{best}' returns {g.loc[best, 'ROAS'] / max(g.loc[worst, 'ROAS'], 1e-9):.1f}× more revenue per unit of spend than '{worst}'."
                            if g.loc[worst, 'ROAS'] > 0 else f"'{worst}' generates almost no attributed revenue."),
            implication=(f"Overall ROAS is {'heavily ' if top_dep >= 40 else ''}dependent on '{top_rev}'. Re-allocating spend between {d} values could change total return, "
                         "but only if the high-ROAS options can absorb more budget at similar efficiency."),
            hypothesis=(f"Lower ROAS for '{worst}' may reflect its role (e.g. upper-funnel awareness) whose value is not captured by last-touch attributed revenue."),
            validation="Run incrementality tests (geo or holdout) or marketing mix modelling before shifting budget; attributed ROAS is not causal.",
            confidence="medium" if not ds.approximate else "low",
            evidence=[ds.evidence([d, ds.first('spend'), ds.first('revenue')])], magnitude=min(1.0, abs(np.log(max(g.loc[best, 'ROAS'], 1e-3) / max(g.loc[worst, 'ROAS'], 1e-3))) / 2),
            impact=0.95, tags=["marketing", "roas", "efficiency", "driver", "contribution", d.lower(), ds.role(d), str(best).lower(), str(worst).lower(), str(top_rev).lower()],
            chart=chart, key=f"roas:{ds.id}:{d}",
            details={"dataset": ds.name, "dimension": d, "table": {str(i): {k: float(v) for k, v in g.loc[i].items() if pd.notna(v)} for i in g.index},
                     "roas_excluding": excl},
            records={"comparison": {"comparison_type": "segment", "subject_a": str(best), "subject_b": str(worst), "measure": "ROAS",
                                    "value_a": float(g.loc[best, 'ROAS']), "value_b": float(g.loc[worst, 'ROAS']),
                                    "details": {"dimension": d}}}))
        # inefficient areas: meaningful spend, ROAS well below average
        weak = g[(g["spend_share"] >= 5) & (g["ROAS"] < 0.6 * tot["ROAS"])]
        for i in weak.index[:3]:
            out.append(Finding(
                category="marketing", title=f"'{i}' absorbs {weak.loc[i, 'spend_share']:.0f}% of spend at below-average ROAS ({weak.loc[i, 'ROAS']:.2f}x)",
                fact=(f"'{i}' ({d}) received {fmt_num(float(weak.loc[i, 'spend']), cur)} ({weak.loc[i, 'spend_share']:.1f}% of spend) and returned "
                      f"{fmt_num(float(weak.loc[i, 'revenue']), cur)} attributed revenue: ROAS {weak.loc[i, 'ROAS']:.2f}x vs {tot['ROAS']:.2f}x overall."),
                interpretation="On attributed revenue alone, this is the least efficient use of budget.",
                implication="If its effect is not being under-measured, budget here could work harder elsewhere.",
                hypothesis="The channel may drive brand or offline effects that attribution misses.",
                validation="Test a controlled spend reduction or use marketing mix modelling to estimate its true contribution.",
                confidence="medium", evidence=[ds.evidence([d, ds.first('spend'), ds.first('revenue')], ds.df[d] == i, filt={d: i})],
                magnitude=min(1.0, weak.loc[i, "spend_share"] / 40), impact=0.9,
                tags=["marketing", "roas", "inefficiency", "risk", str(i).lower(), d.lower(), ds.role(d)], key=f"weak:{ds.id}:{d}:{i}",
                records={"risks": [{"risk": f"Budget in '{i}' may be under-performing", "horizon": "short", "likelihood": "medium",
                                    "impact": f"{weak.loc[i, 'spend_share']:.0f}% of spend returns below-average ROAS", "mitigation": "Test and re-allocate"}]}))
    # rate metrics comparison (CTR/CPC/CPA) for the dimension
    if mode != "quick":
        for name in ("CPA", "CTR", "CPC"):
            if name not in g.columns or g[name].isna().all() or g[name].replace([np.inf], np.nan).dropna().shape[0] < 2:
                continue
            col = g[name].replace([np.inf, -np.inf], np.nan).dropna()
            num, den, mult, unit, hib = RATIO_DEFS[name]
            u = cur if unit == "cur" else unit
            best = col.idxmax() if hib else col.idxmin()
            worst = col.idxmin() if hib else col.idxmax()
            if best == worst or (col.max() / max(col.min(), 1e-9)) < 1.5:
                continue
            out.append(Finding(
                category="marketing", title=f"{name} by {d}: best '{best}' ({fmt_num(float(col[best]), u)}), worst '{worst}' ({fmt_num(float(col[worst]), u)})",
                fact=f"{name} ({'higher' if hib else 'lower'} is better) ranges from {fmt_num(float(col.min()), u)} to {fmt_num(float(col.max()), u)} across {len(col)} {d} values; overall {fmt_num(float(tot[name]), u)}.",
                interpretation=f"There is a {col.max() / max(col.min(), 1e-9):.1f}× spread in {name} across {d}.",
                validation=f"Check whether {d} values differ in objective (awareness vs conversion) before comparing {name} directly.",
                confidence="high" if not ds.approximate else "medium", evidence=[ds.evidence([d, ds.first(num), ds.first(den)])],
                magnitude=min(1.0, np.log(col.max() / max(col.min(), 1e-9)) / 3), impact=0.6,
                tags=["marketing", name.lower(), "efficiency", d.lower(), ds.role(d), str(best).lower(), str(worst).lower()],
                key=f"rate:{ds.id}:{d}:{name}"))
    return out


def _roas_drivers(ds: DatasetView, d: str, cur: str) -> list[Finding]:
    """Decompose the change in total ROAS between the first and last year (or halves) into mix vs efficiency."""
    periods = ds.periods()
    years = pd.to_datetime(ds.df[ds.time_col]).dt.year
    if years.nunique() >= 2:
        labels = years.astype(str)
        a, b = sorted(labels.unique())[-2:]
    else:
        plist = sorted(periods.dropna().unique())
        if len(plist) < 4:
            return []
        half = len(plist) // 2
        labels = periods.map(lambda p: "first half" if p in plist[:half] else "second half")
        a, b = "first half", "second half"
    sp, rv = ds.first("spend"), ds.first("revenue")
    g = ds.df.groupby([labels, ds.df[d]])[[sp, rv]].sum(min_count=1)
    try:
        A, B = g.loc[a], g.loc[b]
    except KeyError:
        return []
    idx = A.index.union(B.index)
    A, B = A.reindex(idx).fillna(0), B.reindex(idx).fillna(0)
    if A[sp].sum() <= 0 or B[sp].sum() <= 0:
        return []
    wA, wB = A[sp] / A[sp].sum(), B[sp] / B[sp].sum()
    rA = (A[rv] / A[sp].replace(0, np.nan)).fillna(0)
    rB = (B[rv] / B[sp].replace(0, np.nan)).fillna(0)
    roasA, roasB = A[rv].sum() / A[sp].sum(), B[rv].sum() / B[sp].sum()
    mix = (wB - wA) * rA
    rate = wB * (rB - rA)
    contrib = (mix + rate).sort_values()
    delta = roasB - roasA
    top_pos, top_neg = contrib.idxmax(), contrib.idxmin()
    chart = {"type": "waterfall", "title": f"What drove the change in ROAS ({a} → {b})", "question": f"Which {d} values drove the change in ROAS?",
             "x_label": d, "y_label": "ROAS points", "unit": "x", "categories": [str(i) for i in contrib.index],
             "series": [{"name": "Contribution", "values": [float(v) for v in contrib.values]}], "start": float(roasA), "end": float(roasB)}
    parts = "; ".join(f"{i}: {contrib[i]:+.2f} (mix {mix[i]:+.2f}, efficiency {rate[i]:+.2f})" for i in contrib.sort_values(key=abs, ascending=False).index[:5])
    share_driver = abs(contrib[top_pos if delta >= 0 else top_neg]) / max(abs(contrib).sum(), 1e-9) * 100
    driver = top_pos if delta >= 0 else top_neg
    return [Finding(
        category="roas", title=f"ROAS {'rose' if delta >= 0 else 'fell'} from {roasA:.2f}x to {roasB:.2f}x; '{driver}' was the biggest driver",
        fact=(f"Total ROAS moved {delta:+.2f} points between {a} and {b}. Contribution by {d} (spend-mix effect + efficiency effect): {parts}. "
              f"'{driver}' accounts for {share_driver:.0f}% of the gross movement."),
        interpretation=(f"The change in ROAS is {'mainly driven by' if share_driver >= 50 else 'partly driven by'} '{driver}'. "
                        "A mix effect means budget moved towards channels with different ROAS; an efficiency effect means the channel's own ROAS changed."),
        implication=f"Future ROAS depends {'heavily ' if share_driver >= 50 else ''}on '{driver}' sustaining its performance.",
        hypothesis=f"Changes in '{driver}' targeting, creative, auction prices or attribution settings may explain its contribution.",
        validation="This is an arithmetic decomposition of attributed ROAS, not causal proof. Validate with incrementality tests.",
        confidence="medium" if not ds.approximate else "low",
        evidence=[ds.evidence([ds.time_col, d, sp, rv])], magnitude=min(1.0, abs(delta) / max(roasA, 1e-9)), impact=0.95,
        tags=["marketing", "roas", "driver", "drivers", "change", "contribution", d.lower(), ds.role(d), str(driver).lower(), str(top_pos).lower()],
        chart=chart, key=f"roasdrv:{ds.id}:{d}",
        details={"dataset": ds.name, "a": str(a), "b": str(b), "roas_a": float(roasA), "roas_b": float(roasB),
                 "contribution": {str(k): float(v) for k, v in contrib.items()}, "mix": {str(k): float(v) for k, v in mix.items()},
                 "efficiency": {str(k): float(v) for k, v in rate.items()}},
        records={"comparison": {"comparison_type": "time", "subject_a": str(a), "subject_b": str(b), "measure": "ROAS",
                                "value_a": float(roasA), "value_b": float(roasB), "details": {"dimension": d, "contribution": {str(k): float(v) for k, v in contrib.items()}}}})]


def _diminishing_returns(ds: DatasetView, d: str, cur: str) -> list[Finding]:
    out = []
    periods = ds.periods()
    sp, rv = ds.first("spend"), ds.first("revenue")
    g = ds.df.groupby([ds.df[d], periods])[[sp, rv]].sum(min_count=1)
    for lvl in g.index.get_level_values(0).unique():
        sub = g.loc[lvl]
        if len(sub) < 8:
            continue
        roas = sub[rv] / sub[sp].replace(0, np.nan)
        cr = correlation(sub[sp], roas)
        if cr and cr["spearman"] <= -0.6 and cr["spearman_p"] < 0.05:
            out.append(Finding(
                category="marketing", title=f"'{lvl}': higher-spend periods show lower ROAS (possible diminishing returns)",
                fact=f"Across {cr['n']} periods, spend and ROAS for '{lvl}' have a rank correlation of {cr['spearman']:.2f} (p={cr['spearman_p']:.3f}).",
                interpretation="As spend in this channel rises, return per unit of spend tends to fall.",
                implication="Further budget increases may deliver progressively less revenue.",
                hypothesis="Audience saturation or rising auction costs could explain this; seasonality could also create the pattern.",
                validation="Correlation is not causation: test spend levels deliberately or model response curves.",
                confidence="low", evidence=[ds.evidence([ds.time_col, d, sp, rv], ds.df[d] == lvl, filt={d: lvl})], magnitude=0.5, impact=0.8,
                tags=["marketing", "roas", "diminishing returns", "saturation", "risk", str(lvl).lower(), d.lower()],
                chart={"type": "scatter", "title": f"'{lvl}': spend vs ROAS by period", "question": "Does ROAS fall as spend rises?",
                       "x_label": sp, "y_label": "ROAS", "unit": "x",
                       "points": [{"label": str(p), "x": float(sub.loc[p, sp]), "y": float(roas.loc[p])} for p in sub.index if pd.notna(roas.loc[p])]},
                key=f"dimret:{ds.id}:{lvl}"))
    return out
