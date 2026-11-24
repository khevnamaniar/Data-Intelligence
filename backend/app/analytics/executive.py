"""Executive (CXO) dashboard: one plain-language answer card per question, plus an at-a-glance summary.

Built from an analysis run's stored answers and findings, so it also works for runs created before this
feature existed. It only re-presents computed findings: no new numbers are invented. Each card keeps a
link to the supporting findings and their evidence.
"""
from __future__ import annotations

import re

STATUS_LABEL = {
    "positive": "Positive",
    "watch": "Watch",
    "action": "Action needed",
    "insufficient": "Not enough data",
    "info": "For information",
}
CONFIDENCE_PLAIN = {"high": "Strong evidence", "medium": "Moderate evidence", "low": "Early signal — needs checking"}

# Technical fragments that a CXO does not need on a dashboard (they stay in the full analysis)
_STRIP = [
    r"\s*\(rank correlation [^)]*\)", r"\s*Linear trend fit R² = [0-9.]+\.?", r"\s*Concentration index \(HHI\) = [0-9.]+\.?",
    r"\s*\(p ?= ?[0-9.e\-]+\)", r"\s*Spearman ρ = [^,.]+[,.]?", r"\s*Pearson r = [0-9.\-]+\.?",
    r"\s*The \d+-period series runs from \S+ to \S+\.", r"\s*\(complete years only\)", r"\s*, complete years only",
    r"\s*\(TF-IDF[^)]*\)",
]
_REPLACE = [
    (r"\bCompound annual growth rate\b", "Average yearly growth"), (r"\bCAGR\b", "average yearly growth"),
    (r"\bYoY\b", "year-on-year"), (r"\bROAS\b", "ROAS (revenue per £1 of ads)"),
]


def plain(text: str, explain_roas: bool = False) -> str:
    """Remove statistical jargon and tidy a sentence for executive readers."""
    if not text:
        return ""
    t = text
    # "… — marketing_dataset.csv" → "… (marketing dataset)"
    t = re.sub(r" — ([\w\-. ]+?)\.(csv|xlsx|pdf|docx|pptx|txt|md)$", lambda m: f" ({m.group(1).replace('_', ' ')})", t)
    # file names inside sentences: "market_report_2025.pdf" → "market report 2025"
    t = re.sub(r"\b([\w\-]+)\.(csv|xlsx|pdf|docx|pptx|txt|md)\b", lambda m: m.group(1).replace("_", " "), t)
    t = re.sub(r"\b([a-z]+)_([a-z]+)\b", r"\1 \2", t)  # internal labels such as market_size
    # 'collagen' → 'Collagen', 'tv' → 'TV'
    t = re.sub(r"'([a-z][a-z0-9 \-]*)'", lambda m: "'" + (m.group(1).upper() if len(m.group(1)) <= 3 else m.group(1).title()) + "'", t)
    for rx in _STRIP:
        t = re.sub(rx, "", t)
    for rx, rep in _REPLACE:
        if rx == r"\bROAS\b" and not explain_roas:
            continue
        t = re.sub(rx, rep, t, count=1 if rx == r"\bROAS\b" else 0)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def _first_sentence(text: str, limit: int = 240) -> str:
    text = plain(text)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _fmt_pct(v) -> str:
    return f"{v:+.0f}%" if abs(v) >= 10 else f"{v:+.1f}%"


