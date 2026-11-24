"""Anomalies, outliers and relationships between measures."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import Finding, ProjectData, fmt_num, fmt_pct
from .stats import correlation, robust_z

DERIVED_PAIRS = {frozenset(p) for p in [("spend", "impressions"), ("spend", "clicks"), ("impressions", "clicks"), ("revenue", "units"),
                                         ("clicks", "conversions"), ("revenue", "conversions"), ("revenue", "roas"), ("spend", "cpm"),
                                         ("plan", "actual")]}


def analyse_anomalies(pdata: ProjectData, mode: str) -> list[Finding]:
    out: list[Finding] = []
    for ds in pdata.datasets:
        t = ds.time_col
        if t:
            periods = ds.periods()
            for m in ds.measures(limit=4):
                if not ds.additive(m):
                    continue
                s = ds.df.groupby(periods)[m].sum(min_count=1).sort_index().dropna()
                if len(s) < 8:
                    continue
                ch = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
                z = robust_z(ch)
                for p in z[z.abs() > 3.5].index[:2]:
                    prev = s.index[list(s.index).index(p) - 1]
                    out.append(Finding(
                        category="anomaly", title=f"Unusual {'jump' if ch[p] > 0 else 'drop'} in {m} in {p} ({fmt_pct(ch[p] * 100)})",
                        fact=f"{m} moved from {fmt_num(float(s[prev]), ds.unit(m))} in {prev} to {fmt_num(float(s[p]), ds.unit(m))} in {p} ({fmt_pct(ch[p] * 100)}), "
                             f"far outside the typical period-to-period change (median {fmt_pct(float(ch.median() * 100))}).",
                        interpretation="A one-off event, a data issue or a genuine shift.",
                        hypothesis="Promotions, stock issues, reporting changes or seasonality could explain it.",
                        validation="Check events in that period and whether the data for it is complete.",
                        confidence="medium", evidence=[ds.evidence([t, m], periods == p)], magnitude=min(1.0, abs(ch[p])), impact=0.6,
                        tags=["anomaly", "outlier", m.lower(), str(p)], key=f"anom:{ds.id}:{m}:{p}"))
        # segment-level outliers on rate metrics (e.g. a product priced far above peers)
        for m in [c for c in ds.measures(limit=8) if ds.role(c) in ("price", "rating", "margin", "growth", "market_share")]:
            d = next((x for x in ds.dims(max_levels=60, limit=3) if ds.role(x) in ("product", "brand", "category", "company")), None)
            if not d:
                continue
            g = ds.df.groupby(d)[m].mean().dropna()
            if len(g) < 8:
                continue
            z = robust_z(g)
            outl = z[z.abs() > 4]
            if len(outl) and len(outl) <= 3:
                items = ", ".join(f"{i} ({fmt_num(float(g[i]), ds.unit(m))})" for i in outl.index)
                out.append(Finding(
                    category="anomaly", title=f"Outlier {m}: {items}",
                    fact=f"Median {m} across {len(g)} {d} values is {fmt_num(float(g.median()), ds.unit(m))}; {items} are far outside the typical range.",
                    interpretation="These values are unusual compared with peers.",
                    validation="Confirm these values are correct and comparable (same pack size, currency, definition).",
                    confidence="medium", evidence=[ds.evidence([d, m], ds.df[d].isin(outl.index))], magnitude=0.35, impact=0.4,
                    tags=["anomaly", "outlier", m.lower(), d.lower()] + [str(i).lower() for i in outl.index], key=f"outl:{ds.id}:{m}:{d}"))
        if mode == "quick":
            continue
        # relationships between measures (row level)
        ms = [m for m in ds.measures(limit=8) if ds.df[m].notna().sum() >= 10]
        done = 0
        for i, a in enumerate(ms):
            for b in ms[i + 1:]:
                if frozenset((ds.role(a), ds.role(b))) in DERIVED_PAIRS or ds.role(a) == ds.role(b):
                    continue
                cr = correlation(ds.df[a], ds.df[b])
                if not cr or abs(cr["spearman"]) < 0.5 or cr["spearman_p"] >= 0.01:
                    continue
                done += 1
                if done > 3:
                    break
                sample = ds.df[[a, b]].dropna()
                sample = sample.sample(min(len(sample), 300), random_state=1)
                out.append(Finding(
                    category="relationship", title=f"{a} and {b} move together (rank correlation {cr['spearman']:+.2f})",
                    fact=f"Across {cr['n']} rows in {ds.name}, {a} and {b} have Spearman ρ = {cr['spearman']:.2f} (p = {cr['spearman_p']:.3g}) and Pearson r = {cr['pearson']:.2f}.",
                    interpretation=f"Higher {a} tends to go with {'higher' if cr['spearman'] > 0 else 'lower'} {b}.",
                    implication="This is an association only; it does not show that one causes the other.",
                    hypothesis=f"A common factor (e.g. size of the segment, seasonality) may drive both {a} and {b}.",
                    validation="Control for segment size and time, or run an experiment, before treating this as a lever.",
                    confidence="medium" if abs(cr["spearman"]) >= 0.7 else "low", evidence=[ds.evidence([a, b])],
                    magnitude=abs(cr["spearman"]) * 0.6, impact=0.5, tags=["relationship", "correlation", a.lower(), b.lower()],
                    chart={"type": "scatter", "title": f"{a} vs {b}", "question": f"Is {a} related to {b}?", "x_label": a, "y_label": b,
                           "points": [{"x": float(r[a]), "y": float(r[b])} for _, r in sample.iterrows()]},
                    key=f"rel:{ds.id}:{a}:{b}"))
    return out
