"""Document intelligence: reported facts, themes, strategy/change comparison, strategy vs data, contradictions."""
from __future__ import annotations

import re
from collections import defaultdict

import numpy as np

from .model import Chunk, DocumentView, Finding, ProjectData, fmt_pct
from .stats import pct_change

STRATEGY_ROLES = {"previous_strategy": 0, "current_strategy": 1, "plan": 1, "proposed_strategy": 2}
UP_WORDS = r"increase|increasing|grow|grew|growing|growth|expand|expanding|invest|scale|launch|launching|prioriti[sz]e|accelerate|double down|extend|introduce|rose|rising|up\b|gain"
DOWN_WORDS = r"reduce|reducing|cut|decrease|decline|declined|declining|exit|discontinue|deprioriti[sz]e|stop|fell|falling|drop|dropped|shrink|lower"
NUM_RE = re.compile(r"(?P<cur>[£$€₹])?\s?(?P<num>\d+(?:[.,]\d+)?)\s?(?P<unit>%|bn|m|k|million|billion|pts?|x)?", re.I)
PCT_STMT_RE = re.compile(r"(?P<pct>[+\-−]?\d+(?:\.\d+)?)\s?%")
SHORT_STOP = {"no", "na", "on", "in", "at", "to", "of", "or", "an", "as", "is", "it", "be", "by", "we", "us", "up", "do", "go", "me", "my"}
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9£$€])")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text) if len(s.strip()) >= 15]


def _tfidf(texts: list[str]):
    from sklearn.feature_extraction.text import TfidfVectorizer

    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1, sublinear_tf=True)
    return vec, vec.fit_transform(texts)


def _numbers(text: str) -> list[str]:
    return [m.group(0).strip() for m in NUM_RE.finditer(text) if m.group("num") and (m.group("unit") or m.group("cur"))]


# ----------------------------------------------------------------- reported facts
def reported_facts(pdata: ProjectData, mode: str) -> list[Finding]:
    """Statements in documents that report a quantified trend or figure (not verified by us)."""
    out: list[Finding] = []
    for doc in pdata.documents:
        if doc.role in STRATEGY_ROLES:
            continue
        count = 0
        for ch in doc.statements(25):
            for sent in sentences(ch.text):
                m = PCT_STMT_RE.search(sent)
                if not m:
                    continue
                up = re.search(UP_WORDS, sent, re.I)
                down = re.search(DOWN_WORDS, sent, re.I)
                if not (up or down or re.search(r"share|account|represent", sent, re.I)):
                    continue
                count += 1
                if count > (6 if mode == "quick" else 20):
                    break
                val = float(m.group("pct").replace("−", "-"))
                ents = _entities_in(sent, pdata)
                pairs = _entity_pct_pairs(sent, ents)
                out.append(Finding(
                    category="reported", title=f"{doc.name} reports: {sent[:110]}{'…' if len(sent) > 110 else ''}",
                    fact=f"The source states: \"{sent}\"",
                    interpretation="This figure is reported by the document; it has not been independently verified against the uploaded datasets"
                                   + (" (approximate: read by OCR)." if ch.approximate else "."),
                    validation="Check the source's methodology and date, and reconcile with any internal data.",
                    confidence="medium" if not ch.approximate else "low", evidence=[doc.evidence(ch, sent)],
                    magnitude=min(1.0, abs(val) / 40), impact=0.5,
                    tags=["reported", "document", "trend" if (up or down) else "fact", "growing" if up and not down else "declining" if down else ""] + [e.lower() for e in ents],
                    key=f"rep:{doc.file_id}:{hash(sent) % 10**8}",
                    details={"document": doc.name, "value_pct": val, "entities": ents, "pairs": pairs,
                             "direction": "up" if up and not down else "down" if down else ""}))
    return out


def _entity_pct_pairs(sent: str, ents: list[str]) -> list[dict]:
    """Pair each entity with the nearest percentage that follows it, with the direction word between them."""
    low = sent.lower()
    pcts = [(m.start(), float(m.group("pct").replace("−", "-"))) for m in PCT_STMT_RE.finditer(sent)]
    pairs = []
    for e in ents:
        pos = low.find(e)
        after = [(p, v) for p, v in pcts if p > pos and p - pos < 80]
        if not after:
            continue
        p, v = after[0]
        between = low[pos:p]
        direction = "down" if re.search(DOWN_WORDS, between) else "up" if re.search(UP_WORDS, between) else ""
        if direction:
            pairs.append({"entity": e, "pct": v, "direction": direction})
    return pairs


