"""Trend analysis across time, overall and by dimension."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import DatasetView, Finding, ProjectData, fmt_num, fmt_pct
from .stats import pct_change, share_table, trend_stats

IMPACT_BY_ROLE = {"revenue": 1.0, "market_size": 0.9, "roas": 0.9, "spend": 0.8, "units": 0.8, "market_share": 0.9,
                  "conversions": 0.8, "margin": 0.9, "actual": 0.8, "growth": 0.7, "price": 0.6, "rating": 0.5}


def _series(ds: DatasetView, measure: str, periods: pd.Series, mask=None) -> pd.Series:
    df = ds.df if mask is None else ds.df[mask]
    p = periods if mask is None else periods[mask]
    g = df.groupby(p)[measure]
    s = g.sum(min_count=1) if ds.agg(measure) == "sum" else g.mean()
    return s.sort_index()


def annual_series(ds: DatasetView, m: str, mask=None) -> pd.Series | None:
    """Year totals (or averages) using only complete years, when the data is sub-annual and covers 2+ full years."""
    t = ds.time_col
    gran = ds.columns[t].granularity if t else ""
    if not t or gran in ("year", ""):
        return None
    df = ds.df if mask is None else ds.df[mask]
    periods = ds.periods() if mask is None else ds.periods()[mask]
    years = pd.to_datetime(df[t]).dt.year
    per_year = periods.groupby(years).nunique()
    if per_year.empty:
        return None
    full = per_year[per_year >= max(per_year.max() * 0.9, 2)].index
    if len(full) < 2:
        return None
    sel = years.isin(full)
    g = df[sel].groupby(years[sel])[m]
    s = g.sum(min_count=1) if ds.agg(m) == "sum" else g.mean()
    s.index = [str(int(i)) for i in s.index]
    return s.sort_index()


def _headline(ds, m, series_stats, mask=None) -> dict:
    """Use complete-year totals for headline change/CAGR when available (avoids seasonal month-vs-month noise)."""
    ann = annual_series(ds, m, mask)
    if ann is not None and len(ann) >= 2:
        st = trend_stats(ann, "year")
        if st:
            return {"start": st["start"], "end": st["end"], "start_period": st["start_period"], "end_period": st["end_period"],
                    "pct_change": st["pct_change"], "cagr": st["cagr"] if len(ann) >= 3 else None, "basis": "complete years",
                    "years": len(ann)}
    return {k: series_stats.get(k) for k in ("start", "end", "start_period", "end_period", "pct_change", "cagr")} | {"basis": "periods"}


def _incomplete_last(ds: DatasetView, periods: pd.Series) -> bool:
    counts = periods.value_counts().sort_index()
    if len(counts) < 4:
        return False
    med = counts.iloc[:-1].median()
    return med >= 4 and counts.iloc[-1] < 0.5 * med


def _confidence(st: dict, approximate: bool, incomplete: bool) -> str:
    if approximate:
        return "low" if st["periods"] < 3 else "medium"
    if st["periods"] >= 4 and (st.get("r_squared") or 0) >= 0.6 and not incomplete:
        return "high"
    if st["periods"] >= 3 or abs(st.get("pct_change") or 0) >= 10:
        return "medium"
    return "medium" if not incomplete else "low"


def wide_period_series(ds: DatasetView):
    """Yield (measure_base, period->column) for measures spread over period columns (Sales 2024 | Sales 2025)."""
    for base, cols in (ds.period_groups or {}).items():
        if all(c in ds.df.columns for c in cols.values()):
            yield base, cols


def analyse_trends(pd_: ProjectData, mode: str) -> list[Finding]:
    findings: list[Finding] = []
    for ds in pd_.datasets:
        t = ds.time_col
        if t:
            periods = ds.periods()
            if periods is None or periods.nunique() < 2:
                continue
            gran = ds.columns[t].granularity or "month"
            incomplete = _incomplete_last(ds, periods)
            measures = ds.measures(limit=4 if mode == "quick" else 8)
            dims = ds.dims(limit=1 if mode == "quick" else 4)
            for mi, m in enumerate(measures):
                if ds.role(m) in ("roas", "ctr", "cpc", "cpm", "cpa", "cvr") and ds.first("spend"):
                    continue  # marketing ratios are recomputed properly in the marketing engine
                use_dims = dims if mi < (1 if mode == "quick" else 2) else []
                findings += _measure_trends(ds, m, periods, gran, incomplete, use_dims, mode)
        # wide layouts: Sales 2024 | Sales 2025
        for base, cols in wide_period_series(ds):
            findings += _wide_trends(ds, base, cols, mode)
    return findings


def _measure_trends(ds, m, periods, gran, incomplete, dims, mode) -> list[Finding]:
    out: list[Finding] = []
    unit = ds.unit(m)
    role = ds.role(m)
    s = _series(ds, m, periods)
    if incomplete and len(s) > 3:
        s_eval = s.iloc[:-1]
    else:
        s_eval = s
    st = trend_stats(s_eval, gran)
    if not st:
        return out
    t = ds.time_col
    agg_word = "total" if ds.agg(m) == "sum" else "average"
    hl = _headline(ds, m, st)
    st["headline"] = hl
    pc = hl["pct_change"]
    direction = st["direction"]
    ev = ds.evidence([t, m], values={"start": st["start"], "end": st["end"], "pct_change": pc,
                                     "start_period": st["start_period"], "end_period": st["end_period"]})
    chart = {"type": "line", "title": f"{m} over time", "question": f"How has {m} changed over time?",
             "x_label": t, "y_label": m, "unit": unit, "categories": [r["period"] for r in st["series"]],
             "series": [{"name": m, "values": [r["value"] for r in st["series"]]}]}
    if st.get("moving_average"):
        chart["series"].append({"name": "3-period moving average", "values": st["moving_average"], "style": "dashed"})
    if hl["basis"] == "complete years":
        fact = (f"{agg_word.capitalize()} {m} was {fmt_num(hl['start'], unit)} in {hl['start_period']} and "
                f"{fmt_num(hl['end'], unit)} in {hl['end_period']} ({fmt_pct(pc)}, complete years only). "
                f"The {st['periods']}-period series runs from {st['start_period']} to {st['end_period']}.")
    else:
        fact = (f"{agg_word.capitalize()} {m} went from {fmt_num(st['start'], unit)} in {st['start_period']} to "
                f"{fmt_num(st['end'], unit)} in {st['end_period']} ({fmt_pct(pc)}) across {st['periods']} periods.")
    if hl.get("cagr") is not None:
        fact += f" Compound annual growth rate: {fmt_pct(hl['cagr'])}."
    if st.get("r_squared") is not None:
        fact += f" Linear trend fit R² = {st['r_squared']:.2f}."
    interp = {
        "increasing": f"{m} shows a sustained upward trend.",
        "decreasing": f"{m} shows a sustained downward trend.",
        "volatile": f"{m} fluctuates substantially without a clear direction.",
        "flat": f"{m} is broadly stable over the period.",
    }[direction]
    if st.get("acceleration") in ("accelerating", "decelerating", "reversing"):
        interp += f" The rate of change is {st['acceleration']} in the more recent half of the period."
    if incomplete:
        interp += " The latest period looks incomplete and was excluded from the trend calculation."
    magnitude = min(1.0, abs(pc or 0) / 60)
    if direction in ("flat",) and magnitude < 0.15:
        magnitude *= 0.5
    span = f"{hl['start_period']}–{hl['end_period']}"
    title = (f"{m} {'grew' if (pc or 0) > 0 else 'declined'} {fmt_pct(abs(pc or 0), signed=False)} ({span})"
             if pc is not None and abs(pc) >= 2 else f"{m} was broadly stable ({span})")
    out.append(Finding(
        category="trend", title=title, fact=fact, interpretation=interp,
        implication=f"Changes in {m} directly affect performance tracked in {ds.file_name}." if role in IMPACT_BY_ROLE else "",
        hypothesis="" if direction == "flat" else f"The movement may be explained by changes in the mix of the underlying {', '.join(dims[:2]) or 'segments'}, pricing or external demand.",
        validation=f"Break {m} down by {', '.join(dims[:3]) or 'other dimensions'} and check external drivers before acting." if direction != "flat" else "",
        confidence=_confidence(st, ds.approximate, incomplete), evidence=[ev], magnitude=magnitude,
        impact=IMPACT_BY_ROLE.get(role, 0.5), tags=["trend", "change", role, m.lower()], chart=chart,
        details={"dataset": ds.name, "measure": m, "unit": unit, "stats": {k: v for k, v in st.items() if k not in ("series", "moving_average")}},
        key=f"trend:{ds.id}:{m}",
        records={"trend": {"dataset_id": ds.id, "measure": m, "dimension": "", "dimension_value": "", "stats": st}}))

    # ----- by dimension
    for d in dims:
        levels = ds.df[d].dropna()
        if levels.nunique() < 2:
            continue
        piv = ds.df.groupby([periods, ds.df[d]])[m]
        piv = (piv.sum(min_count=1) if ds.agg(m) == "sum" else piv.mean()).unstack(d).sort_index()
        if incomplete and len(piv) > 3:
            piv = piv.iloc[:-1]
        if len(piv) < 2:
            continue
        level_stats = {}
        for lvl in piv.columns:
            stl = trend_stats(piv[lvl], gran)
            if not stl:
                continue
            hl = _headline(ds, m, stl, ds.df[d] == lvl)
            stl["headline"] = hl
            stl["series_pct_change"] = stl["pct_change"]
            for k in ("start", "end", "start_period", "end_period", "pct_change", "cagr"):
                stl[k] = hl.get(k)
            if stl["pct_change"] is not None:
                level_stats[lvl] = stl
        if len(level_stats) < 2:
            continue
        if ds.agg(m) == "sum":
            totals = piv.sum()
            ann = {lvl: annual_series(ds, m, ds.df[d] == lvl) for lvl in piv.columns}
            if all(a is not None and len(a) >= 2 for a in ann.values()):
                annual = pd.DataFrame(ann).fillna(0)
                share_first, share_last = share_table(annual.iloc[0]), share_table(annual.iloc[-1])
                share_span = (annual.index[0], annual.index[-1])
                total_change_override = float(annual.iloc[-1].sum() - annual.iloc[0].sum())
            else:
                share_first = share_table(piv.iloc[0].fillna(0))
                share_last = share_table(piv.iloc[-1].fillna(0))
                share_span = (piv.index[0], piv.index[-1])
                total_change_override = None
            base_share = share_table(totals)
            eligible = [l for l in level_stats if base_share.get(l, 0) >= 3]
        else:
            share_first = share_last = None
            share_span, total_change_override = (piv.index[0], piv.index[-1]), None
            eligible = list(level_stats)
        if not eligible:
            continue
        ranked = sorted(eligible, key=lambda l: level_stats[l]["pct_change"])
        top_up, top_down = ranked[-1], ranked[0]
        total_change = total_change_override if total_change_override is not None else ((piv.iloc[-1].sum() - piv.iloc[0].sum()) if ds.agg(m) == "sum" else None)
        series_levels = sorted(level_stats, key=lambda l: -float(piv[l].sum()))[:6]
        chart = {"type": "line", "title": f"{m} by {d} over time", "question": f"Which {d} values are driving the change in {m}?",
                 "x_label": ds.time_col, "y_label": m, "unit": unit, "categories": [str(i) for i in piv.index],
                 "series": [{"name": str(l), "values": [None if pd.isna(v) else float(v) for v in piv[l]]} for l in series_levels]}
        for lvl, label in ((top_up, "fastest-growing"), (top_down, "fastest-declining")):
            stl = level_stats[lvl]
            pcl = stl["pct_change"]
            if label == "fastest-growing" and pcl <= 2:
                continue
            if label == "fastest-declining" and pcl >= -2:
                continue
            mask = ds.df[d] == lvl
            fact = (f"{m} for {d} = '{lvl}' went from {fmt_num(stl['start'], unit)} ({stl['start_period']}) to "
                    f"{fmt_num(stl['end'], unit)} ({stl['end_period']}), {fmt_pct(pcl)}")
            if stl.get("cagr") is not None and stl["periods"] >= 3:
                fact += f" (CAGR {fmt_pct(stl['cagr'])})"
            fact += "."
            interp = f"'{lvl}' is the {label} {d} for {m} among {len(eligible)} with a meaningful base."
            if share_first is not None:
                sf, sl = float(share_first.get(lvl, 0)), float(share_last.get(lvl, 0))
                fact += f" Its share of {m} moved from {sf:.1f}% to {sl:.1f}%."
                if total_change:
                    contrib = (stl["end"] - stl["start"]) / total_change * 100 if total_change else None
                    if contrib is not None and abs(contrib) < 500:
                        fact += f" It accounts for {contrib:.0f}% of the total change in {m}."
            hyp = (f"Growth in '{lvl}' may reflect rising demand, distribution gains or marketing support."
                   if pcl > 0 else f"The decline in '{lvl}' may reflect weakening demand, competitive pressure, pricing or reduced support.")
            out.append(Finding(
                category="trend", title=f"{lvl}: {m} {fmt_pct(pcl)} ({stl['start_period']}–{stl['end_period']})", fact=fact,
                interpretation=interp,
                implication=(f"'{lvl}' is becoming more important to overall {m}." if pcl > 0 else f"'{lvl}' is losing weight in overall {m}; if it is a core line this erodes performance."),
                hypothesis=hyp, validation=f"Check what changed for '{lvl}' (price, distribution, promotion, competitor activity) and whether the trend holds in the latest periods.",
                confidence=_confidence(stl, ds.approximate, incomplete), evidence=[ds.evidence([ds.time_col, d, m], mask, filt={d: lvl})],
                magnitude=min(1.0, abs(pcl) / 70), impact=IMPACT_BY_ROLE.get(role, 0.5),
                tags=["trend", "change", role, m.lower(), d.lower(), str(lvl).lower(), "growing" if pcl > 0 else "declining",
                      ds.role(d)], chart=chart,
                details={"dataset": ds.name, "measure": m, "dimension": d, "value": lvl, "unit": unit,
                         "stats": {k: v for k, v in stl.items() if k not in ("series", "moving_average")}},
                key=f"trend:{ds.id}:{m}:{d}:{lvl}",
                records={"trend": {"dataset_id": ds.id, "measure": m, "dimension": d, "dimension_value": str(lvl), "stats": stl}}))
        # share shift summary (composition) – stacked bar
        if share_first is not None and mode != "quick":
            shift = (share_last - share_first).dropna()
            if not shift.empty and shift.abs().max() >= 3:
                gainer, loser = shift.idxmax(), shift.idxmin()
                comp_chart = {"type": "stacked_bar", "title": f"Share of {m} by {d}", "question": f"How has the {d} mix of {m} shifted?",
                              "x_label": ds.time_col, "y_label": f"% of {m}", "unit": "%", "categories": [str(i) for i in piv.index],
                              "series": [{"name": str(l), "values": [float(share_table(piv.loc[i].fillna(0)).get(l, 0)) for i in piv.index]}
                                         for l in series_levels]}
                out.append(Finding(
                    category="change", title=f"{d} mix of {m} shifted towards '{gainer}'",
                    fact=(f"Between {share_span[0]} and {share_span[1]}, '{gainer}' gained {shift[gainer]:.1f} percentage points of {m} share "
                          f"(to {share_last[gainer]:.1f}%), while '{loser}' lost {abs(shift[loser]):.1f} points (to {share_last[loser]:.1f}%)."),
                    interpretation=f"The composition of {m} is changing, not just its level.",
                    implication=f"Plans and resources allocated by {d} based on the historical mix may no longer match where {m} comes from.",
                    hypothesis=f"Customer preference, availability or investment may be moving towards '{gainer}'.",
                    validation=f"Confirm the shift with the latest data and compare with investment by {d}.",
                    confidence="high" if len(piv) >= 4 and not ds.approximate else "medium",
                    evidence=[ds.evidence([ds.time_col, d, m])], magnitude=min(1.0, abs(shift[gainer]) / 15),
                    impact=IMPACT_BY_ROLE.get(role, 0.5), tags=["change", "trend", "mix", "share", d.lower(), str(gainer).lower(), str(loser).lower(), m.lower(), ds.role(d)],
                    chart=comp_chart, key=f"mix:{ds.id}:{m}:{d}",
                    details={"dataset": ds.name, "measure": m, "dimension": d,
                             "share_first": {str(k): float(v) for k, v in share_first.items()},
                             "share_last": {str(k): float(v) for k, v in share_last.items()}}))
    return out


def _wide_trends(ds: DatasetView, base: str, cols: dict, mode: str) -> list[Finding]:
    out = []
    periods = sorted(cols)
    first, last = cols[periods[0]], cols[periods[-1]]
    role = ds.role(first)
    unit = ds.unit(first)
    tot = {p: float(ds.df[cols[p]].sum()) if ds.additive(first) else float(ds.df[cols[p]].mean()) for p in periods}
    pc = pct_change(tot[periods[0]], tot[periods[-1]])
    if pc is None:
        return out
    chart = {"type": "bar", "title": f"{base} by period", "question": f"How has {base} changed?", "x_label": "Period",
             "y_label": base, "unit": unit, "categories": periods, "series": [{"name": base, "values": [tot[p] for p in periods]}]}
    out.append(Finding(
        category="trend", title=f"{base} {'grew' if pc > 0 else 'declined'} {abs(pc):.1f}% ({periods[0]}–{periods[-1]})",
        fact=f"Total {base} went from {fmt_num(tot[periods[0]], unit)} in {periods[0]} to {fmt_num(tot[periods[-1]], unit)} in {periods[-1]} ({fmt_pct(pc)}).",
        interpretation="Only " + str(len(periods)) + " periods are available, so this is a change between points rather than an established trend." if len(periods) < 3 else "",
        confidence="medium" if not ds.approximate else "low", evidence=[ds.evidence(list(cols.values()))],
        magnitude=min(1.0, abs(pc) / 40), impact=IMPACT_BY_ROLE.get(role, 0.5), tags=["trend", "change", role, base.lower()],
        chart=chart, key=f"wtrend:{ds.id}:{base}", details={"dataset": ds.name, "measure": base},
        records={"trend": {"dataset_id": ds.id, "measure": base, "dimension": "", "dimension_value": "",
                           "stats": {"start_period": periods[0], "end_period": periods[-1], "start": tot[periods[0]], "end": tot[periods[-1]],
                                     "abs_change": tot[periods[-1]] - tot[periods[0]], "pct_change": pc, "direction": "increasing" if pc > 0 else "decreasing",
                                     "series": [{"period": p, "value": tot[p]} for p in periods]}}}))
    for d in ds.dims(limit=1 if mode == "quick" else 3):
        g = ds.df.groupby(d)[[first, last]].sum(min_count=1) if ds.additive(first) else ds.df.groupby(d)[[first, last]].mean()
        g = g.dropna()
        if len(g) < 2:
            continue
        share = share_table(g[last]) if ds.additive(first) else None
        g["pct"] = (g[last] - g[first]) / g[first].abs().replace(0, np.nan) * 100
        g = g.dropna(subset=["pct"])
        if share is not None:
            g = g[share.reindex(g.index).fillna(0) >= 2]
        if len(g) < 2:
            continue
        g = g.sort_values("pct")
        chart = {"type": "bar", "title": f"{base}: change {periods[0]}→{periods[-1]} by {d}", "question": f"Which {d} values grew or declined?",
                 "x_label": d, "y_label": "% change", "unit": "%", "categories": [str(i) for i in g.index],
                 "series": [{"name": "% change", "values": [float(v) for v in g["pct"]]}]}
        for lvl in (g.index[-1], g.index[0]):
            v = float(g.loc[lvl, "pct"])
            if abs(v) < 3:
                continue
            mask = ds.df[d] == lvl
            out.append(Finding(
                category="trend", title=f"{lvl}: {base} {fmt_pct(v)} ({periods[0]}–{periods[-1]})",
                fact=(f"{base} for {d} = '{lvl}' went from {fmt_num(float(g.loc[lvl, first]), unit)} to {fmt_num(float(g.loc[lvl, last]), unit)} "
                      f"({fmt_pct(v)}), versus {fmt_pct(pc)} overall."),
                interpretation=f"'{lvl}' is {'outperforming' if v > (pc or 0) else 'underperforming'} the overall change.",
                hypothesis="Category-level demand, competition or pricing may explain the difference.",
                validation=f"Check whether the {periods[0]}–{periods[-1]} change for '{lvl}' persists with more periods of data.",
                confidence="medium" if not ds.approximate else "low", evidence=[ds.evidence([d, first, last], mask, filt={d: lvl})],
                magnitude=min(1.0, abs(v) / 50), impact=IMPACT_BY_ROLE.get(role, 0.5),
                tags=["trend", "change", role, base.lower(), d.lower(), str(lvl).lower(), "growing" if v > 0 else "declining", ds.role(d)],
                chart=chart, key=f"wtrend:{ds.id}:{base}:{d}:{lvl}",
                details={"dataset": ds.name, "measure": base, "dimension": d, "value": lvl},
                records={"trend": {"dataset_id": ds.id, "measure": base, "dimension": d, "dimension_value": str(lvl),
                                   "stats": {"start_period": periods[0], "end_period": periods[-1], "start": float(g.loc[lvl, first]),
                                             "end": float(g.loc[lvl, last]), "pct_change": v, "direction": "increasing" if v > 0 else "decreasing"}}}))
    return out