def _kpi(f: dict, related: list[dict]) -> dict | None:
    """Pick the single most telling number for a finding, with a short label."""
    d = f.get("details_json") or {}
    cat = f["category"]
    st = d.get("stats") or {}
    subject = d.get("value") or d.get("segment") or d.get("measure") or ""
    if cat in ("trend", "impact") and st.get("pct_change") is not None:
        span = f"{st.get('start_period', '')}–{st.get('end_period', '')}".strip("–")
        return {"value": _fmt_pct(st["pct_change"]), "label": f"{subject} change {span}".strip()}
    if cat == "roas":
        if d.get("roas_b") is not None:
            return {"value": f"{d['roas_b']:.1f}x", "label": f"ROAS in {d.get('b', 'latest period')} (was {d['roas_a']:.1f}x)"}
        table = d.get("table") or {}
        if table:
            top = max(table.items(), key=lambda kv: kv[1].get("revenue", 0))
            share = top[1].get("rev_share")
            if share is not None:
                return {"value": f"{share:.0f}%", "label": f"of attributed revenue comes from {top[0]}"}
    if cat == "opportunity":
        m = (d.get("metrics") or {})
        if isinstance(m.get("growth"), (int, float)):
            return {"value": _fmt_pct(m["growth"]), "label": f"demand growth in {d.get('segment', '')}"}
    if cat == "concentration" and d.get("shares"):
        k, v = max(d["shares"].items(), key=lambda kv: kv[1])
        return {"value": f"{v:.0f}%", "label": f"of the total comes from {k}"}
    if cat == "strategy":
        mism = [r for r in related if r["category"] == "strategy" and "mismatch" in (r.get("tags_json") or [])]
        if mism:
            return {"value": str(len(mism)), "label": "strategy item(s) conflict with the data"}
    if cat == "gap":
        gaps = [r for r in related if r["category"] == "gap"]
        return {"value": str(max(1, len(gaps))), "label": "gap(s) identified"}
    m = re.search(r"([+\-−]?\d+(?:\.\d+)?)\s?(%|x\b)", f["title"])
    if m:
        return {"value": m.group(1) + m.group(2), "label": plain(re.sub(r"\s*\(.*?\)", "", f["title"]))[:70]}
    return None


def _status(f: dict) -> str:
    tags = set(f.get("tags_json") or [])
    cat = f["category"]
    d = f.get("details_json") or {}
    cls = d.get("classification", "")
    if cat == "opportunity":
        return "positive" if cls in ("potential_opportunity", "growing_moderate_competition") else "watch"
    if cat in ("gap", "contradiction") or "mismatch" in tags or "inefficiency" in tags or cat == "concentration":
        return "action"
    if cat == "plan_vs_actual":
        return "action" if "behind" in f["title"] else "positive"
    if cat in ("trend", "impact", "change"):
        st = d.get("stats") or {}
        pc = st.get("pct_change")
        costly = (d.get("measure") or "").lower()
        is_cost = any(w in costly for w in ("spend", "cost", "cpa", "cpc", "cpm", "price"))
        if pc is None:
            return "info"
        if abs(pc) < 3:
            return "info"
        good = (pc > 0) != is_cost
        return "positive" if good else "watch"
    if cat == "roas":
        d2 = f.get("details_json") or {}
        if d2.get("roas_b") is not None and d2.get("roas_a") is not None:
            return "positive" if d2["roas_b"] >= d2["roas_a"] else "watch"
        return "info"
    if cat == "strategy":
        return "positive" if "supported" in tags else "watch"
    return "info"


def _next_step(f: dict, recs: list[dict]) -> str:
    for r in recs:
        if r.get("finding_id") == f["id"]:
            return plain(r["recommendation"])
    return _first_sentence(f.get("validation") or "")