def _entities_in(text: str, pdata: ProjectData) -> list[str]:
    low = text.lower()
    found = []
    for v in _known_values(pdata):
        if (len(v) >= 3 or (len(v) == 2 and v not in SHORT_STOP)) and re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", low):
            found.append(v)
    # keep the most specific (longest) matches
    found.sort(key=len, reverse=True)
    keep = []
    for f in found:
        if not any(f in k for k in keep):
            keep.append(f)
    return keep[:6]


_KV_CACHE: dict[int, list[str]] = {}


def _known_values(pdata: ProjectData) -> list[str]:
    key = id(pdata)
    if key not in _KV_CACHE:
        vals = set()
        for ds in pdata.datasets:
            for c in ds.columns.values():
                if ds.approximate:
                    continue
                if c.dtype == "categorical" and c.role in ("category", "channel", "product", "brand", "company", "geography",
                                                            "campaign", "attribute", "customer_segment") and c.distinct <= 60:
                    vals.update(str(v).lower() for v in ds.df[c.name].dropna().unique())
        _KV_CACHE.clear()
        _KV_CACHE[key] = sorted(vals, key=len, reverse=True)
    return _KV_CACHE[key]


# ----------------------------------------------------------------- themes
def document_themes(pdata: ProjectData) -> list[Finding]:
    docs = [d for d in pdata.documents if sum(len(c.text) for c in d.chunks) > 300]
    if not docs:
        return []
    texts = [" ".join(c.text for c in d.chunks) for d in docs]
    try:
        vec, X = _tfidf(texts)
    except ValueError:
        return []
    terms = np.array(vec.get_feature_names_out())
    out = []
    for i, d in enumerate(docs):
        row = X[i].toarray()[0]
        top = [t for t in terms[np.argsort(row)[::-1][:12]] if not t.isdigit()][:8]
        if not top:
            continue
        ch = next((c for c in d.chunks if c.kind in ("paragraph", "list_item")), d.chunks[0])
        out.append(Finding(
            category="theme", title=f"Main themes in {d.name}: {', '.join(top[:4])}",
            fact=f"The most distinctive terms in {d.name} are: {', '.join(top)} (TF-IDF ranking across {len(docs)} document(s)).",
            interpretation="These terms indicate what the document emphasises relative to the other sources.",
            confidence="medium", evidence=[d.evidence(ch, f"Document: {d.name}")], magnitude=0.15, impact=0.3,
            tags=["document", "themes"] + top, key=f"theme:{d.file_id}", section="documents"))
    return out


# ----------------------------------------------------------------- strategy comparison
def _strategy_docs(pdata: ProjectData) -> list[DocumentView]:
    docs = [d for d in pdata.documents if d.role in STRATEGY_ROLES]
    return sorted(docs, key=lambda d: (STRATEGY_ROLES[d.role], d.period_label, d.order))


def strategy_comparison(pdata: ProjectData, mode: str) -> list[Finding]:
    docs = _strategy_docs(pdata)
    out: list[Finding] = []
    if len(docs) >= 2:
        old, new = docs[-2], docs[-1]
        out += _diff_documents(old, new, pdata)
    if docs:
        out += strategy_vs_data(docs[-1], pdata, mode)
    return out


