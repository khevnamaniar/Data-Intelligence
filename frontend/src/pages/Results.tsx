import { useEffect, useMemo, useState } from "react";
import { api, Finding, fmtDate } from "../api";
import { FindingCard } from "../findings";
import { Link, navigate, parseHash } from "../router";
import { Button, ConfidenceBadge, Empty, ErrorBox, Panel, Spinner, Tag, useAsync } from "../ui";
import type { ProjectDetail } from "./Project";
import { ErrorList } from "./Project";

const SECTIONS: [string, string][] = [
  ["key_findings", "Key Findings"], ["major_trends", "Major Trends"], ["significant_changes", "Significant Changes"],
  ["comparisons", "Comparisons"], ["opportunities", "Opportunities"], ["gaps", "Gaps"], ["risks", "Risks"],
  ["marketing_insights", "Marketing Insights"], ["product_insights", "Product Insights"], ["strategic_implications", "Strategic Implications"],
  ["document_insights", "What the Documents Say"],
];

export function ResultsPage({ d, runId }: { d: ProjectDetail; runId?: string }) {
  const pid = d.project.id;
  const st = useAsync(() => (runId ? api.get<{ run: any }>(`/api/projects/${pid}/analyses/${runId}`) : Promise.resolve(null)), [runId]);
  const run = st.data?.run;
  const active = run && (run.status === "queued" || run.status === "running");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(st.reload, 1500);
    return () => clearInterval(t);
  }, [active]);
  if (!runId) return <Empty title="No analysis yet"><Button onClick={() => navigate(`/p/${pid}/analyse`)}>Configure an analysis</Button></Empty>;
  if (st.loading && !run) return <Spinner label="Loading results…" />;
  if (st.error) return <ErrorBox error={st.error} />;
  return (
    <div className="space-y-4">
      <RunPicker d={d} current={runId} />
      {active && (
        <Panel>
          <p className="text-sm font-medium">{run.mode[0].toUpperCase() + run.mode.slice(1)} analysis is running — {run.stage}</p>
          <div className="mt-2 h-2 w-full rounded bg-slate-100"><div className="h-2 rounded bg-slate-800 transition-all" style={{ width: `${run.progress}%` }} /></div>
        </Panel>
      )}
      {run.status === "failed" && <ErrorList errors={run.errors} />}
      {(run.status === "completed" || run.status === "partial") && <Results run={run} d={d} />}
    </div>
  );
}

function RunPicker({ d, current }: { d: ProjectDetail; current: string }) {
  if (d.runs.length < 2) return null;
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-slate-500">Analysis run:</span>
      <select className="rounded border border-slate-300 bg-white px-2 py-1" value={current} onChange={(e) => navigate(`/p/${d.project.id}/results/${e.target.value}`)}>
        {d.runs.map((r) => <option key={r.id} value={r.id}>{fmtDate(r.created_at)} · {r.mode} · {r.status}</option>)}
      </select>
    </div>
  );
}

