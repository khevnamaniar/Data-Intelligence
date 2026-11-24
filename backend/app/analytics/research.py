"""Research-gap engine, future questions, recommendations and 'what we know / don't know'."""
from __future__ import annotations

from .model import Finding

METHODS = {
    "competition": ["competitor analysis", "desk research", "pricing analysis"],
    "demand": ["search trend analysis", "desk research", "consumer research"],
    "customer": ["surveys", "interviews", "social listening"],
    "pricing": ["pricing analysis", "surveys"],
    "channel": ["incrementality / geo-lift tests", "marketing mix modelling"],
    "distribution": ["distribution analysis", "retailer interviews"],
    "strategy": ["expert interviews", "desk research"],
    "data": ["data collection from source systems"],
    "trend": ["desk research", "expert interviews"],
}


def _methods_for(f: Finding) -> list[str]:
    t = set(f.tags)
    if f.category == "opportunity" or "opportunity" in t:
        return ["consumer research", "search trend analysis", "competitor analysis", "pricing analysis"]
    if "roas" in t or "marketing" in t:
        return METHODS["channel"]
    if "customer" in t:
        return METHODS["customer"]
    if "pricing" in t or "price" in t:
        return METHODS["pricing"]
    if "strategy" in t:
        return METHODS["strategy"]
    if "whitespace" in t or "distribution" in t or "geographic" in t:
        return METHODS["distribution"] + ["consumer research"]
    if f.category in ("trend", "change", "anomaly"):
        return ["desk research", "expert interviews", "competitor analysis"]
    if "data gap" in t or f.category == "contradiction":
        return METHODS["data"] + ["desk research"]
    return ["desk research"]


def future_questions(findings: list[Finding], limit: int = 12) -> list[dict]:
    qs, seen = [], set()

    def add(q, f, why, qtype="follow_up"):
        if q.lower() in seen:
            return
        seen.add(q.lower())
        qs.append({"question": q, "finding_key": f.key if f else None, "rationale": why, "question_type": qtype,
                   "methodologies": _methods_for(f) if f else ["desk research"], "priority": _priority(f)})

    for f in findings:
        subj = f.details.get("value") or f.details.get("segment") or f.details.get("measure") or ""
        if f.category == "trend" and subj:
            grow = "growing" in f.tags
            add(f"Why is {subj} {'growing' if grow else 'declining'}?", f, "Explaining the driver determines whether to act on it.")
            add(f"Is the {'growth' if grow else 'decline'} in {subj} likely to continue?", f, "Sustainability decides how much to invest or protect.")
            if f.details.get("dimension"):
                add(f"Is the change in {subj} concentrated in one channel, region or customer segment?", f, "Concentration changes the response needed.")
        elif f.category == "opportunity":
            add(f"Is there real consumer demand for a new entrant in {subj}?", f, "Low competition alone does not prove demand.", "validation")
            add(f"Is competition in {subj} increasing?", f, "New entrants could close the window.", "validation")
            add(f"What would a winning proposition in {subj} need to offer?", f, "Defines the USP to test.", "research")
        elif f.category == "roas":
            add("What is the incremental (causal) return of each channel, not just attributed ROAS?", f, "Attribution may over- or under-credit channels.", "validation")
            add("Would the high-ROAS channels keep their efficiency at higher spend?", f, "Determines whether re-allocation would work.", "validation")
        elif f.category == "strategy" and "mismatch" in f.tags:
            add(f"What evidence supports the proposed direction for {f.tags[-1]}?", f, "The strategy and the data disagree.", "validation")
        elif f.category == "gap":
            add(f"What data would close this gap: {f.title.split(':', 1)[-1].strip()}?", f, "Identified as a gap in this analysis.", "research")
        elif f.category == "concentration":
            add(f"How exposed is the business if {f.details.get('shares') and max(f.details['shares'], key=f.details['shares'].get)} weakens?", f, "Concentration is a risk.", "risk")
        elif f.category == "contradiction":
            add(f"Which source is right about {f.tags[-1]}, and why do they differ?", f, "Conflicting sources undermine conclusions.", "validation")
        if len(qs) >= limit:
            break
    return qs