def _diff_documents(old: DocumentView, new: DocumentView, pdata: ProjectData) -> list[Finding]:
    a = old.statements(20)
    b = new.statements(20)
    if not a or not b:
        return []
    texts = [c.text for c in a] + [c.text for c in b]
    vec, X = _tfidf(texts)
    A, B = X[: len(a)], X[len(a):]
    sim = (B @ A.T).toarray()
    unchanged, modified, added, removed = [], [], [], []
    matched_old = set()
    for j, ch in enumerate(b):
        i = int(np.argmax(sim[j]))
        s = float(sim[j, i])
        if s >= 0.8 and _numbers(ch.text) == _numbers(a[i].text):
            unchanged.append((a[i], ch, s))
            matched_old.add(i)
        elif s >= 0.3:
            modified.append((a[i], ch, s))
            matched_old.add(i)
        else:
            added.append(ch)
    removed = [c for i, c in enumerate(a) if i not in matched_old]
    ev_new = [new.evidence(c) for c in added[:6]] + [new.evidence(n) for _, n, _ in modified[:4]]
    ev_old = [old.evidence(c) for c in removed[:6]]
    lines = []
    if modified:
        lines.append("Changed: " + " | ".join(f"\"{o.text}\" → \"{n.text}\"" for o, n, _ in modified[:5]))
    if added:
        lines.append("Added: " + " | ".join(f"\"{c.text}\"" for c in added[:6]))
    if removed:
        lines.append("Removed: " + " | ".join(f"\"{c.text}\"" for c in removed[:6]))
    if unchanged:
        lines.append(f"Unchanged: {len(unchanged)} statement(s), e.g. \"{unchanged[0][1].text}\"")
    out = [Finding(
        category="strategy", title=f"Strategy change: {len(added)} added, {len(removed)} removed, {len(modified)} changed ({old.name} → {new.name})",
        fact=" ".join(lines),
        interpretation=f"Comparing {old.name} with {new.name} statement by statement (text similarity matching).",
        implication="Added and changed statements are where new assumptions are being made and should be checked against evidence.",
        validation="Review each added/changed statement against the performance data (see the strategy-vs-data findings).",
        confidence="medium", evidence=ev_new + ev_old, magnitude=min(1.0, (len(added) + len(removed) + len(modified)) / 12), impact=0.85,
        tags=["strategy", "change", "comparison", "strategy comparison", "added", "removed"], key=f"sdiff:{old.file_id}:{new.file_id}",
        details={"old": old.name, "new": new.name,
                 "added": [c.text for c in added], "removed": [c.text for c in removed],
                 "modified": [{"from": o.text, "to": n.text, "similarity": s} for o, n, s in modified],
                 "unchanged": [n.text for _, n, _ in unchanged]},
        records={"comparison": {"comparison_type": "strategy", "subject_a": old.name, "subject_b": new.name, "measure": "statements",
                                "value_a": float(len(a)), "value_b": float(len(b)),
                                "details": {"added": len(added), "removed": len(removed), "modified": len(modified), "unchanged": len(unchanged)}}})]
    # numeric target changes
    for o, n, s in modified:
        no, nn = _numbers(o.text), _numbers(n.text)
        if no and nn and no != nn:
            out.append(Finding(
                category="strategy", title=f"Target changed: {', '.join(no)} → {', '.join(nn)}",
                fact=f"Previous: \"{o.text}\" New: \"{n.text}\"",
                interpretation="A quantified commitment has been revised.",
                validation="Check whether historical performance supports the new figure.",
                confidence="high", evidence=[old.evidence(o), new.evidence(n)], magnitude=0.45, impact=0.8,
                tags=["strategy", "change", "target"] + _entities_in(n.text, pdata), key=f"starget:{o.id}:{n.id}"))
    return out


