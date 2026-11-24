"""Ask Your Data: deterministic question answering over a project's data and latest findings.

Every answer separates computed FACTS from HYPOTHESES, cites its evidence, and says
"The available data is insufficient to determine this." when that is the case.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .documents import _entities_in, _known_values
from .marketing import _ratio_table, marketing_datasets
from .model import DatasetView, ProjectData, fmt_num, fmt_pct
from .questions import detect_intents
from .stats import pct_change
from .trends import annual_series

INSUFFICIENT = "The available data is insufficient to determine this."
MEASURE_WORDS = {
    "revenue": r"revenue|sales|turnover|income", "spend": r"spend|cost|budget|investment", "roas": r"roas|return on ad",
    "units": r"units|volume", "conversions": r"conversions?|orders", "clicks": r"clicks", "impressions": r"impressions",
    "price": r"price|pricing", "market_size": r"market size|market value|size", "market_share": r"share",
}
DIM_WORDS = {"product": r"products?|skus?|items?", "category": r"categor|segments?", "channel": r"channels?|platforms?|media",
             "geography": r"regions?|markets?|countr|geograph", "campaign": r"campaigns?", "brand": r"brands?|competitors?",
             "attribute": r"formats?|flavou?rs?"}


def _measure_roles(q: str) -> list[str]:
    return [r for r, pat in MEASURE_WORDS.items() if re.search(pat, q)]


def _dim_roles(q: str) -> list[str]:
    return [r for r, pat in DIM_WORDS.items() if re.search(pat, q)]


def _years(q: str) -> list[int]:
    return [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", q)]


def _locate(pdata: ProjectData, value: str) -> list[tuple[DatasetView, str]]:
    out = []
    for ds in pdata.datasets:
        if ds.approximate:
            continue
        for c in ds.columns.values():
            if c.dtype == "categorical" and (ds.df[c.name].dropna().str.lower() == value).any():
                out.append((ds, c.name))
    out.sort(key=lambda t: -len(t[0].df))
    return out


def _actual(ds: DatasetView, col: str, value: str) -> str:
    s = ds.df[col].dropna()
    m = s[s.str.lower() == value]
    return m.iloc[0] if len(m) else value


def _pick_measure(ds: DatasetView, roles: list[str]) -> str | None:
    for r in roles:
        if r == "roas":
            continue
        cols = [c.name for c in ds.columns.values() if c.role == r and c.dtype == "numeric"]
        if cols:
            return cols[-1]  # for period columns (Sales 2024, Sales 2025) the latest
    wide = _wide_measure(ds)
    if wide and ds.role(wide) in ("revenue", "units", "market_size", "measure"):
        return wide
    ms = ds.measures(limit=3)
    return ms[0] if ms else wide


def _latest_total(ds: DatasetView, m: str, mask=None) -> tuple[float, str]:
    """Total (or average) of a measure in the latest complete year if there is a time column, else overall."""
    df = ds.df if mask is None else ds.df[mask]
    label = "all periods"
    if ds.time_col:
        yy = pd.to_datetime(df[ds.time_col]).dt.year
        if yy.notna().any():
            y = int(pd.to_datetime(ds.df[ds.time_col]).dt.year.max())
            df, label = df[yy == y], str(y)
    else:
        for base, cols in (ds.period_groups or {}).items():
            if m in cols.values():
                label = next(p for p, c in cols.items() if c == m)
    v = float(df[m].sum()) if ds.additive(m) else float(df[m].mean())
    return v, label


def _change(ds: DatasetView, m: str, mask=None) -> tuple[float | None, str, float | None, float | None]:
    ann = annual_series(ds, m, mask)
    if ann is not None and len(ann) >= 2:
        return pct_change(float(ann.iloc[-2]), float(ann.iloc[-1])), f"{ann.index[-2]}→{ann.index[-1]}", float(ann.iloc[-2]), float(ann.iloc[-1])
    if ds.time_col:
        per = ds.periods()
        df = ds.df if mask is None else ds.df[mask]
        p = per if mask is None else per[mask]
        s = df.groupby(p)[m].sum(min_count=1) if ds.agg(m) == "sum" else df.groupby(p)[m].mean()
        s = s.dropna().sort_index()
        if len(s) >= 2:
            return pct_change(float(s.iloc[0]), float(s.iloc[-1])), f"{s.index[0]}→{s.index[-1]}", float(s.iloc[0]), float(s.iloc[-1])
    groups = list((ds.period_groups or {}).items())
    groups.sort(key=lambda g: 0 if m in g[1].values() else 1)
    for base, cols in groups[:1]:
        if cols:
            ps = sorted(cols)
            df = ds.df if mask is None else ds.df[mask]
            a, b = float(df[cols[ps[0]]].sum()), float(df[cols[ps[-1]]].sum())
            return pct_change(a, b), f"{ps[0]}→{ps[-1]}", a, b
    return None, "", None, None


def _wide_measure(ds: DatasetView) -> str | None:
    for base, cols in (ds.period_groups or {}).items():
        return cols[sorted(cols)[-1]]
    return None


def ask(pdata: ProjectData, question: str, findings: list[dict]) -> dict:
    q = question.strip()
    ql = q.lower()
    intents = detect_intents(q)
    ents = _entities_in(q, pdata)
    mroles = _measure_roles(ql)
    droles = _dim_roles(ql)
    years = _years(ql)
    try:
        if re.search(r"\bcompare|\bvs\.?\b|versus|difference between", ql) and len(ents) >= 2:
            return _compare(pdata, q, ents, mroles)
        if re.search(r"compare|vs|versus", ql) and "strateg" in ql:
            return _from_findings(q, findings, {"strategy", "plan_vs_actual"}, "How the strategy compares with the data")
        if re.search(r"\bwhy\b|driv|reason|cause|explain", ql):
            if "roas" in mroles or "roas" in ql:
                return _roas_driver(pdata, q, ents)
            if ents:
                return _why(pdata, q, ents[0], mroles)
        if re.search(r"grow|declin|increas|decreas|falling|rising", ql) and not ents:
            return _growth_ranking(pdata, q, droles, mroles, "down" if re.search(r"declin|decreas|falling", ql) else "up")
        if re.search(r"\b(top|biggest|largest|highest|lowest|best|worst|most|least)\b", ql) and not re.search(r"gaps?|risks?|opportunit", ql):
            if "roas" in mroles:
                return _roas_driver(pdata, q, ents)
            return _ranking(pdata, q, droles, mroles, ascending=bool(re.search(r"lowest|worst|least|smallest", ql)), years=years)
        if re.search(r"gaps?", ql):
            return _from_findings(q, findings, {"gap"}, "Gaps identified")
        if re.search(r"opportunit|potential|usp", ql):
            return _from_findings(q, findings, {"opportunity"}, "Opportunities identified")
        if re.search(r"risk", ql):
            return _from_findings(q, findings, {"concentration", "impact", "risk"}, "Risks identified", tag="risk")
        if re.search(r"investigat|next|research|find out", ql):
            return _from_findings(q, findings, {"gap", "opportunity", "contradiction", "strategy"}, "What to investigate next", research=True)
        if ents:
            return _entity_summary(pdata, q, ents[0], mroles, years)
        if "roas" in mroles:
            return _roas_driver(pdata, q, ents)
        if mroles:
            return _measure_total(pdata, q, mroles, years)
    except Exception as exc:  # never crash a chat answer
        return _insufficient(q, intents, f"The question could not be computed ({exc.__class__.__name__}).")
    return _retrieve(pdata, q, findings, intents)


# ----------------------------------------------------------------- handlers
def _base(question, answer, facts, evidence, confidence="high", hypotheses=None, chart=None, table=None, follow_ups=None):
    return {"question": question, "answer": answer, "facts": facts, "hypotheses": hypotheses or [], "confidence": confidence,
            "evidence": [e.to_dict() if hasattr(e, "to_dict") else e for e in evidence], "chart": chart, "table": table,
            "follow_ups": follow_ups or [], "insufficient": False, "data_needed": [], "related_finding_ids": []}


def _insufficient(q, intents, why="", needed=None):
    from .questions import DATA_NEEDED

    needed = needed or [DATA_NEEDED[i] for i in intents if i in DATA_NEEDED] or ["data that directly measures what the question asks about"]
    return {"question": q, "answer": INSUFFICIENT + (f" {why}" if why else ""), "facts": [], "hypotheses": [], "confidence": "low",
            "evidence": [], "chart": None, "table": None, "follow_ups": [], "insufficient": True,
            "data_needed": needed, "related_finding_ids": []}


def _compare(pdata, q, ents, mroles):
    # find a column containing at least two of the entities
    for ds in sorted(pdata.datasets, key=lambda d: -len(d.df)):
        if ds.approximate:
            continue
        for c in ds.columns.values():
            if c.dtype != "categorical":
                continue
            lower = ds.df[c.name].dropna().str.lower()
            present = [e for e in ents if (lower == e).any()]
            if len(present) < 2:
                continue
            col = c.name
            names = [_actual(ds, col, e) for e in present[:4]]
            facts, rows, ev = [], [], []
            is_mkt = ds in marketing_datasets(pdata) and ds.first("revenue")
            measures = [m for m in ([_pick_measure(ds, mroles)] + ds.measures(limit=3)) if m]
            measures = list(dict.fromkeys(measures))[:3]
            for n in names:
                mask = ds.df[col] == n
                row = {col: n}
                for m in measures:
                    tot, lbl = _latest_total(ds, m, mask)
                    ch, span, _, _ = _change(ds, m, mask)
                    row[m] = tot
                    row[f"{m} change"] = ch
                    facts.append(f"{n}: {m} {fmt_num(tot, ds.unit(m))} ({lbl})" + (f", change {fmt_pct(ch)} ({span})" if ch is not None else ""))
                if is_mkt:
                    r = _ratio_table(_sub(ds, mask), []).iloc[0]
                    row["ROAS"] = float(r.get("ROAS")) if pd.notna(r.get("ROAS")) else None
                    if row["ROAS"] is not None:
                        facts.append(f"{n}: ROAS {row['ROAS']:.2f}x")
                rows.append(row)
                ev.append(ds.evidence([col] + measures, mask, filt={col: n}))
            m0 = measures[0]
            chart = {"type": "bar", "title": f"{m0}: {' vs '.join(map(str, names))}", "question": q, "unit": ds.unit(m0),
                     "categories": [str(n) for n in names], "series": [{"name": m0, "values": [r[m0] for r in rows]}]}
            lead = max(rows, key=lambda r: r[m0] or 0)
            ans = f"In {ds.file_name}, '{lead[col]}' has the higher {m0} ({fmt_num(lead[m0], ds.unit(m0))}). " + " ".join(facts[:6])
            return _base(q, ans, facts, ev, "high" if not ds.approximate else "medium", chart=chart,
                         table={"columns": list(rows[0].keys()), "rows": rows},
                         hypotheses=["Differences in size may reflect distribution, pricing or investment rather than underlying demand."],
                         follow_ups=[f"Why did {names[0]} change?", f"What is driving {m0} for {names[-1]}?"])
    return _insufficient(q, ["comparison"], "The items mentioned were not found together in any one dataset.")


def _sub(ds: DatasetView, mask) -> DatasetView:
    return DatasetView(**{**ds.__dict__, "df": ds.df[mask]})


def _why(pdata, q, ent, mroles):
    locs = _locate(pdata, ent)
    for ds, col in locs:
        m = _pick_measure(ds, mroles) or _wide_measure(ds)
        if not m:
            continue
        name = _actual(ds, col, ent)
        mask = ds.df[col] == name
        ch, span, a, b = _change(ds, m, mask)
        if ch is None:
            continue
        unit = ds.unit(m)
        facts = [f"{m} for {name} changed {fmt_pct(ch)} ({span}): {fmt_num(a, unit)} → {fmt_num(b, unit)}."]
        ev = [ds.evidence([col, m] + ([ds.time_col] if ds.time_col else []), mask, filt={col: name})]
        contrib_rows = []
        # decompose by other dimensions
        for d in [x for x in ds.dims(max_levels=30, limit=5) if x != col]:
            parts = []
            for lvl in ds.df.loc[mask, d].dropna().unique():
                m2 = mask & (ds.df[d] == lvl)
                c2, _, a2, b2 = _change(ds, m, m2)
                if a2 is None or b2 is None:
                    continue
                parts.append((lvl, b2 - a2, c2))
            if len(parts) < 2 or b == a:
                continue
            parts.sort(key=lambda t: t[1])
            total = sum(p[1] for p in parts) or 1
            driver = parts[0] if ch < 0 else parts[-1]
            share = driver[1] / total * 100
            if 0 < share <= 100:
                facts.append(f"By {d}: '{driver[0]}' accounts for {share:.0f}% of the change ({fmt_num(driver[1], unit)}, {fmt_pct(driver[2])}).")
            else:
                opp = parts[-1] if ch < 0 else parts[0]
                facts.append(f"By {d}: movements offset each other — largest {'fall' if ch < 0 else 'rise'} '{driver[0]}' ({fmt_num(driver[1], unit)}), "
                             f"largest opposite move '{opp[0]}' ({fmt_num(opp[1], unit)}).")
            contrib_rows += [{"dimension": d, "value": str(l), "change": float(v), "pct_change": c} for l, v, c in parts]
            if len(contrib_rows) > 30:
                break
        hyps = [f"The change may be linked to pricing, distribution, promotion or competitor activity affecting {name} — the data does not show which."]
        premise = ""
        asked_down = bool(re.search(r"declin|fall|fell|drop|decreas|lose|lost", q.lower()))
        asked_up = bool(re.search(r"grow|grew|increas|rise|rose|gain", q.lower()))
        if (asked_down and ch > 0) or (asked_up and ch < 0):
            premise = f"Note: in {ds.file_name} {name} did not {'decline' if asked_down else 'grow'} — "
        for ods, ocol in locs[1:3]:
            om = _pick_measure(ods, mroles) or _wide_measure(ods)
            if not om:
                continue
            oc, ospan, _, _ = _change(ods, om, ods.df[ocol] == _actual(ods, ocol, ent))
            if oc is not None:
                facts.append(f"Other source — {ods.file_name}: {om} for {name} {fmt_pct(oc)} ({ospan}).")
                ev.append(ods.evidence([ocol, om], ods.df[ocol] == _actual(ods, ocol, ent), filt={ocol: name}))
        ans = premise + facts[0] + (" " + facts[1] if len(facts) > 1 else "") + " These figures describe where the change happened; they do not establish why. " \
              "To determine the cause, compare with price, distribution and marketing data for the same periods."
        top = sorted(contrib_rows, key=lambda r: r["change"])[:8] if ch < 0 else sorted(contrib_rows, key=lambda r: -r["change"])[:8]
        chart = {"type": "waterfall", "title": f"Where the change in {m} for {name} came from", "question": q, "unit": unit,
                 "categories": [f"{r['value']} ({r['dimension']})" for r in top], "series": [{"name": "Change", "values": [r["change"] for r in top]}]} if top else None
        return _base(q, ans, facts, ev, "medium", hypotheses=hyps, chart=chart,
                     table={"columns": ["dimension", "value", "change", "pct_change"], "rows": contrib_rows[:30]} if contrib_rows else None,
                     follow_ups=[f"Compare {name} with another {col.lower()}", f"Which {col.lower()} values are growing?"])
    return _insufficient(q, ["trend"], f"'{ent}' was found, but there is no time series to explain its change." if locs else f"'{ent}' was not found in the data.")


def _roas_driver(pdata, q, ents):
    mds = [d for d in marketing_datasets(pdata) if d.first("revenue")]
    if not mds:
        return _insufficient(q, ["roas"])
    ds = mds[0]
    ch = next((d for d in ds.dims(max_levels=40) if ds.role(d) == "channel"), None) or (ds.dims(max_levels=40) or [None])[0]
    if not ch:
        return _insufficient(q, ["roas"], "No channel/campaign breakdown was found.")
    g = _ratio_table(ds, [ch])
    tot = _ratio_table(ds, []).iloc[0]
    g["rev_share"] = g["revenue"] / g["revenue"].sum() * 100
    g["spend_share"] = g["spend"] / g["spend"].sum() * 100
    g = g.sort_values("rev_share", ascending=False)
    cur = ds.unit(ds.first("spend"))
    facts = [f"Overall ROAS (total revenue ÷ total spend) is {tot['ROAS']:.2f}x."]
    for i in g.index:
        facts.append(f"{i}: {g.loc[i, 'rev_share']:.1f}% of revenue from {g.loc[i, 'spend_share']:.1f}% of spend, ROAS {g.loc[i, 'ROAS']:.2f}x.")
    target = None
    for e in ents:
        m = [i for i in g.index if str(i).lower() == e]
        if m:
            target = m[0]
    top = target or g.index[0]
    rest = g.drop(index=top)
    excl = rest["revenue"].sum() / rest["spend"].sum() if rest["spend"].sum() else float("nan")
    dep = g.loc[top, "rev_share"]
    verdict = ("is a major driver" if dep >= 35 or g.loc[top, "ROAS"] > tot["ROAS"] * 1.2 else "is not the main driver")
    ans = (f"'{top}' {verdict} of ROAS: it contributes {dep:.1f}% of attributed revenue at {g.loc[top, 'ROAS']:.2f}x ROAS "
           f"(vs {tot['ROAS']:.2f}x overall). Without '{top}', ROAS would be {excl:.2f}x. "
           "This is based on attributed revenue and shows contribution, not proven causal impact.")
    chart = {"type": "bar", "title": f"ROAS by {ch}", "question": q, "unit": "x", "categories": [str(i) for i in g.index],
             "series": [{"name": "ROAS", "values": [float(v) for v in g["ROAS"]]}]}
    return _base(q, ans, facts, [ds.evidence([ch, ds.first("spend"), ds.first("revenue")])], "medium", chart=chart,
                 hypotheses=["Channels with low attributed ROAS (e.g. TV/upper-funnel) may still drive sales that attribution does not capture."],
                 table={"columns": [ch, "spend", "revenue", "ROAS", "spend_share", "rev_share"],
                        "rows": [{ch: str(i), "spend": float(g.loc[i, "spend"]), "revenue": float(g.loc[i, "revenue"]), "ROAS": float(g.loc[i, "ROAS"]),
                                  "spend_share": float(g.loc[i, "spend_share"]), "rev_share": float(g.loc[i, "rev_share"])} for i in g.index]},
                 follow_ups=["What drove the change in ROAS over time?", "Which campaigns are inefficient?"])


def _pick_dim(ds: DatasetView, droles: list[str]) -> str | None:
    for r in droles + ["product", "category", "channel", "brand", "geography", "campaign"]:
        c = next((x for x in ds.dims(max_levels=60, limit=8) if ds.role(x) == r), None)
        if c:
            return c
    d = ds.dims(max_levels=60)
    return d[0] if d else None


def _candidate_datasets(pdata, droles, mroles):
    cands = []
    for ds in pdata.datasets:
        if ds.approximate:
            continue
        d = _pick_dim(ds, droles)
        m = _pick_measure(ds, mroles) or _wide_measure(ds)
        if not d or not m:
            continue
        score = (2 if droles and ds.role(d) in droles else 0) + (2 if mroles and ds.role(m) in mroles else 0) + (1 if ds.time_col or ds.period_groups else 0)
        cands.append((score, len(ds.df), ds, d, m))
    cands.sort(key=lambda t: (-t[0], -t[1]))
    return cands


def _growth_ranking(pdata, q, droles, mroles, direction):
    notes: list[str] = []
    for _, _, ds, d, m in _candidate_datasets(pdata, droles, mroles)[:4]:
        rows = []
        for lvl in ds.df[d].dropna().unique():
            ch, span, a, b = _change(ds, m, ds.df[d] == lvl)
            if ch is not None:
                rows.append({d: str(lvl), "pct_change": ch, "from": a, "to": b, "period": span})
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: -r["pct_change"] if direction == "up" else r["pct_change"])
        sel = [r for r in rows if (r["pct_change"] > 0 if direction == "up" else r["pct_change"] < 0)]
        unit = ds.unit(m)
        if not sel:
            none_note = f"No {d} values are {'growing' if direction == 'up' else 'declining'} in {m} in {ds.file_name} ({rows[0]['period']}). "
            notes.append(none_note)
            continue
        facts = [f"{r[d]}: {fmt_pct(r['pct_change'])} ({fmt_num(r['from'], unit)} → {fmt_num(r['to'], unit)}, {r['period']})" for r in sel[:10]]
        ans = "".join(notes) + (f"In {ds.file_name}, {len(sel)} of {len(rows)} {d} values are {'growing' if direction == 'up' else 'declining'} in {m} "
               f"({sel[0]['period']}). {'Fastest' if direction == 'up' else 'Steepest'}: " + "; ".join(facts[:3]) + ".")
        chart = {"type": "bar", "title": f"{m}: change by {d} ({sel[0]['period']})", "question": q, "unit": "%",
                 "categories": [r[d] for r in rows[:15]], "series": [{"name": "% change", "values": [r["pct_change"] for r in rows[:15]]}]}
        return _base(q, ans, facts, [ds.evidence([d, m] + ([ds.time_col] if ds.time_col else []))], "high", chart=chart,
                     table={"columns": [d, "pct_change", "from", "to", "period"], "rows": rows[:40]},
                     follow_ups=[f"Why is {sel[0][d]} {'growing' if direction == 'up' else 'declining'}?"])
    if notes:
        return _base(q, " ".join(notes), notes, [], "high")
    return _insufficient(q, ["trend"])


def _ranking(pdata, q, droles, mroles, ascending, years):
    for _, _, ds, d, m in _candidate_datasets(pdata, droles, mroles):
        df = ds.df
        if years and ds.time_col:
            yy = pd.to_datetime(df[ds.time_col]).dt.year
            df = df[yy.isin(years)]
        g = (df.groupby(d)[m].sum(min_count=1) if ds.additive(m) else df.groupby(d)[m].mean()).dropna().sort_values(ascending=ascending)
        if g.empty:
            continue
        unit = ds.unit(m)
        facts = [f"{i}: {fmt_num(float(v), unit)}" for i, v in g.head(10).items()]
        ans = f"By {m} ({'total' if ds.additive(m) else 'average'}{', ' + ', '.join(map(str, years)) if years else ''}), the {'lowest' if ascending else 'top'} {d} values are: " + "; ".join(facts[:5]) + "."
        chart = {"type": "bar", "title": f"{m} by {d}", "question": q, "unit": unit, "categories": [str(i) for i in g.head(15).index],
                 "series": [{"name": m, "values": [float(v) for v in g.head(15).values]}]}
        return _base(q, ans, facts, [ds.evidence([d, m])], "high", chart=chart,
                     table={"columns": [d, m], "rows": [{d: str(i), m: float(v)} for i, v in g.head(40).items()]})
    return _insufficient(q, ["comparison"])


def _measure_total(pdata, q, mroles, years):
    for ds in sorted([d for d in pdata.datasets if not d.approximate], key=lambda d: (-(1 if d.time_col else 0), -len(d.df))):
        m = next((ds.first(r) for r in mroles if r != "roas" and ds.first(r)), None)
        if not m:
            continue
        df = ds.df
        if years and ds.time_col:
            df = df[pd.to_datetime(df[ds.time_col]).dt.year.isin(years)]
        tot = float(df[m].sum()) if ds.additive(m) else float(df[m].mean())
        ch, span, _, _ = _change(ds, m)
        facts = [f"{'Total' if ds.additive(m) else 'Average'} {m}{' in ' + ', '.join(map(str, years)) if years else ''}: {fmt_num(tot, ds.unit(m))} ({ds.file_name})."]
        if ch is not None:
            facts.append(f"Change {span}: {fmt_pct(ch)}.")
        return _base(q, " ".join(facts), facts, [ds.evidence([m])], "high")
    return _insufficient(q, ["general"])


def _entity_summary(pdata, q, ent, mroles, years):
    locs = _locate(pdata, ent)
    if not locs:
        return _insufficient(q, ["general"], f"'{ent}' was not found in the data.")
    facts, ev = [], []
    for ds, col in locs[:3]:
        name = _actual(ds, col, ent)
        mask = ds.df[col] == name
        m = _pick_measure(ds, mroles) or _wide_measure(ds)
        if not m:
            continue
        tot, lbl = _latest_total(ds, m, mask)
        all_tot, _ = _latest_total(ds, m)
        share = tot / all_tot * 100 if ds.additive(m) and all_tot else None
        ch, span, _, _ = _change(ds, m, mask)
        facts.append(f"{ds.file_name}: {name} — {m} {fmt_num(tot, ds.unit(m))} in {lbl}" + (f" ({share:.1f}% of total)" if share is not None else "")
                     + (f", {fmt_pct(ch)} ({span})" if ch is not None else "") + ".")
        ev.append(ds.evidence([col, m], mask, filt={col: name}))
    if not facts:
        return _insufficient(q, ["general"])
    return _base(q, " ".join(facts), facts, ev, "high", follow_ups=[f"Why did {ent} change?", f"Compare {ent} with another option"])


def _from_findings(q, findings, cats, label, tag=None, research=False):
    sel = [f for f in findings if f["category"] in cats or (tag and tag in (f.get("tags_json") or []))]
    if not sel:
        return _insufficient(q, ["general"], "No matching findings exist yet — run an analysis first, or the data does not show any.")
    sel = sel[:6]
    facts = [f"{f['title']} [{f['confidence']}]" for f in sel]
    ev = [e for f in sel for e in (f.get("evidence") or [])[:1]]
    ans = f"{label} (from the latest analysis): " + "; ".join(f["title"] for f in sel[:4]) + "."
    if research:
        ans += " Each is listed with the validation step needed."
        facts = [f"{f['title']} → {f.get('validation') or 'investigate further'}" for f in sel]
    out = _base(q, ans, facts, ev, sel[0]["confidence"], hypotheses=[f["hypothesis"] for f in sel if f.get("hypothesis")][:3])
    out["related_finding_ids"] = [f["id"] for f in sel]
    return out


def _retrieve(pdata, q, findings, intents):
    """Fallback: find the most relevant findings and document passages."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    docs, refs = [], []
    for f in findings[:200]:
        docs.append(f"{f['title']} {f['fact']}")
        refs.append(("finding", f))
    for d in pdata.documents:
        for ch in d.chunks:
            if len(ch.text) >= 30:
                docs.append(ch.text)
                refs.append(("chunk", (d, ch)))
    if not docs:
        return _insufficient(q, intents)
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    try:
        X = vec.fit_transform(docs + [q])
    except ValueError:
        return _insufficient(q, intents)
    sims = (X[:-1] @ X[-1].T).toarray().ravel()
    order = np.argsort(sims)[::-1][:5]
    if sims[order[0]] < 0.12:
        return _insufficient(q, intents)
    facts, ev, related = [], [], []
    for i in order:
        if sims[i] < 0.08:
            continue
        kind, ref = refs[i]
        if kind == "finding":
            facts.append(f"Finding: {ref['title']}")
            ev += (ref.get("evidence") or [])[:1]
            related.append(ref["id"])
        else:
            d, ch = ref
            facts.append(f"{d.chunk_label(ch)}: \"{ch.text[:300]}\"")
            ev.append(d.evidence(ch))
    ans = "No direct calculation matched this question; the most relevant evidence is shown below. " + (facts[0] if facts else "")
    out = _base(q, ans, facts, ev, "low")
    out["related_finding_ids"] = related
    return out
