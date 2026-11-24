"""Builds a report snapshot (JSON) from an analysis run. Exporters render this snapshot."""
from __future__ import annotations

from ..db import Connection, now
from ..services.analysis_service import serialize_run

SECTION_ORDER = [
    ("key_findings", "Key Findings", 10),
    ("major_trends", "Major Trends", 8),
    ("significant_changes", "Significant Changes", 6),
    ("comparisons", "Comparisons", 6),
    ("opportunities", "Opportunities", 8),
    ("gaps", "Gaps", 8),
    ("risks", "Risks", 6),
    ("marketing_insights", "Marketing Insights", 8),
    ("product_insights", "Product Insights", 6),
    ("strategic_implications", "Strategic Implications", 8),
    ("document_insights", "What the Documents Say", 6),
]


def _compact(f: dict) -> dict:
    return {"id": f["id"], "rank": f["rank"], "category": f["category"], "title": f["title"], "fact": f["fact"],
            "interpretation": f["interpretation"], "implication": f["implication"], "hypothesis": f["hypothesis"],
            "validation": f["validation"], "confidence": f["confidence"], "approximate": bool(f["is_approximate"]),
            "chart": f.get("chart_json"),
            "evidence": [{"source_label": e["source_label"], "excerpt": e["excerpt"], "file_id": e["file_id"],
                          "approximate": bool(e["is_approximate"]), "locator": e.get("locator_json") or {}} for e in f["evidence"]]}


def build_report(c: Connection, project: dict, run: dict, title: str | None = None) -> dict:
    data = serialize_run(c, run)
    result = data.get("result") or {}
    fmap = {f["id"]: f for f in data.get("findings", [])}
    sections = []
    used_in_key = set(result.get("sections", {}).get("key_findings", []))
    for key, label, limit in SECTION_ORDER:
        ids = result.get("sections", {}).get(key, [])
        if key != "key_findings":
            ids = [i for i in ids if i not in used_in_key] or ids  # avoid repeating key findings when alternatives exist
        items = [_compact(fmap[i]) for i in ids[:limit] if i in fmap]
        if items:
            sections.append({"key": key, "title": label, "findings": items})
    issues = c.fetchall(
        """SELECT q.issue_type, q.field, q.severity, q.description, q.suggested_resolution, f.original_name AS source
           FROM data_quality_issues q LEFT JOIN files f ON f.id = q.file_id WHERE q.project_id = ?
           ORDER BY CASE q.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END""", [project["id"]])
    questions = []
    for q in data.get("questions", []):
        a = q.get("answer_json") or {}
        questions.append({"question": q["question"], "origin": q["origin"], "status": q["status"], "answer": a.get("answer", ""),
                          "confidence": a.get("confidence", ""), "data_needed": a.get("data_needed", []),
                          "finding_ids": a.get("finding_ids", [])})
    return {
        "title": title or f"{project['name']} – Analysis Report",
        "generated_at": now(),
        "project": {k: project.get(k, "") for k in ("name", "description", "industry", "geography", "time_period", "business_objective", "focus_entity")},
        "run": {"id": run["id"], "mode": run["mode"], "created_at": run["created_at"], "status": run["status"]},
        "questions": questions,
        "executive_summary": result.get("executive_summary", []),
        "sections": sections,
        "gaps": data.get("gaps", []),
        "opportunities": data.get("opportunities", []),
        "trends": data.get("trends", []),
        "comparisons": data.get("comparisons", []),
        "data_quality": issues,
        "data_limitations": result.get("data_limitations", []),
        "research": result.get("research", {}),
        "recommendations": result.get("recommendations", []),
        "future_questions": result.get("future_questions", []),
        "sources": result.get("sources", []),
        "all_findings": [_compact(f) for f in data.get("findings", [])],
    }