def strategy_vs_data(doc: DocumentView, pdata: ProjectData, mode: str) -> list[Finding]:
    """Check what the (latest) strategy says about entities against how they perform in the data."""
    from .marketing import _ratio_table, marketing_datasets

    perf = entity_performance(pdata)
    if not perf:
        return []
    out: list[Finding] = []
    mentioned = set()
    carry, reconsider, validate = [], [], []
    for ch in doc.statements(15):
        ents = _entities_in(ch.text, pdata)
        if not ents:
            continue
        up = re.search(UP_WORDS, ch.text, re.I)
        down = re.search(DOWN_WORDS, ch.text, re.I)
        maintain = re.search(r"maintain|keep|continue|sustain", ch.text, re.I)
        measuring = re.search(r"\b(measure|measuring|track|tracking|report|reporting|monitor|modelling|modeling|attribution)\b", ch.text, re.I)
        investing = re.search(r"invest|budget|spend|scale|launch|expand|grow|cut|reduce", ch.text, re.I)
        if measuring and not investing:
            intent = "measure"
        else:
            intent = "increase" if up and not down else "reduce" if down and not up else "maintain" if maintain else "mention"
        for e in ents:
            mentioned.add(e)
            p = perf.get(e)
            if intent == "measure":
                continue
            if not p:
                validate.append((ch, e, intent, None))
                continue
            signal = p["signal"]  # positive / negative / neutral
            if intent == "increase" and signal == "negative":
                reconsider.append((ch, e, intent, p))
            elif intent == "reduce" and signal == "positive":
                reconsider.append((ch, e, intent, p))
            elif intent in ("increase", "maintain") and signal == "positive":
                carry.append((ch, e, intent, p))
            elif intent == "increase" and signal == "neutral":
                validate.append((ch, e, intent, p))
            else:
                carry.append((ch, e, intent, p)) if signal != "negative" else reconsider.append((ch, e, intent, p))
    for ch, e, intent, p in reconsider:
        out.append(Finding(
            category="strategy", title=f"Strategy vs data mismatch: '{e}' — plan says {intent}, data shows {p['summary_short']}",
            fact=f"{doc.name} states: \"{ch.text}\". In the data, {p['summary']}.",
            interpretation=f"The strategy's direction for '{e}' is not supported by its recent performance.",
            implication="If the strategy proceeds unchanged, resources may go to an under-performing area (or be withdrawn from a performing one).",
            hypothesis=p.get("hypothesis", "There may be strategic reasons (e.g. brand building, long-term bets) not visible in the data."),
            validation="Ask the strategy owner for the evidence behind this choice; test with a controlled pilot before full commitment.",
            confidence="medium", evidence=[doc.evidence(ch)] + p["evidence"], magnitude=0.75, impact=0.95,
            tags=["strategy", "mismatch", "reconsider", "risk", "strategy comparison", "contradiction", e], key=f"smis:{ch.id}:{e}",
            records={"recommendations": [{"recommendation": f"Reconsider: \"{ch.text}\"", "rec_type": "reconsider", "priority": "high",
                                          "rationale": p["summary"]}],
                     "risks": [{"risk": f"Strategy for '{e}' conflicts with performance data", "horizon": "medium", "likelihood": "medium",
                                "impact": p["summary"], "mitigation": "Pilot and measure before scaling"}]}))
    for ch, e, intent, p in carry[:8]:
        out.append(Finding(
            category="strategy", title=f"Supported by data: '{e}' ({intent}) — {p['summary_short']}",
            fact=f"{doc.name} states: \"{ch.text}\". In the data, {p['summary']}.",
            interpretation=f"The strategy's direction for '{e}' is consistent with its recent performance.",
            implication="This element can reasonably be carried forward, subject to monitoring.",
            validation="Set measurable targets and monitor that the trend continues.",
            confidence="medium", evidence=[doc.evidence(ch)] + p["evidence"], magnitude=0.4, impact=0.7,
            tags=["strategy", "carry forward", "supported", "strategy comparison", e], key=f"scar:{ch.id}:{e}",
            records={"recommendations": [{"recommendation": f"Carry forward: \"{ch.text}\"", "rec_type": "carry_forward", "priority": "medium",
                                          "rationale": p["summary"]}]}))
    seen_v = {ch.id for ch, *_ in carry + reconsider}
    for ch, e, intent, p in validate:
        if ch.id in seen_v:
            continue
        seen_v.add(ch.id)
        out.append(Finding(
            category="strategy", title=f"Requires validation: \"{ch.text[:90]}\"",
            fact=f"{doc.name} states: \"{ch.text}\". " + (f"The uploaded data mentions '{e}' but shows no clear performance signal." if p else
                                                          f"No uploaded dataset contains performance data for '{e}'."),
            interpretation="This part of the strategy is not yet evidenced by the uploaded data.",
            validation=f"Gather evidence on '{e}' (market size, demand trend, competitor activity) before committing resources.",
            confidence="low", evidence=[doc.evidence(ch)] + (p["evidence"] if p else []), magnitude=0.35, impact=0.7,
            tags=["strategy", "validation", "requires validation", "research", e], key=f"sval:{ch.id}",
            records={"recommendations": [{"recommendation": f"Validate before committing: \"{ch.text}\"", "rec_type": "validate", "priority": "medium",
                                          "rationale": "Not evidenced in uploaded data"}],
                     "gaps": [{"gap_type": "strategy", "gap": f"Strategy item lacks supporting evidence: {ch.text[:120]}",
                               "evidence": f"No performance signal for '{e}' in uploaded data", "why_it_matters": "Unevidenced commitments carry higher risk.",
                               "significance": "medium", "missing_information": f"Demand, competition and performance data for '{e}'.",
                               "recommended_investigation": "Desk research and a small market test.", "potential_action": "Stage-gate the investment."}]}))
    # strong performers the strategy does not mention
    ignored = [(e, p) for e, p in perf.items() if p["signal"] == "positive" and p.get("strength", 0) >= 0.5 and e not in mentioned]
    ignored.sort(key=lambda t: -t[1].get("strength", 0))
    for e, p in ignored[:3]:
        out.append(Finding(
            category="gap", title=f"Strategy gap: '{e}' performs strongly but is not addressed in {doc.name}",
            fact=f"In the data, {p['summary']}. {doc.name} does not mention '{e}'.",
            interpretation="A growth or efficiency driver may be missing from the plan.",
            implication="Momentum could be lost if the area is not resourced deliberately.",
            validation=f"Confirm whether '{e}' was deliberately excluded and whether the performance is sustainable.",
            confidence="medium", evidence=p["evidence"], magnitude=0.55, impact=0.85,
            tags=["gap", "strategy", "strategy gap", "opportunity", e], key=f"sign:{doc.file_id}:{e}",
            records={"gaps": [{"gap_type": "strategy", "gap": f"'{e}' not addressed in strategy", "evidence": p["summary"],
                               "why_it_matters": "Strong performers left unplanned may lose momentum.", "significance": "medium",
                               "missing_information": "Rationale for excluding it.", "recommended_investigation": "Review with strategy owner.",
                               "potential_action": f"Consider explicit plans for '{e}'."}]}))
    return out


