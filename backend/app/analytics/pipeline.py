"""Analysis pipeline: runs every engine for the chosen mode and assembles ranked, sectioned results."""
from __future__ import annotations

import logging
import traceback
from collections import Counter
from typing import Callable

from .anomalies import analyse_anomalies
from .capabilities import detect_capabilities
from .comparisons import analyse_comparisons
from .documents import contradictions, document_themes, reported_facts, strategy_comparison
from .gaps import analyse_gaps
from .impact import analyse_impact
from .marketing import analyse_marketing
from .model import Finding, ProjectData
from .opportunities import analyse_opportunities
from .questions import AUTO_QUESTIONS, answer_question, detect_intents, rank_findings
from .research import future_questions, recommendations, research_gaps
from .trends import analyse_trends

log = logging.getLogger("dip.pipeline")

STAGES = ["Loading data", "Trend analysis", "Comparisons", "Marketing & ROAS", "Opportunities", "Documents & strategy",
          "Anomalies & relationships", "Gap analysis", "Impact analysis", "Research gaps", "Ranking insights", "Answering questions"]


def run_pipeline(pdata: ProjectData, mode: str, questions: list[str], auto_discover: bool,
                 progress: Callable[[int, str], None] | None = None) -> dict:
    progress = progress or (lambda p, s: None)
    errors: list[dict] = []
    findings: list[Finding] = []
    all_questions = list(questions)
    auto_qs = []
    if auto_discover or not questions:
        caps = detect_capabilities(pdata)["capabilities"]
        for c in caps:
            for q in c["example_questions"][:1]:
                if q not in all_questions:
                    auto_qs.append(q)
        for q in AUTO_QUESTIONS:
            if q not in all_questions and q not in auto_qs and len(auto_qs) < 8:
                auto_qs.append(q)
    intents = set()
    for q in all_questions + auto_qs:
        intents.update(detect_intents(q))
    if mode != "quick":
        intents.update({"trend"})

    def stage(i: int, fn, *args):
        progress(int(5 + 85 * i / len(STAGES)), STAGES[i])
        try:
            return fn(*args) or []
        except Exception as exc:
            log.error("Engine %s failed: %s", STAGES[i], traceback.format_exc())
            errors.append({"what": f"{STAGES[i]} could not be completed.", "why": f"{exc.__class__.__name__}: {str(exc)[:200]}",
                           "can_still": "All other analyses were completed and are shown.",
                           "next": "Check the data quality report for the related files, then run the analysis again."})
            return []

    progress(3, STAGES[0])
    trend_f = stage(1, analyse_trends, pdata, mode)
    comp_f = stage(2, analyse_comparisons, pdata, mode)
    mkt_f = stage(3, analyse_marketing, pdata, mode)
    opp_f = stage(4, analyse_opportunities, pdata, mode)
    doc_f, rep_f = [], []
    if mode != "quick" or pdata.documents:
        rep_f = stage(5, reported_facts, pdata, mode)
        doc_f = stage(5, strategy_comparison, pdata, mode) + (stage(5, document_themes, pdata) if mode != "quick" else [])
        doc_f += stage(5, contradictions, pdata, rep_f, comp_f)
    anom_f = stage(6, analyse_anomalies, pdata, mode)
    gap_f = stage(7, analyse_gaps, pdata, mode, intents)
    impact_f = stage(8, analyse_impact, trend_f, mode) if mode != "quick" else []
    findings = trend_f + comp_f + mkt_f + opp_f + rep_f + doc_f + anom_f + gap_f + impact_f
    # quality-driven limitation findings
    findings += _quality_findings(pdata)
    # de-duplicate
    uniq: dict[str, Finding] = {}
    for f in findings:
        if f.key not in uniq or f.magnitude > uniq[f.key].magnitude:
            uniq[f.key] = f
    findings = list(uniq.values())
    progress(int(5 + 85 * 10 / len(STAGES)), STAGES[10])
    findings = rank_findings(findings, all_questions + auto_qs)
    if mode == "quick":
        findings = [f for f in findings if f.category not in ("relationship", "theme", "impact")][:40]

    progress(int(5 + 85 * 11 / len(STAGES)), STAGES[11])
    gaps_all = [g for f in findings for g in f.records.get("gaps", [])]
    research = research_gaps(findings, gaps_all, [], mode)
    if mode == "quick":
        research = {"known": [], "unknown": research["unknown"][:5], "to_find_out": research["to_find_out"][:5], "assumptions": [], "hypotheses": []}
    answers = [answer_question(q, findings, {"research": research}) | {"origin": "user"} for q in questions]
    auto_answers = [answer_question(q, findings, {"research": research}) | {"origin": "auto"} for q in auto_qs] if auto_discover or not questions else []
    for a in answers + auto_answers:
        if a["status"] == "insufficient":
            research["unknown"].append({"statement": f"Answer to: {a['question']}", "type": "unanswered question",
                                        "context": "The available data is insufficient to determine this. Needed: " + "; ".join(a.get("data_needed", []))})
    _disambiguate_titles(findings, pdata)
    fut = future_questions(findings, limit=8 if mode == "quick" else 15)
    recs = recommendations(findings)
    sections = build_sections(findings, mode)
    summary = executive_summary(findings, pdata, mode)
    limitations = data_limitations(pdata, findings, errors)
    sources = source_references(pdata, findings)
    progress(97, "Saving results")
    return {
        "findings": findings, "answers": answers, "auto_answers": auto_answers, "auto_questions": auto_qs,
        "sections": sections, "executive_summary": summary, "research": research, "future_questions": fut,
        "recommendations": recs, "data_limitations": limitations, "sources": sources, "errors": errors,
        "capabilities": detect_capabilities(pdata), "stats": {"findings": len(findings), "by_category": dict(Counter(f.category for f in findings)),
                                                             "datasets": len(pdata.datasets), "documents": len(pdata.documents)},
    }


