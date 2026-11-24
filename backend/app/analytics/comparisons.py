"""Comparison engine: period vs period, segment vs segment, plan vs actual, source vs source; concentration."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .model import DatasetView, Finding, ProjectData, fmt_num, fmt_pct
from .stats import hhi, pct_change, share_table
from .trends import IMPACT_BY_ROLE

STABLE_PCT = 2.0


def analyse_comparisons(pdata: ProjectData, mode: str) -> list[Finding]:
    out: list[Finding] = []
    for ds in pdata.datasets:
        out += period_comparison(ds, mode)
        out += segment_comparison(ds, mode)
        out += plan_vs_actual(ds)
    if mode != "quick":
        out += source_comparison(pdata)
    return out


def period_comparison(ds: DatasetView, mode: str) -> list[Finding]:
    """First vs last period by dimension: improved / declined / stable / added / removed."""
    out: list[Finding] = []
    t = ds.time_col
    if not t:
        return out
    periods = ds.periods()
    plist = sorted(periods.dropna().unique())
    if len(plist) < 2:
        return out
    # compare complete years where possible, else first vs last period
    gran = ds.columns[t].granularity
    if gran in ("month", "quarter", "week", "day"):
        years = pd.to_datetime(ds.df[t]).dt.year
        per_year = ds.df.groupby(years)[t].apply(lambda x: periods[x.index].nunique())
        full = [y for y, n in per_year.items() if n >= per_year.max() * 0.9]
        if len(full) >= 2:
            labels = years.astype("Int64").astype(str)
            a, b = str(full[-2]), str(full[-1])  # most recent complete year-on-year
        else:
            labels, a, b = periods, plist[0], plist[-1]
    else:
        labels, a, b = periods, plist[-2], plist[-1]
    for m in ds.measures(limit=3 if mode == "quick" else 5):
        if ds.role(m) in ("roas", "ctr", "cpc", "cpm", "cpa", "cvr") and ds.first("spend"):
            continue
        unit = ds.unit(m)
        agg = ds.agg(m)
        for d in ds.dims(limit=1 if mode == "quick" else 3):
            g = ds.df.groupby([labels, ds.df[d]])[m]
            g = (g.sum(min_count=1) if agg == "sum" else g.mean()).unstack(0)
            if a not in g.columns or b not in g.columns:
                continue
            va, vb = g[a], g[b]
            def present(v) -> bool:
                return pd.notna(v) and (agg != "sum" or float(v) != 0)

            added = [str(i) for i in g.index if not present(va.get(i)) and present(vb.get(i))]
            removed = [str(i) for i in g.index if present(va.get(i)) and not present(vb.get(i))]
            both = [i for i in g.index if str(i) not in added and str(i) not in removed and pd.notna(va.get(i)) and pd.notna(vb.get(i))]
            changes = {str(i): pct_change(float(va[i]), float(vb[i])) for i in both}
            changes = {k: v for k, v in changes.items() if v is not None}
            if not changes:
                continue
            improved = sorted([k for k, v in changes.items() if v > STABLE_PCT], key=lambda k: -changes[k])
            declined = sorted([k for k, v in changes.items() if v < -STABLE_PCT], key=lambda k: changes[k])
            stable = [k for k, v in changes.items() if abs(v) <= STABLE_PCT]
            if not (improved or declined or added or removed):
                continue
            higher_is_better = ds.role(m) not in ("cpa", "cpc", "cpm", "spend", "price")
            up_word, down_word = ("improved", "declined") if higher_is_better else ("increased", "decreased")
            parts = []
            if improved:
                parts.append(f"{up_word}: " + ", ".join(f"{k} ({fmt_pct(changes[k])})" for k in improved[:5]))
            if declined:
                parts.append(f"{down_word}: " + ", ".join(f"{k} ({fmt_pct(changes[k])})" for k in declined[:5]))
            if stable:
                parts.append("stayed about the same: " + ", ".join(stable[:5]))
            if added:
                parts.append("new in " + str(b) + ": " + ", ".join(added[:5]))
            if removed:
                parts.append("no longer present in " + str(b) + ": " + ", ".join(removed[:5]))
            cats = [str(i) for i in g.index if str(i) in changes][:12]
            chart = {"type": "grouped_bar", "title": f"{m} by {d}: {a} vs {b}", "question": f"What changed between {a} and {b}?",
                     "x_label": d, "y_label": m, "unit": unit, "categories": cats,
                     "series": [{"name": str(a), "values": [float(va.get(c)) if pd.notna(va.get(c)) else None for c in g.index if str(c) in cats]},
                                {"name": str(b), "values": [float(vb.get(c)) if pd.notna(vb.get(c)) else None for c in g.index if str(c) in cats]}]}
            tot_a = float(va.sum()) if agg == "sum" else float(va.mean())
            tot_b = float(vb.sum()) if agg == "sum" else float(vb.mean())
            spread = max(changes.values()) - min(changes.values())
            out.append(Finding(
                category="comparison", title=f"{a} vs {b}: {m} by {d}",
                fact=f"Comparing {a} with {b} ({m}, {'total' if agg == 'sum' else 'average'} {fmt_num(tot_a, unit)} → {fmt_num(tot_b, unit)}, {fmt_pct(pct_change(tot_a, tot_b))}): " + "; ".join(parts) + ".",
                interpretation=(f"Performance diverges across {d}: a {spread:.0f}-point spread between the best and worst change." if spread > 10
                                else f"Changes are fairly uniform across {d}."),
                implication=f"Segments that {down_word} are where {m} is being lost; segments that {up_word} show where momentum is." ,
                validation="Check whether the differences persist in the latest months and what drove the segments at each end.",
                confidence="high" if not ds.approximate else "medium", evidence=[ds.evidence([ds.time_col, d, m])],
                magnitude=min(1.0, spread / 60), impact=IMPACT_BY_ROLE.get(ds.role(m), 0.5),
                tags=["comparison", "change", "time comparison", m.lower(), d.lower(), ds.role(d), ds.role(m)] + [k.lower() for k in improved[:3] + declined[:3]],
                chart=chart, key=f"pcmp:{ds.id}:{m}:{d}",
                details={"dataset": ds.name, "a": str(a), "b": str(b), "improved": improved, "declined": declined, "stable": stable,
                         "added": added, "removed": removed, "changes": changes},
                records={"comparison": {"comparison_type": "time", "subject_a": str(a), "subject_b": str(b), "measure": m,
                                        "value_a": tot_a, "value_b": tot_b, "details": {"dimension": d, "improved": improved,
                                                                                         "declined": declined, "stable": stable, "added": added, "removed": removed}}}))
    return out


def segment_comparison(ds: DatasetView, mode: str) -> list[Finding]:
    """Cross-sectional comparison and concentration of a measure across a dimension."""
    out: list[Finding] = []
    for m in ds.measures(limit=2 if mode == "quick" else 4):
        if not ds.additive(m):
            continue
        unit = ds.unit(m)
        for d in ds.dims(max_levels=30, limit=2 if mode == "quick" else 4):
            g = ds.df.groupby(d)[m].sum(min_count=1).dropna().sort_values(ascending=False)
            if len(g) < 3 or g.sum() <= 0:
                continue
            sh = share_table(g)
            h = hhi(sh)
            top, top_share = str(g.index[0]), float(sh.iloc[0])
            top2 = float(sh.iloc[:2].sum())
            concentrated = top_share >= 40 or h >= 0.25
            even = 100 / len(g)
            chart = {"type": "bar", "title": f"{m} by {d}", "question": f"Which {d} contributes most to {m}?",
                     "x_label": d, "y_label": m, "unit": unit, "categories": [str(i) for i in g.index[:12]],
                     "series": [{"name": m, "values": [float(v) for v in g.values[:12]]}]}
            fact = (f"'{top}' accounts for {top_share:.1f}% of total {m} ({fmt_num(float(g.iloc[0]), unit)} of {fmt_num(float(g.sum()), unit)}); "
                    f"the top two {d} values account for {top2:.1f}%. There are {len(g)} {d} values (an even split would be {even:.1f}% each). "
                    f"Concentration index (HHI) = {h:.2f}.")
            out.append(Finding(
                category="concentration" if concentrated else "comparison",
                title=(f"{m} is concentrated in '{top}' ({top_share:.0f}%)" if concentrated else f"{m} by {d}: '{top}' leads with {top_share:.0f}%"),
                fact=fact,
                interpretation=("Results depend heavily on a single " + d + "." if concentrated else f"{m} is spread across several {d} values."),
                implication=("A setback in '" + top + "' would have an outsized effect on total " + m + "." if concentrated else ""),
                validation=(f"Assess the health and risks of '{top}' and the potential of the smaller {d} values." if concentrated else ""),
                confidence="high" if not ds.approximate else "medium", evidence=[ds.evidence([d, m])],
                magnitude=min(1.0, (top_share - even) / 60) if concentrated else 0.15, impact=IMPACT_BY_ROLE.get(ds.role(m), 0.5),
                tags=["comparison", "concentration" if concentrated else "segment", d.lower(), ds.role(d), m.lower(), ds.role(m), top.lower()],
                chart=chart, key=f"seg:{ds.id}:{m}:{d}",
                details={"dataset": ds.name, "shares": {str(k): float(v) for k, v in sh.items()}, "hhi": h},
                records={"comparison": {"comparison_type": "segment", "subject_a": top, "subject_b": str(g.index[-1]), "measure": m,
                                        "value_a": float(g.iloc[0]), "value_b": float(g.iloc[-1]),
                                        "details": {"dimension": d, "shares": {str(k): float(v) for k, v in sh.items()}}}}))
    return out


_PLAN_RE = re.compile(r"\b(plan|planned|target|budget|forecast|goal)\b", re.I)
_ACT_RE = re.compile(r"\b(actual|actuals|achieved|outturn|delivered)\b", re.I)


def _base(name: str) -> str:
    return " ".join(_ACT_RE.sub("", _PLAN_RE.sub("", name)).split()).lower()


def plan_vs_actual(ds: DatasetView) -> list[Finding]:
    out: list[Finding] = []
    num = [c for c in ds.columns.values() if c.dtype == "numeric"]
    plans = [c.name for c in num if _PLAN_RE.search(c.name)]
    acts = [c.name for c in num if _ACT_RE.search(c.name)]
    pairs = []
    for p in plans:
        match = [a for a in acts if _base(a) == _base(p)] or (acts if len(acts) == 1 and len(plans) == 1 else [])
        if match:
            pairs.append((p, match[0]))
    for p, a in pairs:
        unit = ds.unit(a) or ds.unit(p)
        tp, ta = float(ds.df[p].sum()), float(ds.df[a].sum())
        var = pct_change(tp, ta)
        dims = ds.dims(max_levels=40, limit=1)
        evid = [ds.evidence([p, a])]
        detail_txt, chart, worst, best = "", None, None, None
        if dims:
            d = dims[0]
            g = ds.df.groupby(d)[[p, a]].sum()
            g["var_pct"] = (g[a] - g[p]) / g[p].abs().replace(0, np.nan) * 100
            g = g.dropna().sort_values("var_pct")
            if len(g):
                worst, best = g.index[0], g.index[-1]
                detail_txt = (f" Largest shortfall: '{worst}' ({fmt_pct(float(g.loc[worst, 'var_pct']))}, {fmt_num(float(g.loc[worst, a] - g.loc[worst, p]), unit)}). "
                              f"Largest over-delivery: '{best}' ({fmt_pct(float(g.loc[best, 'var_pct']))}).")
                chart = {"type": "waterfall", "title": f"Actual vs plan by {d}", "question": f"Where did {a} beat or miss plan?",
                         "x_label": d, "y_label": f"Variance ({unit or 'units'})", "unit": unit, "categories": [str(i) for i in g.index],
                         "series": [{"name": "Variance", "values": [float(g.loc[i, a] - g.loc[i, p]) for i in g.index]}]}
                behind = [str(i) for i in g.index if g.loc[i, "var_pct"] < -5]
            else:
                behind = []
        else:
            behind = []
        out.append(Finding(
            category="plan_vs_actual", title=f"Actual {'ahead of' if ta >= tp else 'behind'} plan by {abs(var or 0):.1f}% ({a})",
            fact=f"Total actual {fmt_num(ta, unit)} versus plan {fmt_num(tp, unit)} ({fmt_pct(var)}).{detail_txt}",
            interpretation=("Execution is behind plan overall." if ta < tp else "Execution is ahead of plan overall.") +
                           (f" Under-delivery is concentrated in {', '.join(behind[:4])}." if behind else ""),
            implication="Plans built on the original targets may need re-forecasting." if ta < tp else "Targets may have been set conservatively.",
            hypothesis="Shortfalls may stem from optimistic targets, execution issues or market changes.",
            validation="Review the assumptions behind the plan for the segments with the largest variances.",
            confidence="high" if not ds.approximate else "medium", evidence=evid, magnitude=min(1.0, abs(var or 0) / 30),
            impact=0.9, tags=["plan vs actual", "execution", "strategy", "comparison", "gap", p.lower(), a.lower()] + ([str(worst).lower()] if worst is not None else []),
            chart=chart, key=f"pva:{ds.id}:{p}:{a}",
            details={"dataset": ds.name, "plan": p, "actual": a, "behind": behind},
            records={"comparison": {"comparison_type": "plan_vs_actual", "subject_a": p, "subject_b": a, "measure": a,
                                    "value_a": tp, "value_b": ta, "details": {"behind": behind}},
                     "gaps": [{"gap_type": "execution", "gap": f"Actual below plan in {', '.join(behind[:5])}",
                               "evidence": detail_txt.strip(), "why_it_matters": "Unmet plans affect forecasts and resource allocation.",
                               "significance": "high" if (var or 0) < -10 else "medium",
                               "missing_information": "Reasons for the shortfall (distribution, pricing, demand, timing).",
                               "recommended_investigation": "Variance analysis with the commercial team for each under-delivering segment.",
                               "potential_action": "Re-forecast and re-allocate support to segments with achievable upside."}] if behind else []}))
    return out


def source_comparison(pdata: ProjectData) -> list[Finding]:
    """Compare the same kind of measure reported by two different sources for the same segments."""
    out: list[Finding] = []
    cands = []
    for ds in pdata.datasets:
        for m in ds.measures(limit=4):
            if not ds.additive(m):
                continue
            for d in ds.dims(max_levels=40, limit=3):
                cands.append((ds, m, d))
    seen = set()
    for i, (a, ma, da) in enumerate(cands):
        for b, mb, db in cands[i + 1:]:
            if a.file_id == b.file_id or a.role(ma) != b.role(mb) or a.role(da) != b.role(db) or a.role(ma) == "measure":
                continue
            key = tuple(sorted([a.id, b.id])) + (a.role(ma), a.role(da))
            if key in seen:
                continue
            ga = _latest_year_totals(a, ma, da)
            gb = _latest_year_totals(b, mb, db)
            if ga is None or gb is None:
                continue
            (ya, sa), (yb, sb) = ga, gb
            if ya != yb:
                continue
            common = [k for k in sa.index if k in sb.index]
            if len(common) < 3:
                continue
            seen.add(key)
            diffs = {str(k): pct_change(float(sa[k]), float(sb[k])) for k in common}
            diffs = {k: v for k, v in diffs.items() if v is not None}
            big = {k: v for k, v in diffs.items() if abs(v) > 15}
            if not big:
                continue
            out.append(Finding(
                category="comparison", title=f"Sources differ on {a.role(ma)} by {da} ({ya})",
                fact=(f"For {ya}, '{ma}' in {a.file_name} and '{mb}' in {b.file_name} differ by more than 15% for "
                      f"{len(big)} of {len(common)} shared {da} values (e.g. " + ", ".join(f"{k}: {fmt_pct(v)}" for k, v in list(big.items())[:3]) + ")."),
                interpretation="The two sources may measure different scopes (e.g. own sales vs total market), definitions or time windows.",
                implication="Mixing these sources without reconciling definitions could produce misleading conclusions.",
                validation="Confirm the definition and scope of each source before combining them.",
                confidence="low", evidence=[a.evidence([da, ma]), b.evidence([db, mb])], magnitude=0.3, impact=0.4,
                tags=["comparison", "source comparison", "data quality", da.lower(), a.role(ma)], key=f"src:{key}",
                records={"comparison": {"comparison_type": "source", "subject_a": a.file_name, "subject_b": b.file_name, "measure": a.role(ma),
                                        "details": {"differences_pct": diffs, "year": ya}}}))
    return out


def _latest_year_totals(ds: DatasetView, m: str, d: str):
    if ds.time_col:
        years = pd.to_datetime(ds.df[ds.time_col]).dt.year
        y = int(years.max())
        sub = ds.df[years == y]
        return y, sub.groupby(d)[m].sum(min_count=1).dropna()
    for base, cols in (ds.period_groups or {}).items():
        if m in cols.values():
            y = int(max(cols))
            return y, ds.df.groupby(d)[cols[max(cols)]].sum(min_count=1).dropna()
    return None
