import { useState } from "react";
import Chart from "../Chart";

// Executive (CXO) dashboard: one plain-language answer card per question, plus an at-a-glance summary.
// Everything shown is a re-presentation of computed findings; "See evidence" opens the full analysis.

type Card = {
  question: string; status: "positive" | "watch" | "action" | "insufficient" | "info"; status_label: string;
  headline: string; summary: string; so_what: string; next_step: string; kpi: { value: string; label: string } | null;
  confidence: string; confidence_label: string; chart: any; approximate?: boolean; supporting: string[]; finding_ids: string[];
  origin: string;
};
export type ExecutiveData = {
  cards: Card[]; tiles: { label: string; value: string; note: string }[]; decisions: string[]; top_risk: string; top_win: string; note: string;
};

const TONE: Record<string, { bar: string; chip: string; icon: string }> = {
  positive: { bar: "border-l-emerald-500", chip: "bg-emerald-50 text-emerald-800", icon: "▲" },
  watch: { bar: "border-l-amber-500", chip: "bg-amber-50 text-amber-800", icon: "◆" },
  action: { bar: "border-l-red-500", chip: "bg-red-50 text-red-800", icon: "!" },
  insufficient: { bar: "border-l-slate-300", chip: "bg-slate-100 text-slate-700", icon: "?" },
  info: { bar: "border-l-blue-500", chip: "bg-blue-50 text-blue-800", icon: "i" },
};

export function ExecutiveDashboard({ data, onEvidence }: { data: ExecutiveData; onEvidence: (findingId: string) => void }) {
  return (
    <div className="space-y-5" data-testid="executive-dashboard">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {data.tiles.map((t) => (
          <div key={t.label} className="rounded-lg border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{t.label}</p>
            <p className="mt-1 text-3xl font-semibold text-slate-900">{t.value}</p>
            <p className="mt-0.5 text-xs text-slate-500 line-clamp-2">{t.note}</p>
          </div>
        ))}
      </div>

      {(data.top_win || data.top_risk || data.decisions.length > 0) && (
        <div className="grid items-start gap-3 lg:grid-cols-3">
          {data.top_win && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-emerald-800">▲ Biggest positive</p>
              <p className="mt-1 text-sm font-medium text-slate-900">{data.top_win}</p>
            </div>
          )}
          {data.top_risk && (
            <div className="rounded-lg border border-red-200 bg-red-50/60 p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-red-800">! Biggest concern</p>
              <p className="mt-1 text-sm font-medium text-slate-900">{data.top_risk}</p>
            </div>
          )}
          {data.decisions.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Decisions to consider</p>
              <ol className="mt-1 list-decimal space-y-1 pl-4 text-sm text-slate-800">{data.decisions.map((d, i) => <li key={i}>{d}</li>)}</ol>
            </div>
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {data.cards.map((c, i) => <AnswerCard key={i} c={c} onEvidence={onEvidence} />)}
      </div>
      <p className="text-xs text-slate-500">{data.note}</p>
    </div>
  );
}

function AnswerCard({ c, onEvidence }: { c: Card; onEvidence: (id: string) => void }) {
  const [chart, setChart] = useState(false);
  const tone = TONE[c.status] || TONE.info;
  return (
    <article className={`flex flex-col rounded-lg border border-l-4 border-slate-200 bg-white p-4 ${tone.bar}`} data-testid="exec-card">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium text-slate-500">{c.origin === "auto" ? "Suggested question" : "Your question"}: {c.question}</p>
        <span className={`whitespace-nowrap rounded px-2 py-0.5 text-[11px] font-semibold ${tone.chip}`}><span aria-hidden>{tone.icon} </span>{c.status_label}</span>
      </div>
      <div className="mt-2 flex items-start gap-4">
        <h3 className="flex-1 text-base font-semibold leading-snug text-slate-900">{c.headline}</h3>
        {c.kpi && (
          <div className="min-w-[7rem] text-right">
            <p className="text-2xl font-bold text-slate-900">{c.kpi.value}</p>
            <p className="text-[11px] leading-tight text-slate-500">{c.kpi.label}</p>
          </div>
        )}
      </div>
      {c.summary && <p className="mt-2 text-sm text-slate-700">{c.summary}</p>}
      <dl className="mt-3 space-y-2 text-sm">
        {c.so_what && <div><dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">What it means</dt><dd className="text-slate-800">{c.so_what}</dd></div>}
        {c.next_step && <div><dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Recommended next step</dt><dd className="text-slate-800">{c.next_step}</dd></div>}
      </dl>
      {c.supporting.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-slate-600">{c.supporting.map((s, i) => <li key={i}>{s}</li>)}</ul>
      )}
      {chart && c.chart && <Chart spec={c.chart} />}
      <div className="mt-auto flex flex-wrap items-center gap-3 pt-3 text-xs">
        <span className="text-slate-600" title="How strong the evidence behind this answer is">Evidence: <b>{c.confidence_label}</b>{c.approximate && " · includes approximate values"}</span>
        {c.chart && <button className="text-blue-700 underline" onClick={() => setChart(!chart)}>{chart ? "Hide chart" : "Show chart"}</button>}
        {c.finding_ids.length > 0 && <button className="text-blue-700 underline" onClick={() => onEvidence(c.finding_ids[0])}>See evidence</button>}
      </div>
    </article>
  );
}