SECTION_RULES = [
    ("major_trends", lambda f: f.category == "trend"),
    ("significant_changes", lambda f: f.category in ("change", "anomaly") or (f.category == "comparison" and "time comparison" in f.tags)),
    ("comparisons", lambda f: f.category in ("comparison", "concentration", "plan_vs_actual", "relationship", "contradiction") and "time comparison" not in f.tags),
    ("opportunities", lambda f: f.category == "opportunity" or (f.category == "gap" and "portfolio" in f.tags)),
    ("gaps", lambda f: f.category == "gap"),
    ("risks", lambda f: f.category in ("concentration",) or "risk" in f.tags or bool(f.records.get("risks"))),
    ("marketing_insights", lambda f: f.category in ("marketing", "roas")),
    ("product_insights", lambda f: f.category == "opportunity" or ("product" in f.tags or "category" in f.tags) and f.category in ("trend", "change", "comparison", "concentration")),
    ("strategic_implications", lambda f: f.category in ("strategy", "impact", "plan_vs_actual")),
    ("document_insights", lambda f: f.category in ("reported", "theme")),
]


def build_sections(findings: list[Finding], mode: str) -> dict:
    sections: dict[str, list[str]] = {"key_findings": [f.key for f in findings if f.category not in ("theme",)][:10]}
    for name, rule in SECTION_RULES:
        keys = [f.key for f in findings if rule(f)]
        if keys:
            sections[name] = keys
    return sections


def executive_summary(findings: list[Finding], pdata: ProjectData, mode: str) -> list[str]:
    top = [f for f in findings if f.category not in ("theme", "reported")][:6]
    lines = []
    if not findings:
        return ["No significant findings could be produced from the uploaded data. See Data Limitations for what would be needed."]
    n_ds, n_doc = len(pdata.datasets), len(pdata.documents)
    lines.append(f"{mode.capitalize()} analysis of {n_ds} dataset(s) and {n_doc} document(s) produced {len(findings)} findings; the most important are summarised below.")
    for f in top:
        lines.append(f"{f.title} [{f.confidence} confidence].")
    opp = [f for f in findings if f.category == "opportunity" and f.details.get("classification") == "potential_opportunity"]
    trap = [f for f in findings if f.category == "opportunity" and f.details.get("classification") == "low_competition_weak_demand"]
    if opp:
        lines.append("Potential opportunities to validate: " + ", ".join(f.details.get("segment", "") for f in opp[:3]) + ".")
    if trap:
        lines.append("Low competition but weak demand (not an established opportunity): " + ", ".join(f.details.get("segment", "") for f in trap[:3]) + ".")
    mism = [f for f in findings if f.category == "strategy" and "mismatch" in f.tags]
    if mism:
        lines.append(f"{len(mism)} element(s) of the latest strategy conflict with performance data and should be reconsidered.")
    return lines


