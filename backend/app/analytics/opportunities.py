"""Product / category opportunity analysis.

Low competition is NOT treated as an opportunity by itself: each segment is classified
on both competition and demand (size, growth) and only segments with genuine demand
indicators are called potential opportunities — always flagged for validation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import DatasetView, Finding, ProjectData, fmt_num, fmt_pct
from .stats import pct_change


def _segment_dim(ds: DatasetView) -> str | None:
    for role in ("category", "attribute", "geography", "customer_segment"):
        for c in ds.columns.values():
            if c.role == role and c.dtype == "categorical" and 3 <= c.distinct <= 40:
                return c.name
    return None


def _entity_dims(ds: DatasetView) -> tuple[str | None, str | None]:
    comp = ds.first("brand", "company")
    prod = ds.first("product")
    return comp, prod


def _growth_by_segment(ds: DatasetView, seg: str):
    """Return (size Series, growth % Series, label) from period groups, a time column, or a growth column."""
    for base, cols in (ds.period_groups or {}).items():
        role = ds.role(list(cols.values())[0])
        if role in ("revenue", "units", "market_size", "measure"):
            ps = sorted(cols)
            first, last = cols[ps[0]], cols[ps[-1]]
            g = ds.df.groupby(seg)[[first, last]].sum(min_count=1)
            growth = (g[last] - g[first]) / g[first].abs().replace(0, np.nan) * 100
            return g[last], growth, f"{base} {ps[0]}→{ps[-1]}", [first, last]
    demand = ds.first("revenue", "market_size", "units", "search_volume")
    if demand and ds.time_col:
        years = pd.to_datetime(ds.df[ds.time_col]).dt.year
        ys = sorted(years.dropna().unique())
        if len(ys) >= 2:
            g = ds.df.groupby([seg, years])[demand].sum(min_count=1).unstack()
            a, b = ys[-2], ys[-1]
            growth = (g[b] - g[a]) / g[a].abs().replace(0, np.nan) * 100
            return g[b], growth, f"{demand} {a}→{b}", [ds.time_col, demand]
    gcol = ds.first("growth")
    size = ds.df.groupby(seg)[demand].sum(min_count=1) if demand else None
    if gcol:
        return size, ds.df.groupby(seg)[gcol].mean(), gcol, [gcol] + ([demand] if demand else [])
    return size, None, "", [demand] if demand else []


def analyse_opportunities(pdata: ProjectData, mode: str) -> list[Finding]:
    out: list[Finding] = []
    focus = pdata.focus.lower()
    for ds in pdata.datasets:
        seg = _segment_dim(ds)
        comp, prod = _entity_dims(ds)
        explicit_count = ds.first("competitor_count")
        if not seg or not (comp or explicit_count or (prod and ds.file_role == "competitor_data")):
            continue  # competition needs competitor/brand information, not just our own product list
        if len(ds.df) < 8:
            continue
        df = ds.df
        segs = pd.DataFrame(index=sorted(df[seg].dropna().unique()))
        if explicit_count:
            segs["competitors"] = df.groupby(seg)[explicit_count].mean()
            comp_label = explicit_count
        else:
            key = comp or prod
            others = df if not focus or not comp else df[df[comp].str.lower() != focus]
            segs["competitors"] = others.groupby(seg)[key].nunique()
            segs["products"] = df.groupby(seg)[prod].nunique() if prod else segs["competitors"]
            comp_label = f"distinct {'brands' if comp else 'products'} (excluding {pdata.focus})" if focus and comp else f"distinct {'brands' if comp else 'products'}"
            if prod and comp:  # use number of competing products as density, brands as context
                segs["competitors"] = (others.groupby(seg)[prod].nunique())
                segs["brands"] = others.groupby(seg)[comp].nunique()
                comp_label = "competing products" + (f" (excluding {pdata.focus})" if focus else "")
        size, growth, glabel, gcols = _growth_by_segment(ds, seg)
        if size is not None:
            segs["size"] = size
        if growth is not None:
            segs["growth"] = growth
        price = ds.first("price")
        rating = ds.first("rating")
        if price:
            segs["avg_price"] = df.groupby(seg)[price].mean()
        if rating:
            segs["avg_rating"] = df.groupby(seg)[rating].mean()
        if focus and comp:
            segs["focus_present"] = df.groupby(seg)[comp].apply(lambda s: (s.str.lower() == focus).any())
        segs = segs.dropna(subset=["competitors"])
        if len(segs) < 3:
            continue
        has_size, has_growth = "size" in segs and segs["size"].notna().sum() >= 3, "growth" in segs and segs["growth"].notna().sum() >= 3
        cq = segs["competitors"].quantile([1 / 3, 2 / 3])
        segs["competition"] = segs["competitors"].map(lambda v: "low" if v <= cq.iloc[0] else "high" if v > cq.iloc[1] else "medium")
        med_growth = float(segs["growth"].median()) if has_growth else None
        med_size = float(segs["size"].median()) if has_size else None

        def classify(r) -> str:
            g = r.get("growth") if has_growth else None
            low = r["competition"] == "low"
            if g is None or pd.isna(g):
                return "low_competition_unverified_demand" if low else "insufficient_demand_data"
            growing = g > max(med_growth + 5, 5)  # clearly faster than the typical segment
            declining = g < 0
            if low and growing:
                return "potential_opportunity"
            if low and declining:
                return "low_competition_weak_demand"
            if low:
                return "low_competition_stable_demand"
            if growing:
                return "growing_but_crowded" if r["competition"] == "high" else "growing_moderate_competition"
            return "mature_or_declining" if declining else "established"

        segs["classification"] = segs.apply(classify, axis=1)
        unit = ds.unit(gcols[-1]) if gcols else ""
        demand_unit = ds.unit(ds.first("revenue", "market_size") or "") or unit
        # opportunity matrix chart
        pts = []
        for s, r in segs.iterrows():
            pts.append({"label": str(s), "x": float(r["competitors"]), "y": float(r["growth"]) if has_growth and pd.notna(r.get("growth")) else 0.0,
                        "size": float(r["size"]) if has_size and pd.notna(r.get("size")) else 1.0, "group": r["classification"]})
        chart = {"type": "matrix", "title": f"Opportunity matrix: competition vs growth by {seg}",
                 "question": f"Which {seg} values combine growing demand with limited competition?",
                 "x_label": comp_label, "y_label": f"Growth % ({glabel})" if has_growth else "Growth (not available)", "unit": "%",
                 "points": pts, "x_split": float(cq.iloc[0]), "y_split": med_growth or 0.0,
                 "note": "Bubble size = demand (size). Low competition alone does not make an opportunity."}
        seg_cols = [c for c in [seg, comp, prod, price, rating] + gcols if c]
        attr_cols = [c.name for c in ds.columns.values() if c.role == "attribute" and c.name != seg and c.dtype == "categorical" and c.distinct <= 15]

        for s, r in segs.iterrows():
            cls = r["classification"]
            if cls not in ("potential_opportunity", "low_competition_weak_demand", "low_competition_unverified_demand", "growing_moderate_competition"):
                continue
            if cls == "growing_moderate_competition" and mode == "quick":
                continue
            mask = df[seg] == s
            sub = df[mask]
            usp, usp_details = _usp(df, sub, seg, s, attr_cols, price, rating,
                                    growth_cols=gcols if has_growth and all(ds.columns.get(c) and ds.columns[c].dtype == "numeric" for c in gcols) else [])
            demand_txt = []
            if has_size and pd.notna(r.get("size")):
                demand_txt.append(f"size {fmt_num(float(r['size']), demand_unit)} (median {fmt_num(med_size, demand_unit)})")
            if has_growth and pd.notna(r.get("growth")):
                demand_txt.append(f"growth {fmt_pct(float(r['growth']))} ({glabel}; median segment {fmt_pct(med_growth)})")
            if rating and pd.notna(r.get("avg_rating")):
                demand_txt.append(f"average rating {r['avg_rating']:.1f}")
            comp_txt = f"{int(r['competitors'])} {comp_label}" + (f" from {int(r['brands'])} brands" if "brands" in r and pd.notna(r.get("brands")) else "")
            focus_txt = ""
            if "focus_present" in segs:
                focus_txt = f" {pdata.focus} {'already has' if r['focus_present'] else 'has no'} products here."
            if cls == "potential_opportunity":
                title = f"Potential opportunity: {s} (growing demand, low competition)"
                conf = "medium" if has_size and has_growth and not ds.approximate else "low"
                interp = f"'{s}' combines above-median growth with one of the lowest competitor counts."
                risks = "Fast-growing small segments can attract competitors quickly; growth may come from a small base; regulatory or margin constraints are unknown."
                magnitude, impact = min(1.0, 0.5 + (float(r.get("growth") or 0) - (med_growth or 0)) / 60), 0.9
            elif cls == "low_competition_weak_demand":
                title = f"Low competition is NOT an opportunity here: {s} demand is declining"
                conf = "medium" if has_growth else "low"
                interp = f"'{s}' has few competitors, but demand is shrinking ({fmt_pct(float(r['growth']))}). Few competitors may reflect an unattractive segment rather than whitespace."
                risks = "Entering a declining segment risks low returns even without competition."
                magnitude, impact = 0.55, 0.8
            elif cls == "low_competition_unverified_demand":
                title = f"{s}: low competition, but demand is unverified"
                conf = "low"
                interp = f"'{s}' has few competitors, but the data does not show whether demand is growing."
                risks = "Without demand evidence this may be a niche with little commercial potential."
                magnitude, impact = 0.35, 0.6
            else:
                title = f"{s}: growing demand with moderate competition"
                conf = "medium" if has_size and has_growth else "low"
                interp = f"'{s}' is growing faster than the median segment, but competition is already moderate."
                risks = "Differentiation will be needed to win share against existing players."
                magnitude, impact = 0.4, 0.7
            fact = f"'{s}' ({seg}): {comp_txt}; " + ("; ".join(demand_txt) if demand_txt else "no demand data available") + "." + focus_txt
            validation = ("Validate demand with search-trend, retail panel and consumer research; check pricing headroom, retailer appetite and regulatory "
                          "constraints; size the realistic share a new entrant could take.")
            out.append(Finding(
                category="opportunity", title=title, fact=fact, interpretation=interp,
                implication=("Worth a structured opportunity assessment." if cls == "potential_opportunity" else
                             "Do not prioritise on competitor count alone." if cls.startswith("low_competition") else "Entry requires a clear point of difference."),
                hypothesis=(f"Potential USP: {usp}" if usp else ""), validation=validation, confidence=conf,
                evidence=[ds.evidence(seg_cols, mask, filt={seg: s})], magnitude=magnitude, impact=impact,
                tags=["opportunity", "product", "potential", "usp", "competition", "whitespace", seg.lower(), str(s).lower(), cls.replace("_", " ")]
                + (["gap"] if cls == "potential_opportunity" else []),
                chart=chart, key=f"opp:{ds.id}:{s}",
                details={"dataset": ds.name, "segment_dimension": seg, "segment": str(s), "classification": cls, "usp_details": usp_details,
                         "metrics": {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) and pd.notna(v) else (bool(v) if isinstance(v, (bool, np.bool_)) else str(v)))
                                     for k, v in r.items()}},
                records={"opportunity": {"subject": str(s), "classification": cls, "rationale": interp, "potential_usp": usp,
                                         "competition_level": r["competition"], "demand_indicators": "; ".join(demand_txt) or "none in data",
                                         "risks": risks, "needs_validation": validation, "confidence": conf,
                                         "metrics": {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) and pd.notna(v) else str(v)) for k, v in r.items()}}}))
        # whitespace / portfolio gaps for the focus entity
        if "focus_present" in segs and focus:
            absent = segs[(~segs["focus_present"].astype(bool))]
            attractive = absent[absent["classification"].isin(["potential_opportunity", "growing_moderate_competition", "established"])] if has_growth else absent
            if len(absent):
                lst = ", ".join(f"{s} ({fmt_pct(float(r['growth']))})" if has_growth and pd.notna(r.get('growth')) else str(s) for s, r in absent.iterrows())
                out.append(Finding(
                    category="gap", title=f"Portfolio gap: {pdata.focus} is absent from {len(absent)} of {len(segs)} {seg} values",
                    fact=f"{pdata.focus} has no products in: {lst}. Competitors are present in all of them.",
                    interpretation=(f"Of these, {', '.join(map(str, attractive.index[:5]))} show growth at or above the median." if has_growth and len(attractive) else
                                    "The data does not show whether these segments are attractive."),
                    implication="These are the most direct candidates for portfolio extension — subject to demand validation.",
                    validation="Assess capability fit, margin potential and retailer demand for each absent segment.",
                    confidence="high" if not ds.approximate else "medium", evidence=[ds.evidence([seg, comp])], magnitude=0.6, impact=0.85,
                    tags=["gap", "portfolio", "whitespace", "opportunity", "product", seg.lower()] + [str(s).lower() for s in absent.index],
                    key=f"pgap:{ds.id}:{seg}",
                    records={"gaps": [{"gap_type": "portfolio", "gap": f"{pdata.focus} absent from {', '.join(map(str, absent.index))}",
                                       "evidence": f"{ds.file_name}: no {pdata.focus} rows for these {seg} values", "why_it_matters": "Unserved segments limit growth options.",
                                       "significance": "high" if len(attractive) else "medium", "missing_information": "Demand, margin and capability fit for each segment.",
                                       "recommended_investigation": "Opportunity sizing and consumer research for the absent segments.",
                                       "potential_action": "Prioritise entry into absent segments that have validated demand."}]}))
    return out


def _usp(df, sub, seg, s, attr_cols, price, rating, growth_cols) -> tuple[str, dict]:
    """Look for attribute or price positions that are under-served inside a segment."""
    ideas, details = [], {}
    for a in attr_cols:
        overall = df[a].value_counts(normalize=True)
        inside = sub[a].value_counts()
        n = len(sub)
        for val, share in overall.items():
            if share >= 0.15 and inside.get(val, 0) <= max(1, 0.1 * n):
                ideas.append(f"'{val}' {a.lower()} is common across the market ({share:.0%} of products) but only {int(inside.get(val, 0))} of {n} products in {s} use it")
                details.setdefault("under_represented", []).append({"attribute": a, "value": str(val), "market_share_of_products": float(share),
                                                                     "products_in_segment": int(inside.get(val, 0))})
        if len(growth_cols) == 2 and all(c in df.columns for c in growth_cols) and growth_cols[0] != growth_cols[1]:
            g = df.groupby(a)[growth_cols].sum(min_count=1)
            gr = ((g[growth_cols[1]] - g[growth_cols[0]]) / g[growth_cols[0]].replace(0, np.nan) * 100).dropna()
            if len(gr) >= 2:
                best = gr.idxmax()
                if inside.get(best, 0) <= max(1, 0.2 * n) and gr[best] > gr.median():
                    ideas.append(f"'{best}' is the fastest-growing {a.lower()} market-wide ({fmt_pct(float(gr[best]))}) and is scarce in {s}")
                    details["fastest_growing_attribute"] = {"attribute": a, "value": str(best), "growth_pct": float(gr[best])}
    if price and sub[price].notna().sum() >= 3:
        prices = np.sort(sub[price].dropna().unique())
        if len(prices) >= 3:
            gaps = np.diff(prices)
            i = int(np.argmax(gaps))
            rng = prices[-1] - prices[0]
            if rng > 0 and gaps[i] / rng >= 0.3:
                ideas.append(f"no products priced between {prices[i]:.2f} and {prices[i + 1]:.2f} (a gap covering {gaps[i] / rng:.0%} of the price range)")
                details["price_gap"] = {"from": float(prices[i]), "to": float(prices[i + 1])}
    return ("; ".join(ideas[:3]), details)