def build_executive_dashboard(run: dict) -> dict:
    findings = run.get("findings") or []
    fmap = {f["id"]: f for f in findings}
    result = run.get("result") or {}
    recs = result.get("recommendations") or []
    cards = []
    questions = [q for q in run.get("questions", []) if q["origin"] == "user"]
    if not questions:  # "I don't know" runs: use the system's own questions
        questions = [q for q in run.get("questions", []) if q["origin"] == "auto"][:6]
    for q in questions:
        a = q.get("answer_json") or {}
        ids = [i for i in a.get("finding_ids", []) if i in fmap]
        if a.get("status") == "insufficient" or not ids:
            cards.append({
                "question": q["question"], "status": "insufficient", "status_label": STATUS_LABEL["insufficient"],
                "headline": "We can't answer this reliably with the data provided.",
                "summary": "The uploaded sources don't contain the information needed.",
                "so_what": "", "next_step": "Provide: " + "; ".join(a.get("data_needed") or ["data that measures this directly"]) + ".",
                "kpi": None, "confidence": "low", "confidence_label": "No evidence yet", "chart": None,
                "supporting": [], "finding_ids": [], "origin": q["origin"]})
            continue
        lead = fmap[ids[0]]
        related = [fmap[i] for i in ids]
        intents = set(a.get("intents") or [])
        mismatches = [f for f in findings if f["category"] == "strategy" and "mismatch" in (f.get("tags_json") or [])]
        supported = [f for f in findings if f["category"] == "strategy" and "supported" in (f.get("tags_json") or [])]
        if "strategy" in intents and (mismatches or supported):
            names = [(f.get("tags_json") or [""])[-1] for f in mismatches]
            if mismatches:
                headline = (f"{len(mismatches)} part(s) of the new strategy conflict with past performance: "
                            + ", ".join(n.upper() if len(n) <= 3 else n.title() for n in names[:4]) + ".")
                status = "action"
            else:
                headline = "The new strategy is broadly supported by past performance."
                status = "positive"
            summary = " ".join(_first_sentence(f["fact"].split(". In the data, ")[-1], 160) for f in mismatches[:2]) or _first_sentence(supported[0]["fact"])
            so_what = (f"{len(supported)} other strategy item(s) are backed by the data and can be carried forward."
                       if supported else "")
            next_step = "Ask the strategy owner for the evidence behind the conflicting items; pilot them before full investment."
            kpi = {"value": str(len(mismatches)), "label": "strategy item(s) conflict with the data"}
            lead = mismatches[0] if mismatches else supported[0]
            ids = [f["id"] for f in (mismatches + supported)[:4]] + ids
            related = [fmap[i] for i in ids if i in fmap]
        elif "research" in intents and (result.get("research") or {}).get("to_find_out"):
            items = result["research"]["to_find_out"][:3]
            headline = "Top priority to investigate: " + plain(items[0]["question"])
            summary = " ".join(f"{n}. {plain(i['question'])}" for n, i in enumerate(items, 1))
            so_what = "These open questions carry the most risk or upside if left unanswered."
            next_step = "Suggested methods: " + ", ".join(dict.fromkeys(m for i in items for m in i["methodologies"][:2])) + "."
            kpi = {"value": str(len(result["research"]["to_find_out"])), "label": "open research questions"}
            status = "info"
        else:
            headline = plain(lead["title"], explain_roas="roas" in intents)
            summary = _first_sentence(lead.get("interpretation") or lead.get("fact") or "")
            so_what = _first_sentence(lead.get("implication") or "")
            next_step = _next_step(lead, recs)
            kpi = _kpi(lead, related)
            status = _status(lead)
        cards.append({
            "question": q["question"], "status": status, "status_label": STATUS_LABEL[status], "headline": headline,
            "summary": summary, "so_what": so_what, "next_step": next_step, "kpi": kpi,
            "confidence": lead["confidence"], "confidence_label": CONFIDENCE_PLAIN.get(lead["confidence"], lead["confidence"]),
            "chart": lead.get("chart_json"), "approximate": bool(lead.get("is_approximate")),
            "supporting": [plain(f["title"]) for f in related[1:3] if f["id"] != lead["id"]],
            "finding_ids": list(dict.fromkeys(ids))[:4], "origin": q["origin"]})

    # ----- at a glance
    opp = [f for f in findings if f["category"] == "opportunity" and (f.get("details_json") or {}).get("classification") == "potential_opportunity"]
    serious_gaps = [g for g in run.get("gaps") or [] if g.get("significance") == "high"]
    risks = serious_gaps + [f for f in findings if ("mismatch" in (f.get("tags_json") or []) or "inefficiency" in (f.get("tags_json") or [])
                                                    or (f["category"] == "plan_vs_actual" and "behind" in f["title"]))]
    strong = sum(1 for f in findings if f["confidence"] in ("high", "medium"))
    sources = [s for s in (result.get("sources") or []) if s.get("status") in ("processed", "partial")]
    tiles = [
        {"label": "Sources analysed", "value": str(len(sources)), "note": "files included in this analysis"},
        {"label": "Opportunities to validate", "value": str(len(opp)),
         "note": ", ".join((f.get("details_json") or {}).get("segment", "") for f in opp[:3]) or "none found"},
        {"label": "Issues needing attention", "value": str(len(risks)), "note": "serious gaps, strategy conflicts, shortfalls, inefficient spend"},
        {"label": "Evidence strength", "value": f"{round(100 * strong / len(findings)) if findings else 0}%",
         "note": "of findings have strong or moderate evidence"},
    ]
    decisions = [plain(r["recommendation"]) for r in recs if r.get("priority") == "high"][:3] or [plain(r["recommendation"]) for r in recs[:3]]
    top_risk = next((plain(f["title"]) for f in findings if _status(f) == "action"), "")
    top_win = next((plain(f["title"]) for f in findings if _status(f) == "positive"), "")
    return {"cards": cards, "tiles": tiles, "decisions": decisions, "top_risk": top_risk, "top_win": top_win,
            "note": "Plain-language summary of the computed findings. Every number comes from your data; open the full analysis for evidence."}