def data_limitations(pdata: ProjectData, findings: list[Finding], errors: list[dict]) -> list[dict]:
    lims = []
    sev = Counter(i["severity"] for i in pdata.quality_issues)
    if pdata.quality_issues:
        lims.append({"limitation": f"{len(pdata.quality_issues)} data-quality issue(s) detected ({sev.get('high', 0)} high, {sev.get('medium', 0)} medium, {sev.get('low', 0)} low severity).",
                     "effect": "Results using affected fields should be read with caution; see the Data Quality Report."})
    approx = [f for f in findings if f.approximate]
    if approx:
        lims.append({"limitation": f"{len(approx)} finding(s) use values visually interpreted from images or OCR.",
                     "effect": "These values are approximate and confidence is capped at medium."})
    failed = [f for f in pdata.files if f["status"] == "failed"]
    if failed:
        lims.append({"limitation": f"{len(failed)} file(s) could not be processed: {', '.join(f['original_name'] for f in failed[:5])}.",
                     "effect": "Their content is not included in this analysis."})
    if any(f.category in ("roas", "marketing") for f in findings):
        lims.append({"limitation": "Marketing revenue is attributed revenue as reported in the data.",
                     "effect": "ROAS comparisons show association with spend, not proven causal impact."})
    for e in errors:
        lims.append({"limitation": e["what"], "effect": e["why"]})
    if not pdata.datasets:
        lims.append({"limitation": "No structured datasets were found.", "effect": "Quantitative trend, comparison and ROAS analyses are not possible."})
    return lims


def source_references(pdata: ProjectData, findings: list[Finding]) -> list[dict]:
    counts = Counter(e.file_id for f in findings for e in f.evidence if e.file_id)
    refs = []
    for f in pdata.files:
        refs.append({"file_id": f["id"], "name": f["original_name"], "type": f["file_type"], "role": f["source_role"],
                     "status": f["status"], "findings_citing": counts.get(f["id"], 0)})
    return refs


def _disambiguate_titles(findings: list[Finding], pdata: ProjectData) -> None:
    """When the same measure name exists in several files, add the file name to titles that use it."""
    names: dict[str, set] = {}
    for ds in pdata.datasets:
        for m in ds.measures(limit=20):
            names.setdefault(m, set()).add(ds.file_name)
    dup = {m for m, files in names.items() if len(files) > 1}
    for f in findings:
        m = f.details.get("measure")
        if m in dup and f.evidence and f.category in ("trend", "change", "comparison", "concentration", "impact", "anomaly"):
            src = f.evidence[0].source_label.split(" › ")[0]
            if src not in f.title:
                f.title = f"{f.title} — {src}"


def _quality_findings(pdata: ProjectData) -> list[Finding]:
    from .model import Evidence

    out = []
    high = [i for i in pdata.quality_issues if i["severity"] == "high"]
    if high:
        fmap = {f["id"]: f["original_name"] for f in pdata.files}
        txt = "; ".join(f"{fmap.get(i['file_id'], '?')}: {i['description']}" for i in high[:4])
        out.append(Finding(
            category="gap", title=f"{len(high)} high-severity data-quality issue(s) may affect results",
            fact=txt + ("…" if len(high) > 4 else ""),
            interpretation="Some figures may be distorted by these issues.",
            validation="Review the Data Quality Report and correct the source data where possible.",
            confidence="high", evidence=[Evidence(source_label=f"{fmap.get(i['file_id'], '?')} › {i['field']}", file_id=i["file_id"],
                                                  dataset_id=i.get("dataset_id"), locator=i.get("locator_json") or {}, excerpt=i["description"]) for i in high[:4]],
            magnitude=0.4, impact=0.6, tags=["gap", "data quality", "measurement", "data gap"], key="dq:high",
            records={"gaps": [{"gap_type": "data", "gap": "High-severity data-quality issues", "evidence": txt,
                               "why_it_matters": "Analysis on flawed data can mislead.", "significance": "high",
                               "missing_information": "Corrected source values.", "recommended_investigation": "Data owner review.",
                               "potential_action": "Fix at source and re-run."}]}))
    return out
