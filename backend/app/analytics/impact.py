"""Impact / repercussion analysis: 'if this trend continues, what could happen?'

Projections are simple linear extrapolations and are always labelled as speculative.
"""
from __future__ import annotations

from .model import Finding, fmt_num, fmt_pct
from .stats import linear_forecast

ROLE_IMPLICATIONS = {
    ("revenue", "up"): "Revenue momentum would strengthen its weight in the business; capacity, supply and support may need to scale.",
    ("revenue", "down"): "Continued decline would erode total revenue and may make the line unprofitable at current cost levels.",
    ("market_size", "up"): "A growing market raises the value of share held now and may attract new competitors.",
    ("market_size", "down"): "A shrinking market means growth must come from taking share, increasing price pressure.",
    ("spend", "up"): "Rising spend increases the importance of measuring incremental return.",
    ("spend", "down"): "Lower spend may reduce reach and future demand if the channel is effective.",
    ("units", "up"): "Volume growth may require supply-chain capacity.",
    ("units", "down"): "Falling volumes can raise unit costs and weaken retailer support.",
    ("roas", "down"): "Falling efficiency would mean each unit of spend buys less revenue; budgets may need re-allocation.",
    ("roas", "up"): "Improving efficiency creates headroom to scale investment, if returns hold at higher spend.",
    ("market_share", "down"): "Losing share can reduce bargaining power with retailers and partners.",
    ("market_share", "up"): "Gaining share strengthens market position; watch for competitive response.",
}


def analyse_impact(trend_findings: list[Finding], mode: str) -> list[Finding]:
    out: list[Finding] = []
    cands = [f for f in trend_findings if f.category == "trend" and (f.details.get("stats") or {}).get("periods", 0) >= 4
             and abs((f.details.get("stats") or {}).get("pct_change") or 0) >= 8]
    cands.sort(key=lambda f: -f.magnitude * f.impact)
    for f in cands[: (3 if mode == "quick" else 8)]:
        tr = f.records.get("trend", {}).get("stats", {})
        series = tr.get("series") or []
        vals = [r["value"] for r in series if r.get("value") is not None]
        if len(vals) < 4:
            continue
        fc = linear_forecast(vals, 12 if len(vals) >= 12 else 3)
        if not fc:
            continue
        role = (f.tags[2] if len(f.tags) > 2 else "")
        direction = "up" if (tr.get("pct_change") or 0) > 0 else "down"
        unit = f.details.get("unit", "")
        subject = f.details.get("value") or f.details.get("measure")
        r2 = fc["r_squared"]
        short_n = 1
        medium_n = len(fc["values"])
        conf_short = "medium" if r2 >= 0.7 and len(vals) >= 6 else "low"
        txt_short = (f"next period ≈ {fmt_num(fc['values'][0], unit)} (range {fmt_num(fc['low'][0], unit)}–{fmt_num(fc['high'][0], unit)})")
        txt_med = (f"in {medium_n} periods ≈ {fmt_num(fc['values'][-1], unit)} (range {fmt_num(fc['low'][-1], unit)}–{fmt_num(fc['high'][-1], unit)})")
        implication = ROLE_IMPLICATIONS.get((role, direction), f"If the trend in {subject} continues, its weight in overall results will {'grow' if direction == 'up' else 'shrink'}.")
        chart = {"type": "line", "title": f"{subject}: history and straight-line extrapolation", "question": "What happens if the trend continues?",
                 "categories": [r["period"] for r in series] + [f"+{i + 1}" for i in range(medium_n)], "unit": unit,
                 "series": [{"name": "Actual", "values": vals + [None] * medium_n},
                            {"name": "Extrapolation (speculative)", "values": [None] * (len(vals) - 1) + [vals[-1]] + fc["values"], "style": "dashed"}],
                 "note": "Straight-line extrapolation for illustration; not a forecast model."}
        out.append(Finding(
            category="impact", title=f"If the trend continues: {subject} {'keeps rising' if direction == 'up' else 'keeps falling'}",
            fact=f"Observed: {f.fact}",
            interpretation=(f"Short term (speculative): {txt_short}. Medium term (speculative): {txt_med}. "
                            f"Long term: direction only — {implication}"),
            implication=implication,
            hypothesis="Assumes the past trend continues unchanged; competitive response, saturation or market shifts would change the outcome.",
            validation="Track the next periods against this line; build a driver-based forecast before using it for planning.",
            confidence=conf_short, evidence=f.evidence, magnitude=f.magnitude * 0.9, impact=f.impact,
            tags=["impact", "implication", "forecast", "future", "risk" if direction == "down" else "opportunity"] + f.tags[2:],
            chart=chart, key=f"impact:{f.key}",
            details={"base_finding": f.key, "forecast": fc, "short_term": txt_short, "medium_term": txt_med, "long_term": implication,
                     "speculative": True},
            records={"risks": [{"risk": f"Continued decline in {subject}", "horizon": "medium", "likelihood": "medium" if r2 >= 0.6 else "low",
                                "impact": implication, "mitigation": "Diagnose the drivers and set early-warning thresholds"}] if direction == "down" else []}))
    return out