def entity_performance(pdata: ProjectData) -> dict[str, dict]:
    """Summarise how each named entity (dimension value) performs: growth trend and/or ROAS vs average."""
    from .marketing import _ratio_table, marketing_datasets

    perf: dict[str, dict] = {}
    for ds in marketing_datasets(pdata):
        if not ds.first("revenue"):
            continue
        for d in ds.dims(max_levels=40, limit=4):
            g = _ratio_table(ds, [d])
            tot = _ratio_table(ds, []).iloc[0]
            years = None
            if ds.time_col:
                years = __import__("pandas").to_datetime(ds.df[ds.time_col]).dt.year
            for lvl in g.index:
                roas = float(g.loc[lvl, "ROAS"]) if "ROAS" in g.columns else None
                if roas is None or np.isnan(roas):
                    continue
                trend_txt, rel = "", roas / max(float(tot["ROAS"]), 1e-9)
                trend_dir = 0
                if years is not None and years.nunique() >= 2:
                    ys = sorted(years.unique())[-2:]
                    sub = ds.df[ds.df[d] == lvl]
                    yy = years[sub.index]
                    r = {y: sub[yy == y][ds.first("revenue")].sum() / max(sub[yy == y][ds.first("spend")].sum(), 1e-9) for y in ys}
                    ch = pct_change(r[ys[0]], r[ys[1]])
                    if ch is not None:
                        trend_txt = f", ROAS {fmt_pct(ch)} from {ys[0]} to {ys[1]}"
                        trend_dir = 1 if ch > 5 else -1 if ch < -5 else 0
                signal = "negative" if rel < 0.7 or (rel < 1 and trend_dir < 0) else "positive" if rel > 1.1 or trend_dir > 0 and rel >= 0.9 else "neutral"
                key = str(lvl).lower()
                summary = f"'{lvl}' has ROAS {roas:.2f}x vs {float(tot['ROAS']):.2f}x average{trend_txt}"
                perf[key] = {"signal": signal, "summary": summary, "summary_short": f"ROAS {roas:.2f}x vs {float(tot['ROAS']):.2f}x avg{trend_txt}",
                             "strength": min(1.0, abs(rel - 1)), "evidence": [ds.evidence([d, ds.first("spend"), ds.first("revenue")], ds.df[d] == lvl, filt={d: lvl})],
                             "hypothesis": "The channel may serve upper-funnel goals that attributed revenue does not capture."}
    # growth signals from trend-capable datasets
    import pandas as pd

    for ds in pdata.datasets:
        demand = ds.first("revenue", "market_size", "units")
        for d in ds.dims(max_levels=40, limit=4):
            g = None
            if demand and ds.time_col and demand not in [c for cols in (ds.period_groups or {}).values() for c in cols.values()]:
                years = pd.to_datetime(ds.df[ds.time_col]).dt.year
                ys = sorted(years.dropna().unique())
                if len(ys) >= 2:
                    t = ds.df.groupby([ds.df[d], years])[demand].sum(min_count=1).unstack()
                    g = (t[ys[0]], t[ys[-1]], f"{ys[0]}→{ys[-1]}", [ds.time_col, d, demand])
            elif ds.period_groups:
                base, cols = next(iter(ds.period_groups.items()))
                ps = sorted(cols)
                t = ds.df.groupby(d)[[cols[ps[0]], cols[ps[-1]]]].sum(min_count=1)
                g = (t[cols[ps[0]]], t[cols[ps[-1]]], f"{ps[0]}→{ps[-1]}", [d] + list(cols.values()))
            if g is None:
                continue
            first, last, label, cols = g
            for lvl in first.index:
                key = str(lvl).lower()
                ch = pct_change(float(first.get(lvl, np.nan)), float(last.get(lvl, np.nan)))
                if ch is None or (key in perf and "ROAS" in perf[key]["summary"]):
                    continue
                signal = "positive" if ch >= 8 else "negative" if ch <= -3 else "neutral"
                summary = f"'{lvl}' {ds.role(demand) if demand else 'value'} changed {fmt_pct(ch)} ({label}, {ds.file_name})"
                if key not in perf or abs(ch) / 50 > perf[key].get("strength", 0):
                    perf[key] = {"signal": signal, "summary": summary, "summary_short": f"{fmt_pct(ch)} {label}", "strength": min(1.0, abs(ch) / 30),
                                 "evidence": [ds.evidence(cols, ds.df[d] == lvl, filt={d: lvl})]}
    return perf