def _priority(f: Finding | None) -> str:
    if not f:
        return "medium"
    s = f.magnitude * f.impact
    return "high" if s >= 0.45 else "medium" if s >= 0.2 else "low"


def research_gaps(findings: list[Finding], gaps: list[dict], unanswered: list[str], mode: str) -> dict:
    """Build WHAT WE KNOW / DON'T KNOW / SHOULD FIND OUT / HOW / PRIORITY."""
    known = [{"statement": f.fact if len(f.fact) < 260 else f.title, "confidence": f.confidence, "finding_key": f.key}
             for f in findings if f.confidence in ("high", "medium") and f.category not in ("reported", "theme", "impact")][:12]
    unknown = []
    for g in gaps:
        if g.get("missing_information"):
            unknown.append({"statement": g["missing_information"], "context": g["gap"], "type": g["gap_type"]})
    for f in findings:
        if f.confidence == "low" and f.hypothesis:
            unknown.append({"statement": f"Whether: {f.hypothesis}", "context": f.title, "type": "hypothesis"})
    for q in unanswered:
        unknown.append({"statement": f"Answer to: {q}", "context": "The available data is insufficient to determine this.", "type": "unanswered question"})
    to_find = []
    for f in findings:
        if f.hypothesis and f.category in ("trend", "opportunity", "roas", "strategy", "contradiction", "gap", "anomaly", "concentration"):
            to_find.append({"question": _as_question(f), "hypothesis": f.hypothesis, "methodologies": _methods_for(f),
                            "priority": _priority(f), "finding_key": f.key})
    for g in gaps:
        to_find.append({"question": f"What is needed to close: {g['gap']}?", "hypothesis": "", "methodologies": [g.get("recommended_investigation") or "desk research"],
                        "priority": g.get("significance", "medium"), "finding_key": None})
    order = {"high": 0, "medium": 1, "low": 2}
    to_find.sort(key=lambda x: order.get(x["priority"], 1))
    assumptions = []
    if mode == "research":
        assumptions = [
            "Uploaded datasets are complete for the periods they cover (incomplete periods are flagged where detected).",
            "Attributed marketing revenue reflects the reporting platform's attribution model, not causal impact.",
            "Category and channel labels mean the same thing across files (harmonised spellings only).",
            "Reported figures in documents are taken as stated by the source and not independently verified.",
        ]
    return {"known": known, "unknown": unknown[:15], "to_find_out": to_find[:15], "assumptions": assumptions,
            "hypotheses": [{"hypothesis": f.hypothesis, "finding": f.title, "validation": f.validation, "finding_key": f.key}
                           for f in findings if f.hypothesis][:15] if mode == "research" else []}


def _as_question(f: Finding) -> str:
    subj = f.details.get("value") or f.details.get("segment") or f.details.get("measure") or f.title
    if f.category == "opportunity":
        return f"Is {subj} a genuine, accessible market opportunity?"
    if f.category == "roas":
        return "Which channels deliver incremental return, and at what spend level do returns diminish?"
    if f.category == "strategy":
        return f"Is the strategic direction for {f.tags[-1]} supported by evidence?"
    if f.category == "trend":
        return f"What is driving the change in {subj}, and will it persist?"
    if f.category == "contradiction":
        return f"Why do sources disagree on {f.tags[-1]}?"
    return f"What explains: {f.title}?"


def recommendations(findings: list[Finding]) -> list[dict]:
    recs, seen = [], set()
    for f in findings:
        for r in f.records.get("recommendations", []):
            k = r["recommendation"]
            if k not in seen:
                seen.add(k)
                recs.append({**r, "finding_key": f.key})
    for f in findings[:15]:
        if f.validation and f.category in ("opportunity", "roas", "gap", "concentration", "trend", "plan_vs_actual", "marketing"):
            k = f.validation
            if k in seen:
                continue
            seen.add(k)
            rec_type = "research" if f.category in ("opportunity", "gap") else "action"
            recs.append({"recommendation": f.validation, "rec_type": rec_type, "priority": _priority(f), "rationale": f.title, "finding_key": f.key})
    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: order.get(r["priority"], 1))
    return recs[:20]
