"""'What can I do with this data?' — analyses available given what was detected in the uploads."""
from __future__ import annotations

from .model import ProjectData


def detect_capabilities(pdata: ProjectData) -> dict:
    roles_by_ds = []
    for ds in pdata.datasets:
        roles = {c.role for c in ds.columns.values()}
        if ds.time_col or ds.period_groups:
            roles.add("time")
        roles_by_ds.append((ds, roles))
    all_roles = set().union(*[r for _, r in roles_by_ds]) if roles_by_ds else set()
    caps = []

    def where(*need: str) -> list[str]:
        return [ds.name for ds, r in roles_by_ds if all(any(x in r for x in n.split("|")) for n in need)]

    def add(key, title, description, need, questions, also_missing=""):
        found = where(*need)
        if found:
            caps.append({"key": key, "title": title, "description": description, "based_on": found[:5],
                         "detected": [n.replace("|", " or ") for n in need], "example_questions": questions,
                         "unlock_more": also_missing})

    add("growth_trends", "Growth trend analysis", "How key measures have changed over time, overall and by segment, with growth rates and CAGR.",
        ["time", "revenue|units|market_size|spend|measure|actual"], ["What are the major trends?", "Which segments are growing fastest?"])
    add("roas_drivers", "ROAS driver analysis", "Which channels/campaigns drive return on ad spend, and what changed it over time.",
        ["spend", "revenue", "channel|campaign"], ["Is ROAS being driven by a particular channel?", "What drove the change in ROAS?"])
    add("marketing_efficiency", "Marketing efficiency analysis", "CTR, CPC, CPM, CPA and spend share vs outcome share by channel.",
        ["spend", "clicks|impressions|conversions"], ["Which channels are inefficient?", "Where is spend wasted?"])
    add("product_opportunity", "Product opportunity analysis", "Segments with growing demand and limited competition — separating genuine opportunity from simply low competition.",
        ["category|attribute", "brand|company|product|competitor_count"], ["Which products/categories have potential?", "Where could a USP be built?"],
        "" if "growth" in all_roles or "time" in all_roles else "Add sales by period or a growth column to assess demand.")
    add("competitive_gaps", "Competitive gap analysis", "Where competitors are present and you are not; pricing position vs competitors.",
        ["brand|company", "category"], ["What gaps exist in our portfolio?", "How does our pricing compare?"],
        "" if pdata.focus else "Set 'Your company / brand' in project settings to compare yourself with competitors.")
    add("portfolio", "Portfolio analysis", "Contribution, concentration and momentum of products/categories in the portfolio.",
        ["product|category", "revenue|units|market_size"], ["Which products drive the portfolio?", "Is the portfolio too concentrated?"])
    add("whitespace", "Market whitespace analysis", "Empty combinations of product × channel / region / format that may be unserved.",
        ["category|product|attribute", "channel|geography|attribute"], ["Where is whitespace?", "Which markets are under-served?"])
    add("geographic", "Geographic analysis", "Performance and gaps by region/market.", ["geography"], ["Which regions are growing or declining?"])
    add("pricing", "Pricing analysis", "Price levels, gaps in the price ladder and price positioning.", ["price"], ["Are there price gaps?"])
    add("plan_vs_actual", "Planned vs actual analysis", "Execution against plan/target/budget.", ["plan", "actual"], ["Where are we behind plan?"])
    strategy_docs = [d for d in pdata.documents if d.role in ("previous_strategy", "current_strategy", "proposed_strategy", "plan")]
    if len(strategy_docs) >= 2:
        caps.append({"key": "strategy_change", "title": "Strategy change analysis", "description": "What was added, removed or changed between strategy documents.",
                     "based_on": [d.name for d in strategy_docs], "detected": ["2+ strategy documents"],
                     "example_questions": ["How does the new strategy differ from the previous one?"], "unlock_more": ""})
    if strategy_docs and pdata.datasets:
        caps.append({"key": "strategy_vs_data", "title": "Strategy vs performance", "description": "Whether the strategy's priorities are supported by the data — what to carry forward, reconsider or validate.",
                     "based_on": [strategy_docs[-1].name], "detected": ["strategy document", "performance data"],
                     "example_questions": ["How does future strategy compare with historical performance?", "What should be taken forward?"], "unlock_more": ""})
    reports = [d for d in pdata.documents if d.role not in ("previous_strategy", "current_strategy", "proposed_strategy", "plan")]
    if reports:
        caps.append({"key": "document_insights", "title": "Document insight extraction", "description": "Quantified statements, themes and claims from reports, cross-checked against data where possible.",
                     "based_on": [d.name for d in reports[:5]], "detected": ["text documents"],
                     "example_questions": ["What do the reports say about market growth?"], "unlock_more": ""})
    if pdata.datasets or pdata.documents:
        caps.append({"key": "research_gaps", "title": "Research gap analysis", "description": "What is known, unknown, and how to find out — with suggested methods.",
                     "based_on": ["all sources"], "detected": ["any data"], "example_questions": ["What should we investigate next?"], "unlock_more": ""})
    missing = []
    if "customer_segment" not in all_roles:
        missing.append("Customer/audience segment data would enable customer analysis.")
    if "spend" not in all_roles:
        missing.append("Media spend with revenue would enable ROAS analysis.")
    if not any(r in all_roles for r in ("brand", "company", "competitor_count")):
        missing.append("Competitor/brand data would enable competitive and opportunity analysis.")
    if "price" not in all_roles:
        missing.append("Prices would enable pricing analysis.")
    return {"capabilities": caps, "missing_data": missing,
            "detected_roles": sorted(r for r in all_roles if r not in ("unknown", "text", "identifier", "dimension", "measure"))}


def suggested_questions(pdata: ProjectData) -> list[str]:
    caps = detect_capabilities(pdata)["capabilities"]
    qs = []
    for c in caps:
        qs.extend(c["example_questions"][:1])
    if not qs:
        qs = ["What are the major trends?", "What gaps exist?"]
    return list(dict.fromkeys(qs))[:8]
