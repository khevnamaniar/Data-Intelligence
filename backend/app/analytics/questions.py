"""Learning objectives: intent detection and evidence-based answers to each question."""
from __future__ import annotations

import re

from .model import CONF_WEIGHT, Finding

INTENTS = {
    "trend": r"trend|over time|growth|growing|grow|declin|increas|decreas|momentum|trajectory",
    "change": r"chang|differ|shift|evolv|what happened|compared? (to|with)",
    "comparison": r"compar|versus|\bvs\.?\b|difference|benchmark|against",
    "gap": r"\bgaps?\b|missing|whitespace|white space|unserved|under-?served|lack",
    "opportunity": r"opportunit|potential|usp|unique selling|whitespace|launch|untapped|room to grow",
    "product": r"product|portfolio|sku|range|categor",
    "marketing": r"marketing|media|campaign|advert|\bads?\b|channel|spend|ctr|cpc|cpm|cpa",
    "roas": r"roas|return on ad|efficien|driven by|driver",
    "strategy": r"strateg|plan|proposed|future strategy|previous strategy|taken forward|carry forward|reconsider",
    "risk": r"risk|threat|danger|exposure|vulnerab",
    "impact": r"continue|what could happen|implicat|impact|repercussion|future|forecast|if this",
    "research": r"investigat|research|next|find out|unknown|validate|should we look",
    "customer": r"customer|consumer|audience|shopper|segment",
    "geography": r"region|country|market|geograph|city|territor",
    "pricing": r"pric",
    "competition": r"compet|rival|brand",
    "anomaly": r"anomal|unusual|outlier|strange|spike|drop",
    "recommendation": r"should (we|i)|recommend|what to do|action|taken forward|change\b",
}
CATEGORY_INTENTS = {
    "trend": {"trend", "change"}, "change": {"change", "trend"}, "comparison": {"comparison", "change"},
    "concentration": {"comparison", "risk", "product"}, "gap": {"gap", "opportunity", "research"}, "opportunity": {"opportunity", "product", "gap"},
    "marketing": {"marketing", "roas"}, "roas": {"roas", "marketing"}, "strategy": {"strategy", "change", "comparison", "recommendation"},
    "plan_vs_actual": {"strategy", "comparison", "gap"}, "impact": {"impact", "risk"}, "anomaly": {"anomaly", "trend"},
    "relationship": {"comparison"}, "reported": {"trend"}, "contradiction": {"comparison", "research"}, "theme": set(),
}
AUTO_QUESTIONS = [
    "What are the major trends?", "What changed most significantly?", "What gaps exist?", "Which products or categories have potential?",
    "What is driving marketing performance and ROAS?", "How does the strategy compare with actual performance?",
    "What are the main risks?", "What should we investigate next?",
]
DATA_NEEDED = {
    "roas": "media spend and attributed revenue by channel (and ideally by period)",
    "marketing": "media spend with clicks, conversions or revenue by channel/campaign",
    "trend": "data with a date or period column covering at least two periods",
    "opportunity": "a product/competitor list with category, and demand indicators (sales by period, market size or growth)",
    "strategy": "a strategy or plan document, plus performance data to compare it with",
    "customer": "data broken down by customer or audience segment",
    "geography": "data broken down by region or market",
    "pricing": "product prices",
    "competition": "competitor/brand data",
}


def detect_intents(question: str) -> list[str]:
    q = question.lower()
    found = [k for k, pat in INTENTS.items() if re.search(pat, q)]
    return found or ["general"]


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text.lower())
            if w not in {"what", "which", "the", "are", "is", "does", "how", "with", "and", "for", "our", "this", "that", "have", "there",
                         "major", "most", "should", "being", "particular", "data", "from"}}


PRIMARY = {"gap": {"gap"}, "opportunity": {"opportunity"}, "trend": {"trend"}, "roas": {"roas", "marketing"},
           "marketing": {"marketing", "roas"}, "strategy": {"strategy", "plan_vs_actual"}, "risk": {"concentration", "impact"},
           "comparison": {"comparison", "plan_vs_actual"}, "change": {"change", "comparison"}, "impact": {"impact"},
           "anomaly": {"anomaly"}, "product": {"opportunity"}}