# ----------------------------------------------------------------- contradictions
def contradictions(pdata: ProjectData, reported: list[Finding], comparison_findings: list[Finding]) -> list[Finding]:
    """Reported year-on-year percentages vs year-on-year changes computed from the datasets for the same entity."""
    out: list[Finding] = []
    computed: dict[str, tuple[float, Finding, str]] = {}
    for f in comparison_findings:
        a, b = str(f.details.get("a", "")), str(f.details.get("b", ""))
        if not (a.isdigit() and b.isdigit() and int(b) - int(a) == 1):
            continue
        for ent, pc in (f.details.get("changes") or {}).items():
            computed.setdefault(str(ent).lower(), (pc, f, b))
    for r in reported:
        for pair in r.details.get("pairs") or []:
            e, val, direction = pair["entity"], pair["pct"], pair["direction"]
            if e not in computed:
                continue
            pc, f, year = computed[e]
            if year not in r.evidence[0].excerpt and year not in r.evidence[0].source_label:
                # only compare when the report sentence refers to the same year (or the document is about it)
                doc_name = r.details.get("document", "")
                if year not in doc_name:
                    continue
            reported_val = abs(val) if direction == "up" else -abs(val)
            if np.sign(reported_val) != np.sign(pc) or abs(reported_val - pc) > max(10, abs(pc) * 0.5):
                out.append(Finding(
                    category="contradiction", title=f"Sources differ on '{e}' in {year}: report {fmt_pct(reported_val)} vs data {fmt_pct(pc)}",
                    fact=(f"{r.evidence[0].source_label}: \"{r.evidence[0].excerpt}\". Year-on-year change computed from "
                          f"{f.evidence[0].source_label}: {fmt_pct(pc)} ({f.details.get('a')}→{year})."),
                    interpretation=("The report and the uploaded data describe different changes. They may cover different scopes "
                                    "(e.g. total market vs own sales), periods or definitions — a difference can itself be informative "
                                    "(e.g. own performance ahead of or behind the market)."),
                    implication="Using either figure alone could mislead decisions about this segment.",
                    validation="Check the scope, period and definition used in each source before combining them.",
                    confidence="low", evidence=r.evidence + f.evidence[:1], magnitude=0.45, impact=0.7,
                    tags=["contradiction", "source comparison", "comparison", "data quality", e], key=f"contra:{r.key}:{e}"))
    return out