function Results({ run, d }: { run: any; d: ProjectDetail }) {
  const pid = d.project.id;
  const res = run.result || {};
  const fmap = useMemo(() => Object.fromEntries((run.findings as Finding[]).map((f) => [f.id, f])), [run]);
  const highlight = parseHash().query.get("f");
  const userQs = run.questions.filter((q: any) => q.origin === "user");
  const autoQs = run.questions.filter((q: any) => q.origin === "auto");
  const files = d.files.filter((f) => !run.file_ids.length || run.file_ids.includes(f.id));
  const [showAll, setShowAll] = useState(!!highlight);
  const [cat, setCat] = useState("");
  const research = res.research || {};
  const nav = [["summary", "Executive Summary"], ["questions", "Your Questions"], ...SECTIONS.filter(([k]) => res.sections?.[k]?.length), ["all", "All findings"],
    ["limitations", "Data Limitations"], ["unknown", "What We Still Need to Know"], ["research", "Recommended Research"], ["actions", "Recommended Actions"],
    ["future", "Future Questions"], ["sources", "Source References"]];
  useEffect(() => { if (highlight) document.getElementById(`f-${highlight}`)?.scrollIntoView({ block: "center" }); }, [highlight]);

  return (
    <div className="grid gap-6 lg:grid-cols-[13rem_1fr]">
      <aside className="hidden lg:block">
        <nav className="sticky top-4 space-y-0.5 text-sm">
          {nav.map(([k, l]) => <a key={k} href={`#${parseHash().path}`} onClick={(e) => { e.preventDefault(); document.getElementById(`s-${k}`)?.scrollIntoView({ behavior: "smooth" }); }} className="block rounded px-2 py-1 text-slate-600 hover:bg-slate-100 hover:text-slate-900">{l}</a>)}
        </nav>
      </aside>
      <div className="min-w-0 space-y-6">
        <Panel>
          <div className="grid gap-3 text-sm sm:grid-cols-4">
            <div><p className="text-slate-500">Mode</p><p className="font-medium capitalize">{run.mode} analysis</p></div>
            <div><p className="text-slate-500">Data analysed</p><p>{files.length} file(s)</p></div>
            <div><p className="text-slate-500">Findings</p><p>{run.findings.length}</p></div>
            <div><p className="text-slate-500">Completed</p><p>{fmtDate(run.completed_at)}</p></div>
          </div>
          <details className="mt-2 text-xs text-slate-600"><summary className="cursor-pointer">Files included</summary>{files.map((f) => f.original_name).join(" · ")}</details>
          {run.status === "partial" && <div className="mt-3"><ErrorList errors={run.errors} /></div>}
          <div className="mt-3 flex gap-2">
            <Button small onClick={() => navigate(`/p/${pid}/reports?run=${run.id}`)}>Generate report</Button>
            <Button small kind="secondary" onClick={() => navigate(`/p/${pid}/ask`)}>Ask a follow-up question</Button>
          </div>
        </Panel>

        <Section id="summary" title="Executive Summary">
          <ul className="list-disc space-y-1 pl-5 text-sm text-slate-800">{(res.executive_summary || []).map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>
          <p className="mt-3 text-xs text-slate-500">Each finding separates <b>Fact</b> (what the data shows) from <b>Interpretation</b>, <b>Implication</b>, <b>Hypothesis</b> and <b>Validation</b>. Correlation is never presented as causation.</p>
        </Section>

        <Section id="questions" title="Your Questions">
          <div className="space-y-3">
            {userQs.length === 0 && <p className="text-sm text-slate-600">No questions were entered; see the automatically generated questions below.</p>}
            {[...userQs, ...autoQs].map((q: any) => <QuestionAnswer key={q.id} q={q} fmap={fmap} />)}
          </div>
        </Section>

        {SECTIONS.map(([k, title]) => {
          const ids: string[] = res.sections?.[k] || [];
          if (!ids.length) return null;
          return <FindingSection key={k} id={k} title={title} ids={ids} fmap={fmap} pid={pid} highlight={highlight} limit={k === "key_findings" ? 10 : 5} />;
        })}

        <Section id="all" title={`All findings (${run.findings.length})`}>
          <div className="mb-3 flex flex-wrap gap-1">
            {["", ...Array.from(new Set(run.findings.map((f: Finding) => f.category)))].map((c: any) => (
              <button key={c} onClick={() => { setCat(c); setShowAll(true); }} className={`rounded border px-2 py-0.5 text-xs ${cat === c ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300"}`}>{c || "all"}</button>
            ))}
          </div>
          {showAll ? (
            <div className="space-y-2">{(run.findings as Finding[]).filter((f) => !cat || f.category === cat).map((f) => <div id={`f-${f.id}`} key={f.id}><FindingCard f={f} projectId={pid} defaultOpen={f.id === highlight} /></div>)}</div>
          ) : <Button kind="secondary" onClick={() => setShowAll(true)}>Show all {run.findings.length} ranked findings</Button>}
        </Section>

        <Section id="limitations" title="Data Limitations">
          <ul className="space-y-1 text-sm">{(res.data_limitations || []).map((l: any, i: number) => <li key={i}><b>{l.limitation}</b> <span className="text-slate-600">— {l.effect}</span></li>)}</ul>
          <p className="mt-2 text-sm"><Link to={`/p/${pid}/sources`} className="text-blue-700 underline">Open the Data Quality Report</Link></p>
        </Section>

        <Section id="unknown" title="What We Know / What We Still Need to Know">
          <div className="grid gap-4 md:grid-cols-2">
            <div><p className="mb-1 text-xs font-semibold tracking-wide text-slate-500">WHAT WE KNOW</p>
              <ul className="list-disc space-y-1 pl-5 text-sm">{(research.known || []).map((k: any, i: number) => <li key={i}>{k.statement} <span className="text-xs text-slate-500">[{k.confidence}]</span></li>)}</ul></div>
            <div><p className="mb-1 text-xs font-semibold tracking-wide text-slate-500">WHAT WE DON'T KNOW</p>
              <ul className="list-disc space-y-1 pl-5 text-sm">{(research.unknown || []).map((u: any, i: number) => <li key={i}>{u.statement} {u.context && <span className="text-xs text-slate-500">({u.context})</span>}</li>)}</ul></div>
          </div>
          {research.assumptions?.length > 0 && <div className="mt-3"><p className="text-xs font-semibold tracking-wide text-slate-500">ASSUMPTIONS</p><ul className="list-disc pl-5 text-sm">{research.assumptions.map((a: string) => <li key={a}>{a}</li>)}</ul></div>}
          {research.hypotheses?.length > 0 && <div className="mt-3"><p className="text-xs font-semibold tracking-wide text-slate-500">HYPOTHESES TO VALIDATE</p><ul className="list-disc pl-5 text-sm">{research.hypotheses.map((h: any, i: number) => <li key={i}>{h.hypothesis} <span className="text-xs text-slate-500">— validate: {h.validation}</span></li>)}</ul></div>}
        </Section>

        <Section id="research" title="Recommended Research — what we should find out and how">
          <table className="w-full text-sm"><thead className="text-left text-slate-500"><tr><th className="py-1 pr-3 font-medium">Question</th><th className="pr-3 font-medium">How we can find out</th><th className="font-medium">Priority</th></tr></thead>
            <tbody>{(research.to_find_out || []).map((q: any, i: number) => (
              <tr key={i} className="border-t border-slate-100 align-top"><td className="py-1.5 pr-3">{q.question}</td><td className="pr-3 text-xs">{q.methodologies.join(", ")}</td><td><Tag tone={q.priority === "high" ? "red" : q.priority === "medium" ? "amber" : "slate"}>{q.priority}</Tag></td></tr>))}</tbody></table>
        </Section>

        <Section id="actions" title="Recommended Actions">
          <ul className="space-y-1.5 text-sm">{(res.recommendations || []).map((r: any, i: number) => (
            <li key={i} className="flex gap-2"><Tag tone={r.priority === "high" ? "red" : "slate"}>{r.priority}</Tag><span>{r.recommendation} <span className="text-xs text-slate-500">({r.rec_type.replace("_", " ")})</span>
              {r.finding_id && fmap[r.finding_id] && <a href={`#/p/${pid}/results/${run.id}?f=${r.finding_id}`} onClick={() => setShowAll(true)} className="ml-1 text-xs text-blue-700 underline">evidence</a>}</span></li>))}</ul>
        </Section>

        <Section id="future" title="Future Questions">
          <ul className="space-y-1 text-sm">{(res.future_questions || []).map((q: any, i: number) => (
            <li key={i}>{q.question} <span className="text-xs text-slate-500">— {q.rationale} Methods: {q.methodologies.join(", ")}</span>
              <button className="ml-2 text-xs text-blue-700 underline" onClick={() => navigate(`/p/${pid}/ask?q=${encodeURIComponent(q.question)}`)}>ask</button></li>))}</ul>
        </Section>

        <Section id="sources" title="Source References">
          <table className="w-full text-sm"><thead className="text-left text-slate-500"><tr><th className="py-1 pr-3 font-medium">File</th><th className="pr-3 font-medium">Type</th><th className="pr-3 font-medium">Role</th><th className="font-medium">Findings citing it</th></tr></thead>
            <tbody>{(res.sources || []).map((s: any) => (
              <tr key={s.file_id} className="border-t border-slate-100"><td className="py-1.5 pr-3"><a className="text-blue-700 underline" href={`/api/projects/${pid}/files/${s.file_id}/download`}>{s.name}</a></td><td className="pr-3">{s.type}</td><td className="pr-3">{s.role.replace(/_/g, " ")}</td><td>{s.findings_citing}</td></tr>))}</tbody></table>
        </Section>
      </div>
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return <section id={`s-${id}`} className="scroll-mt-4"><Panel title={title}>{children}</Panel></section>;
}

function FindingSection({ id, title, ids, fmap, pid, highlight, limit }: { id: string; title: string; ids: string[]; fmap: Record<string, Finding>; pid: string; highlight: string | null; limit: number }) {
  const [more, setMore] = useState(false);
  const items = ids.map((i) => fmap[i]).filter(Boolean);
  const shown = more ? items : items.slice(0, limit);
  return (
    <Section id={id} title={`${title} (${items.length})`}>
      <div className="space-y-2">{shown.map((f) => <div id={`f-${f.id}`} key={f.id}><FindingCard f={f} projectId={pid} defaultOpen={f.id === highlight || (id === "key_findings" && f.rank === 1)} /></div>)}</div>
      {items.length > limit && <div className="mt-2"><Button kind="ghost" small onClick={() => setMore(!more)}>{more ? "Show fewer" : `Show more (${items.length - limit})`}</Button></div>}
    </Section>
  );
}

function QuestionAnswer({ q, fmap }: { q: any; fmap: Record<string, Finding> }) {
  const a = q.answer_json || {};
  return (
    <div className="rounded border border-slate-200 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium">{q.question}</p>
        {q.origin === "auto" && <Tag>generated by the system</Tag>}
        {a.confidence && <ConfidenceBadge level={a.confidence} />}
      </div>
      <p className={`mt-1 text-sm ${a.status === "insufficient" ? "text-amber-800" : "text-slate-700"}`}>{a.answer}</p>
      {a.data_needed?.length > 0 && <p className="mt-1 text-xs text-slate-600">Data that would be needed: {a.data_needed.join("; ")}</p>}
      {a.finding_ids?.length > 0 && (
        <p className="mt-1 text-xs text-slate-500">Based on: {a.finding_ids.filter((i: string) => fmap[i]).slice(0, 4).map((i: string) => (
          <a key={i} className="mr-2 text-blue-700 underline" href={`${window.location.hash.split("?")[0]}?f=${i}`}>{fmap[i].title.slice(0, 60)}</a>))}</p>
      )}
    </div>
  );
}