def relevance(f: Finding, intents: list[str], terms: set[str]) -> float:
    cat_int = CATEGORY_INTENTS.get(f.category, set())
    tagset = set(t for t in f.tags if t)
    s = 0.0
    if set(intents) & (cat_int | tagset):
        s += 0.5
    if any(f.category in PRIMARY.get(i, set()) for i in intents):
        s += 0.2
    text_terms = _terms(" ".join([f.title] + list(tagset)))
    if terms:
        overlap = len(terms & text_terms) / len(terms)
        s += 0.4 * overlap
    return min(1.0, s)


def answer_question(question: str, findings: list[Finding], extras: dict) -> dict:
    intents = detect_intents(question)
    terms = _terms(question)
    research = extras.get("research") or {}
    if "research" in intents and research.get("to_find_out"):
        items = research["to_find_out"][:5]
        keys = [i["finding_key"] for i in items if i.get("finding_key")]
        txt = "Priority questions to investigate next: " + " ".join(
            f"({n}) {i['question']} [{i['priority']} priority; methods: {', '.join(i['methodologies'][:2])}]" for n, i in enumerate(items, 1))
        return {"question": question, "intents": intents, "status": "answered", "answer": txt, "finding_keys": keys[:6],
                "confidence": "medium", "evidence_count": len(keys)}
    scored = [(relevance(f, intents, terms), f) for f in findings]
    scored = [(r, f) for r, f in scored if r >= 0.5]
    scored.sort(key=lambda t: -(t[0] * 0.6 + t[1].score * 0.4))
    top = [f for _, f in scored[:6]]
    if not top:
        needed = [DATA_NEEDED[i] for i in intents if i in DATA_NEEDED]
        return {"question": question, "intents": intents, "status": "insufficient",
                "answer": "The available data is insufficient to determine this.",
                "data_needed": needed or ["data that directly measures what the question asks about"],
                "finding_keys": [], "confidence": "low"}
    conf = top[0].confidence
    lead = top[0]
    summary = lead.title + ". " + (lead.interpretation or "")
    if len(top) > 1:
        summary += " Related: " + "; ".join(f.title for f in top[1:3]) + "."
    return {"question": question, "intents": intents, "status": "answered", "answer": summary.strip(),
            "finding_keys": [f.key for f in top], "confidence": conf,
            "evidence_count": sum(len(f.evidence) for f in top)}


def rank_findings(findings: list[Finding], questions: list[str]) -> list[Finding]:
    """Rank by magnitude, relevance to questions, confidence, evidence strength, impact and uniqueness."""
    q_intents = [detect_intents(q) for q in questions]
    q_terms = [_terms(q) for q in questions]
    seen_subjects: dict[str, int] = {}
    for f in findings:
        rel = max((relevance(f, i, t) for i, t in zip(q_intents, q_terms)), default=0.0)
        ev = min(1.0, len(f.evidence) / 2 + (0.3 if any((e.locator or {}).get("row_count", 0) >= 20 for e in f.evidence) else 0))
        strategic = 0.15 if f.category in ("opportunity", "strategy", "roas", "plan_vs_actual", "contradiction") else 0
        f.score = (0.27 * f.magnitude + 0.2 * rel + 0.18 * CONF_WEIGHT[f.confidence] + 0.1 * ev + 0.17 * f.impact + strategic) * (0.75 if f.approximate else 1)
        if f.category in ("theme", "reported"):
            f.score *= 0.6
    findings.sort(key=lambda f: -f.score)
    # uniqueness: penalise many findings about the same subject
    for f in findings:
        subj = str(f.details.get("measure", "")) + "|" + str(f.details.get("dimension", "")) + "|" + f.category
        n = seen_subjects.get(subj, 0)
        f.score *= (1.0 if n == 0 else 0.85 if n == 1 else 0.7)
        seen_subjects[subj] = n + 1
    findings.sort(key=lambda f: -f.score)
    for i, f in enumerate(findings, start=1):
        f.rank = i
    return findings
