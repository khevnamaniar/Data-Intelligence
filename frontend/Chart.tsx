import { useMemo, useState } from "react";
import { ChartSpec } from "./api";

// Dependency-free SVG charts driven by the chart specs produced by the analysis engine.
// Colours follow the validated categorical palette (fixed order); hover tooltips on every mark;
// a legend for 2+ series; and a table view so values never depend on colour alone.

const SERIES = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`);
const W = 640, H = 260, M = { l: 56, r: 16, t: 12, b: 48 };

export function fmtVal(v: number | null | undefined, unit?: string) {
  if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
  if (unit === "%") return `${v.toFixed(1)}%`;
  if (unit === "x") return `${v.toFixed(2)}x`;
  const sign = ({ GBP: "£", USD: "$", EUR: "€", INR: "₹" } as Record<string, string>)[unit || ""] || "";
  const a = Math.abs(v);
  const s = a >= 1e9 ? `${(v / 1e9).toFixed(1)}bn` : a >= 1e6 ? `${(v / 1e6).toFixed(1)}m` : a >= 1e4 ? `${(v / 1e3).toFixed(0)}k` : a >= 100 ? v.toFixed(0) : v.toFixed(a < 10 ? 2 : 1);
  return v < 0 && sign ? `-${sign}${s.replace("-", "")}` : `${sign}${s}`;
}

function ticks(min: number, max: number, n = 5) {
  if (min === max) { max = min + 1; }
  const span = max - min;
  const step = Math.pow(10, Math.floor(Math.log10(span / n)));
  const err = (n * step) / span;
  const mult = err <= 0.15 ? 10 : err <= 0.35 ? 5 : err <= 0.75 ? 2 : 1;
  const s = step * mult;
  const out: number[] = [];
  for (let v = Math.floor(min / s) * s; v <= max + s * 0.001; v += s) out.push(Number(v.toPrecision(12)));
  return out;
}

type Tip = { x: number; y: number; lines: string[] } | null;

export default function Chart({ spec }: { spec: ChartSpec }) {
  const [tip, setTip] = useState<Tip>(null);
  const [table, setTable] = useState(false);
  const body = useMemo(() => render(spec, setTip), [spec]);
  if (!body) return null;
  const series = spec.series || [];
  return (
    <figure className="mt-3 rounded-md border border-slate-200 bg-[var(--surface-1)] p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <figcaption className="text-sm font-medium text-slate-900">{spec.title}</figcaption>
          {spec.question && <p className="text-xs text-slate-500">Answers: {spec.question}</p>}
        </div>
        <button onClick={() => setTable(!table)} className="text-xs text-slate-600 underline whitespace-nowrap">{table ? "Show chart" : "Show table"}</button>
      </div>
      {table ? <DataTable spec={spec} /> : (
        <div className="relative" onMouseLeave={() => setTip(null)}>
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={spec.title}>{body}</svg>
          {tip && (
            <div className="pointer-events-none absolute z-10 rounded border border-slate-200 bg-white px-2 py-1 text-xs shadow"
              style={{ left: `${(tip.x / W) * 100}%`, top: `${(tip.y / H) * 100}%`, transform: "translate(-50%, -110%)" }}>
              {tip.lines.map((l, i) => <div key={i} className={i === 0 ? "font-medium text-slate-900" : "text-slate-600"}>{l}</div>)}
            </div>
          )}
        </div>
      )}
      {!table && series.length > 1 && spec.type !== "waterfall" && (
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-700">
          {series.slice(0, 8).map((s, i) => (
            <span key={s.name} className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: SERIES[i], opacity: s.style === "dashed" ? 0.6 : 1 }} />{s.name}
            </span>
          ))}
        </div>
      )}
      {spec.note && <p className="mt-1 text-xs text-slate-500">{spec.note}</p>}
    </figure>
  );
}

function DataTable({ spec }: { spec: ChartSpec }) {
  let head: string[] = [], rows: (string | number)[][] = [];
  if (spec.points) {
    head = ["Label", spec.x_label || "x", spec.y_label || "y"];
    rows = spec.points.map((p) => [p.label || "", fmtVal(p.x, spec.type === "matrix" ? "" : ""), fmtVal(p.y, spec.unit)]);
  } else if (spec.values && spec.rows && spec.cols) {
    head = ["", ...spec.cols];
    rows = spec.rows.map((r, i) => [r, ...spec.values![i].map((v) => fmtVal(v, spec.unit))]);
  } else {
    head = [spec.x_label || "", ...(spec.series || []).map((s) => s.name)];
    rows = (spec.categories || []).map((c, i) => [c, ...(spec.series || []).map((s) => fmtVal(s.values[i], spec.unit))]);
  }
  return (
    <div className="mt-2 max-h-72 overflow-auto">
      <table className="w-full text-xs">
        <thead><tr>{head.map((h) => <th key={h} className="border-b px-2 py-1 text-left font-medium text-slate-600">{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="border-b border-slate-100 px-2 py-1">{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function yAxis(min: number, max: number, unit?: string) {
  const t = ticks(min, max);
  const lo = Math.min(min, t[0]), hi = Math.max(max, t[t.length - 1]);
  const y = (v: number) => M.t + (H - M.t - M.b) * (1 - (v - lo) / (hi - lo || 1));
  const els = t.map((v) => (
    <g key={v}>
      <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} stroke="var(--grid)" strokeWidth={1} />
      <text x={M.l - 6} y={y(v) + 3} textAnchor="end" fontSize={10} fill="var(--text-secondary)">{fmtVal(v, unit)}</text>
    </g>
  ));
  return { y, els, lo, hi };
}

function xLabels(cats: string[], x: (i: number) => number) {
  const step = Math.max(1, Math.ceil(cats.length / 10));
  const rotate = cats.length > 6;
  return cats.map((c, i) => i % step === 0 && (
    <text key={i} x={x(i)} y={H - M.b + 14} fontSize={10} fill="var(--text-secondary)" textAnchor={rotate ? "end" : "middle"}
      transform={rotate ? `rotate(-30 ${x(i)} ${H - M.b + 14})` : undefined}>{c.length > 16 ? c.slice(0, 15) + "…" : c}</text>
  ));
}

function render(spec: ChartSpec, setTip: (t: Tip) => void): React.ReactNode {
  const unit = spec.unit;
  const cats = (spec.categories || []).map(String);
  const series = (spec.series || []).slice(0, 8);
  const pw = W - M.l - M.r;
  switch (spec.type) {
    case "line": {
      const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
      if (!all.length) return null;
      const { y, els } = yAxis(Math.min(0, ...all) < 0 ? Math.min(...all) : Math.min(...all) * 0.9, Math.max(...all));
      const x = (i: number) => M.l + (cats.length <= 1 ? pw / 2 : (pw * i) / (cats.length - 1));
      return (
        <g>
          {els}{xLabels(cats, x)}
          {series.map((s, si) => {
            let d = "", pen = false;
            s.values.forEach((v, i) => { if (v === null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i)},${y(v)}`; pen = true; });
            return <path key={si} d={d} fill="none" stroke={SERIES[si]} strokeWidth={2} strokeDasharray={s.style === "dashed" ? "5 4" : undefined} />;
          })}
          {cats.map((c, i) => (
            <rect key={i} x={x(i) - pw / Math.max(cats.length, 1) / 2} y={M.t} width={pw / Math.max(cats.length, 1)} height={H - M.t - M.b} fill="transparent"
              onMouseEnter={() => setTip({ x: x(i), y: M.t + 10, lines: [c, ...series.map((s) => `${s.name}: ${fmtVal(s.values[i], unit)}`)] })} />
          ))}
        </g>
      );
    }
    case "bar": case "grouped_bar": case "stacked_bar": case "waterfall": {
      const stacked = spec.type === "stacked_bar";
      const n = cats.length || 1;
      let all: number[];
      if (stacked) all = cats.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0));
      else all = series.flatMap((s) => s.values.map((v) => v || 0));
      if (!all.length) return null;
      const { y, els } = yAxis(Math.min(0, ...all), Math.max(0, ...all), unit);
      const band = pw / n;
      const x = (i: number) => M.l + band * i + band / 2;
      const groups = stacked || spec.type === "waterfall" ? 1 : series.length;
      const bw = Math.max(3, Math.min(40, (band * 0.75) / groups - 2));
      const marks: React.ReactNode[] = [];
      cats.forEach((c, i) => {
        let acc = 0;
        series.forEach((s, si) => {
          const v = s.values[i] || 0;
          let y0: number, y1: number, bx: number;
          if (stacked) { y0 = y(acc); y1 = y(acc + v); acc += v; bx = x(i) - bw / 2; }
          else { y0 = y(0); y1 = y(v); bx = x(i) - (groups * (bw + 2)) / 2 + (spec.type === "waterfall" ? 0 : si) * (bw + 2) + 1; }
          const top = Math.min(y0, y1), h = Math.max(1, Math.abs(y1 - y0) - (stacked ? 2 : 0));
          const fill = spec.type === "waterfall" ? (v >= 0 ? "var(--pos)" : "var(--neg)") : SERIES[stacked || groups > 1 ? si : 0];
          marks.push(<rect key={`${i}-${si}`} x={bx} y={top} width={bw} height={h} rx={2} fill={fill}
            onMouseEnter={() => setTip({ x: bx + bw / 2, y: top, lines: [c, `${s.name}: ${fmtVal(s.values[i], unit)}`] })} />);
        });
      });
      return <g>{els}<line x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} stroke="var(--text-muted)" />{marks}{xLabels(cats, x)}</g>;
    }
    case "scatter": case "matrix": {
      const pts = (spec.points || []).slice(0, 400);
      if (!pts.length) return null;
      const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
      const xmin = Math.min(0, ...xs), xmax = Math.max(...xs) * 1.05 || 1;
      const { y, els } = yAxis(Math.min(0, ...ys), Math.max(...ys) * 1.05 || 1, spec.type === "matrix" ? "%" : spec.unit === "%" ? "%" : undefined);
      const x = (v: number) => M.l + (pw * (v - xmin)) / (xmax - xmin || 1);
      const maxSize = Math.max(...pts.map((p) => p.size || 1));
      const xt = ticks(xmin, xmax);
      const groups = Array.from(new Set(pts.map((p) => p.group || "")));
      return (
        <g>
          {els}
          {xt.map((v) => <text key={v} x={x(v)} y={H - M.b + 14} fontSize={10} textAnchor="middle" fill="var(--text-secondary)">{fmtVal(v)}</text>)}
          <text x={M.l + pw / 2} y={H - 8} fontSize={10} textAnchor="middle" fill="var(--text-secondary)">{spec.x_label}</text>
          {spec.reference_line === "y=x" && <line x1={x(0)} y1={y(0)} x2={x(Math.min(xmax, Math.max(...ys)))} y2={y(Math.min(xmax, Math.max(...ys)))} stroke="var(--text-muted)" strokeDasharray="4 4" />}
          {spec.type === "matrix" && spec.x_split !== undefined && <line x1={x(spec.x_split)} x2={x(spec.x_split)} y1={M.t} y2={H - M.b} stroke="var(--text-muted)" strokeDasharray="3 3" />}
          {spec.type === "matrix" && spec.y_split !== undefined && <line x1={M.l} x2={W - M.r} y1={y(spec.y_split)} y2={y(spec.y_split)} stroke="var(--text-muted)" strokeDasharray="3 3" />}
          {pts.map((p, i) => {
            const r = maxSize > 1 ? 4 + 14 * Math.sqrt((p.size || 1) / maxSize) : 4;
            const gi = Math.min(groups.indexOf(p.group || ""), 2);
            return (
              <g key={i}>
                <circle cx={x(p.x)} cy={y(p.y)} r={r} fill={SERIES[spec.type === "matrix" ? gi : 0]} fillOpacity={0.55} stroke="var(--surface-1)" strokeWidth={2}
                  onMouseEnter={() => setTip({ x: x(p.x), y: y(p.y) - r, lines: [p.label || "", `${spec.x_label}: ${fmtVal(p.x)}`, `${spec.y_label}: ${fmtVal(p.y, spec.unit)}`, ...(p.group ? [p.group.replace(/_/g, " ")] : [])] })} />
                {pts.length <= 14 && p.label && <text x={x(p.x) + r + 2} y={y(p.y) + 3} fontSize={9} fill="var(--text-primary)">{p.label.slice(0, 18)}</text>}
              </g>
            );
          })}
        </g>
      );
    }
    case "heatmap": {
      const rows = spec.rows || [], cols = spec.cols || [], vals = spec.values || [];
      if (!rows.length || !cols.length) return null;
      const max = Math.max(...vals.flat(), 1);
      const left = 110, cw = (W - left - M.r) / cols.length, ch = (H - M.t - 40) / rows.length;
      return (
        <g>
          {rows.map((r, i) => <text key={r} x={left - 6} y={M.t + ch * i + ch / 2 + 3} fontSize={10} textAnchor="end" fill="var(--text-secondary)">{r.slice(0, 18)}</text>)}
          {cols.map((c, j) => <text key={c} x={left + cw * j + cw / 2} y={H - 24} fontSize={10} textAnchor="middle" fill="var(--text-secondary)">{c.slice(0, 12)}</text>)}
          {vals.map((row, i) => row.map((v, j) => (
            <g key={`${i}-${j}`}>
              <rect x={left + cw * j + 1} y={M.t + ch * i + 1} width={cw - 2} height={ch - 2} rx={2}
                fill={v === 0 ? "#ffffff" : "var(--series-1)"} fillOpacity={v === 0 ? 1 : 0.15 + 0.85 * (v / max)} stroke={v === 0 ? "var(--neg)" : "none"} strokeDasharray={v === 0 ? "3 2" : undefined}
                onMouseEnter={() => setTip({ x: left + cw * j + cw / 2, y: M.t + ch * i, lines: [`${rows[i]} × ${cols[j]}`, fmtVal(v, spec.unit)] })} />
              {v === 0 && <text x={left + cw * j + cw / 2} y={M.t + ch * i + ch / 2 + 3} fontSize={9} textAnchor="middle" fill="var(--text-secondary)">none</text>}
            </g>
          )))}
        </g>
      );
    }
    default:
      return null;
  }
}
