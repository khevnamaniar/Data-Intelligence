import { useEffect, useState } from "react";
import { ApiError, ErrorInfo } from "./api";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Button({ children, onClick, kind = "primary", disabled, type = "button", small, title }: {
  children: React.ReactNode; onClick?: () => void; kind?: "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean; type?: "button" | "submit"; small?: boolean; title?: string;
}) {
  const styles = {
    primary: "bg-slate-900 text-white hover:bg-slate-700",
    secondary: "bg-white text-slate-800 border border-slate-300 hover:bg-slate-50",
    danger: "bg-white text-red-700 border border-red-300 hover:bg-red-50",
    ghost: "text-slate-600 hover:text-slate-900 hover:bg-slate-100",
  }[kind];
  return (
    <button type={type} title={title} onClick={onClick} disabled={disabled}
      className={cx("rounded-md font-medium transition disabled:opacity-50 disabled:cursor-not-allowed", small ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm", styles)}>
      {children}
    </button>
  );
}

export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-slate-700 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500 mt-1">{hint}</span>}
    </label>
  );
}

export const inputCls = "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400";

export function Panel({ title, children, actions, className }: { title?: React.ReactNode; children: React.ReactNode; actions?: React.ReactNode; className?: string }) {
  return (
    <section className={cx("bg-white border border-slate-200 rounded-lg", className)}>
      {(title || actions) && (
        <div className="flex items-center justify-between px-5 pt-4 pb-2">
          {title && <h2 className="text-base font-semibold text-slate-900">{title}</h2>}
          {actions}
        </div>
      )}
      <div className="px-5 pb-5 pt-1">{children}</div>
    </section>
  );
}

const CONF = { high: "bg-emerald-50 text-emerald-800 border-emerald-200", medium: "bg-amber-50 text-amber-800 border-amber-200", low: "bg-slate-100 text-slate-700 border-slate-300" };

export function ConfidenceBadge({ level }: { level: "high" | "medium" | "low" | string }) {
  const icon = level === "high" ? "●●●" : level === "medium" ? "●●○" : "●○○";
  return (
    <span title="Confidence: HIGH = directly supported by reliable data; MEDIUM = supported but needs interpretation; LOW = hypothesis with insufficient evidence"
      className={cx("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide", (CONF as any)[level] || CONF.low)}>
      <span aria-hidden className="text-[8px] tracking-tighter">{icon}</span>{level} confidence
    </span>
  );
}

export function Tag({ children, tone = "slate" }: { children: React.ReactNode; tone?: "slate" | "blue" | "amber" | "red" | "green" }) {
  const t = { slate: "bg-slate-100 text-slate-700", blue: "bg-blue-50 text-blue-800", amber: "bg-amber-50 text-amber-800", red: "bg-red-50 text-red-800", green: "bg-emerald-50 text-emerald-800" }[tone];
  return <span className={cx("inline-block rounded px-1.5 py-0.5 text-[11px] font-medium", t)}>{children}</span>;
}

export function ErrorBox({ error, onClose }: { error: unknown; onClose?: () => void }) {
  if (!error) return null;
  const info: ErrorInfo = error instanceof ApiError ? error.info : { what: String((error as any)?.message || error) };
  return (
    <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900">
      <div className="flex justify-between gap-3">
        <div>
          <p className="font-semibold">What happened: {info.what}</p>
          {info.why && <p className="mt-0.5"><span className="font-medium">Why:</span> {info.why}</p>}
          {info.can_still && <p className="mt-0.5"><span className="font-medium">What still works:</span> {info.can_still}</p>}
          {info.next && <p className="mt-0.5"><span className="font-medium">What you can do:</span> {info.next}</p>}
        </div>
        {onClose && <button onClick={onClose} className="text-red-700 text-xs" aria-label="Dismiss">✕</button>}
      </div>
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-600">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700" />
      {label}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
      <p className="font-medium text-slate-800">{title}</p>
      {children && <div className="mt-2 text-sm text-slate-600">{children}</div>}
    </div>
  );
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
      <div className="relative h-full w-full max-w-3xl overflow-y-auto bg-white shadow-xl">
        <div className="sticky top-0 flex items-center justify-between border-b bg-white px-5 py-3">
          <h3 className="font-semibold text-slate-900">{title}</h3>
          <Button kind="ghost" small onClick={onClose}>Close ✕</Button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn().then((d) => alive && (setData(d), setError(null))).catch((e) => alive && setError(e)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData };
}

export function confirmAction(message: string) {
  return window.confirm(message);
}
