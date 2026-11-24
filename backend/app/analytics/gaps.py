"""Gap analysis engine (data, market/whitespace, channel, geographic, customer, measurement, pricing gaps).

Portfolio, execution, strategy and research gaps are produced by their own engines and
merged here into one gap list.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import Evidence, Finding, ProjectData, fmt_num

NEEDS = {
    # intent: (roles needed (any-of groups), plain description)
    "roas": ([["spend"], ["revenue"]], "media spend and attributed revenue by channel and period"),
    "marketing": ([["spend"]], "media spend with outcomes (clicks, conversions or revenue)"),
    "opportunity": ([["category", "attribute"], ["brand", "company", "product", "competitor_count"]], "a list of products/competitors with their category"),
    "trend": ([["time"]], "data with a date or period column"),
    "customer": ([["customer_segment"]], "customer or audience segment data"),
    "pricing": ([["price"]], "product prices"),
    "geography": ([["geography"]], "data broken down by region, country or market"),
    "competition": ([["brand", "company", "competitor_count"]], "competitor or brand data"),
}


def roles_present(pdata: ProjectData) -> set[str]:
    roles = set()
    for ds in pdata.datasets:
        roles.update(c.role for c in ds.columns.values())
    return roles


def analyse_gaps(pdata: ProjectData, mode: str, intents: set[str]) -> list[Finding]:
    out: list[Finding] = []
    roles = roles_present(pdata)
    # ---------------- data gaps relative to what the user asked
    for intent, (groups, desc) in NEEDS.items():
        if intent not in intents:
            continue
        missing = [g for g in groups if not any(r in roles for r in g)]
        if missing:
            out.append(_gap_finding(
                "data", f"Data gap: no {desc} were found",
                f"The questions asked require {desc}, but none of the uploaded datasets contain columns recognised as {', '.join('/'.join(g) for g in missing)}.",
                "Without this data the related questions cannot be answered with evidence.",
                "high", desc, f"Upload {desc}.", f"Add a dataset containing {desc}.", tags=[intent, "data gap"],
                evidence=[Evidence(source_label="All uploaded datasets (column roles checked)", locator={"roles_found": sorted(roles)})]))
    if "customer_segment" not in roles and pdata.datasets and mode != "quick":
        out.append(_gap_finding(
            "customer", "Customer gap: no customer or audience segment data",
            "None of the datasets break results down by customer, audience or demographic segment.",
            "It is not possible to say which customers drive growth or which segments are under-served.",
            "medium", "Customer/audience segment breakdowns (age, need-state, shopper type, loyalty).",
            "Consumer research or CRM/loyalty data analysis.", "Add customer segment data to explain who drives performance.",
            tags=["customer", "data gap", "research"], evidence=[Evidence(source_label="All uploaded datasets (column roles checked)")]))
    if pdata.datasets and not any(ds.time_col or ds.period_groups for ds in pdata.datasets):
        out.append(_gap_finding(
            "data", "Data gap: no time series", "No dataset has a date/period column or period columns, so trends over time cannot be measured.",
            "Without history, growth, decline and momentum cannot be assessed.", "high", "Historical data by period.",
            "Request at least 2–3 years of history by month or quarter.", "Upload historical data.", tags=["trend", "data gap"],
            evidence=[Evidence(source_label="All uploaded datasets")]))
    # ---------------- measurement gaps in marketing data
    for ds in pdata.datasets:
        if ds.first("spend") and not ds.first("revenue") and not ds.first("conversions"):
            out.append(_gap_finding(
                "measurement", f"Measurement gap: spend without outcomes in {ds.file_name}",
                f"{ds.name} contains spend but no revenue or conversion columns.", "Efficiency (ROAS/CPA) cannot be evaluated.", "high",
                "Attributed revenue or conversions per row.", "Join platform conversion data or sales data.", "Add outcome metrics to media reporting.",
                tags=["marketing", "roas", "measurement"], evidence=[ds.evidence([ds.first("spend")])]))
        if ds.first("spend") and ds.first("revenue"):
            sp = ds.first("spend")
            # channels with spend but (almost) no clicks – offline channels whose effect attribution cannot see
            ch = next((d for d in ds.dims(max_levels=40) if ds.role(d) == "channel"), None)
            if ch and ds.first("clicks"):
                g = ds.df.groupby(ch)[[sp, ds.first("clicks")]].sum()
                offline = [str(i) for i in g.index if g.loc[i, sp] > 0 and g.loc[i, ds.first("clicks")] == 0]
                if offline:
                    out.append(_gap_finding(
                        "measurement", f"Measurement gap: {', '.join(offline)} has spend but no click tracking",
                        f"In {ds.file_name}, {', '.join(offline)} has spend recorded but zero clicks, so its revenue attribution relies on a different (or no) method.",
                        "Its ROAS is not comparable to digital channels and its true contribution is uncertain.", "high",
                        "An independent measure of offline/brand channel impact.", "Marketing mix modelling or geo-lift testing.",
                        "Do not compare offline ROAS with last-click digital ROAS directly.", tags=["marketing", "roas", "measurement", "tv"] + [o.lower() for o in offline],
                        evidence=[ds.evidence([ch, sp, ds.first("clicks")], ds.df[ch].isin(offline))]))
    # ---------------- whitespace: empty cells in dimension cross-tabs
    for ds in pdata.datasets:
        dims = [d for d in ds.dims(max_levels=15, min_levels=3, limit=5) if ds.role(d) in
                ("category", "product", "channel", "geography", "attribute", "customer_segment", "brand", "company")]
        measure = ds.first("revenue", "units", "market_size", "spend")
        for i, a in enumerate(dims):
            for b in dims[i + 1:]:
                if {ds.role(a), ds.role(b)} == {"product", "category"} or {ds.role(a), ds.role(b)} & {"brand", "company"}:
                    continue  # products belong to one category; brand gaps are covered by the portfolio-gap engine
                if measure:
                    tab = ds.df.pivot_table(index=a, columns=b, values=measure, aggfunc="sum", fill_value=0)
                else:
                    tab = pd.crosstab(ds.df[a], ds.df[b])
                if tab.shape[0] < 3 or tab.shape[1] < 3:
                    continue
                empty = [(r, c) for r in tab.index for c in tab.columns if tab.loc[r, c] == 0]
                share_empty = len(empty) / tab.size
                if not empty or share_empty > 0.5:
                    continue
                # cells empty although both the row and the column are substantial
                row_tot, col_tot = tab.sum(axis=1), tab.sum(axis=0)
                strong = [(r, c) for r, c in empty if row_tot[r] >= row_tot.median() * 0.5 and col_tot[c] >= col_tot.median() * 0.5]
                if not strong:
                    continue
                gtype = {"geography": "geographic", "channel": "distribution" if ds.role(a) in ("product", "category") or ds.role(b) in ("product", "category") else "channel"}
                gt = gtype.get(ds.role(b)) or gtype.get(ds.role(a)) or "market"
                desc = ", ".join(f"{r} × {c}" for r, c in strong[:6])
                chart = {"type": "heatmap", "title": f"{measure or 'Row count'} by {a} and {b}", "question": f"Where are the empty {a} × {b} combinations?",
                         "rows": [str(r) for r in tab.index], "cols": [str(c) for c in tab.columns],
                         "values": [[float(tab.loc[r, c]) for c in tab.columns] for r in tab.index], "unit": ds.unit(measure) if measure else ""}
                out.append(_gap_finding(
                    gt, f"{gt.capitalize()} gap: {len(strong)} empty {a} × {b} combination(s)",
                    f"In {ds.file_name}, there is no {measure or 'activity'} for: {desc}{'…' if len(strong) > 6 else ''}, although these {a} and {b} values are active elsewhere.",
                    "Empty combinations may be whitespace (unserved demand) or deliberate choices.", "medium",
                    f"Whether there is demand for these {a} × {b} combinations.", f"Check distribution/listing data and demand for the empty cells.",
                    "Test availability in the most promising empty combinations.", tags=["gap", "whitespace", gt, a.lower(), b.lower()] + [str(x).lower() for r, c in strong[:4] for x in (r, c)],
                    evidence=[ds.evidence([a, b] + ([measure] if measure else []))], chart=chart, magnitude=min(0.8, 0.3 + len(strong) / 20)))
    # ---------------- pricing gaps for the focus entity vs market
    focus = pdata.focus.lower()
    for ds in pdata.datasets:
        price, comp, seg = ds.first("price"), ds.first("brand", "company"), ds.first("category")
        if not (price and comp and seg and focus):
            continue
        f = ds.df[ds.df[comp].str.lower() == focus]
        o = ds.df[ds.df[comp].str.lower() != focus]
        if f.empty or o.empty:
            continue
        fp, op = f.groupby(seg)[price].mean(), o.groupby(seg)[price].mean()
        diff = ((fp - op) / op * 100).dropna()
        big = diff[diff.abs() >= 15]
        if len(big):
            txt = ", ".join(f"{k}: {v:+.0f}% vs market average" for k, v in big.items())
            out.append(_gap_finding(
                "pricing", f"Pricing gap: {pdata.focus} prices differ from the market by 15%+ in {len(big)} {seg} value(s)",
                f"Average {pdata.focus} price compared with other brands — {txt}.",
                "A large premium needs a clear benefit to justify it; a large discount may leave margin on the table.", "medium",
                "Price elasticity and perceived value.", "Pricing analysis (price ladders, elasticity, conjoint).",
                "Review price positioning in these segments.", tags=["pricing", "gap", "price", seg.lower()] + [k.lower() for k in big.index],
                evidence=[ds.evidence([comp, seg, price])]))
    return out


def _gap_finding(gap_type, title, evidence_txt, why, significance, missing, investigate, action, tags=None, evidence=None, chart=None,
                 magnitude=None) -> Finding:
    sig_mag = {"high": 0.7, "medium": 0.45, "low": 0.25}
    return Finding(
        category="gap", title=title, fact=evidence_txt, interpretation=why, implication=why,
        validation=investigate, confidence="high" if gap_type in ("data", "measurement") else "medium",
        evidence=evidence or [], magnitude=magnitude if magnitude is not None else sig_mag.get(significance, 0.4), impact=0.7,
        tags=["gap", gap_type] + (tags or []), chart=chart, key=f"gap:{gap_type}:{title}",
        records={"gaps": [{"gap_type": gap_type, "gap": title, "evidence": evidence_txt, "why_it_matters": why, "significance": significance,
                           "missing_information": missing, "recommended_investigation": investigate, "potential_action": action}]})
