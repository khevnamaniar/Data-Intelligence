import { useState } from "react";
import { api, fmtDate } from "../api";
import { Button, ErrorBox, Panel, Spinner, Tag, useAsync } from "../ui";

// Owner/admin view: health, usage vs free-tier limits, recent errors, live self-test, user help, backup.
// Shows counts and sizes only — never the contents of anyone's projects.

function Meter({ label, l }: { label: string; l: any }) {
  const pct = l.percent ?? 0;
  const tone = l.level === "critical" ? "bg-red-600" : l.level === "warning" ? "bg-amber-500" : "bg-emerald-600";
  return (
    <div>
      <div className="flex justify-between text-sm"><span className="font-medium">{label}</span>
        <span className="text-slate-600">{l.used_mb ?? "?"} MB{l.limit_mb ? ` of ${l.limit_mb} MB (${pct}%)` : " (no limit set)"}</span></div>
      <div className="mt-1 h-2 rounded bg-slate-100"><div className={`h-2 rounded ${tone}`} style={{ width: `${Math.min(100, pct)}%` }} /></div>
      {l.level === "warning" && <p className="mt-1 text-xs text-amber-800">⚠ Over 75% used — consider deleting old projects.</p>}
      {l.level === "critical" && <p className="mt-1 text-xs text-red-800">⛔ Over 90% used — uploads or saving may soon be refused.</p>}
    </div>
  );
}

export function AdminPage() {
  const ov = useAsync(() => api.get<any>("/api/admin/overview"), []);
  const users = useAsync(() => api.get<{ users: any[] }>("/api/admin/users"), []);
  const [test, setTest] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [link, setLink] = useState<any>(null);
  const runTest = async () => {
    setTesting(true); setErr(null);
    try { setTest(await api.post("/api/admin/selftest")); } catch (e) { setErr(e); } finally { setTesting(false); }
  };
  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between">
        <div><h1 className="text-2xl font-semibold tracking-tight">Admin</h1>
          <p className="text-sm text-slate-600">System health and usage. Project contents are not shown here.</p></div>
        <Button kind="secondary" onClick={() => { ov.reload(); users.reload(); }}>Refresh</Button>
      </div>
      <ErrorBox error={err || ov.error} />
      {ov.loading && !ov.data ? <Spinner /> : ov.data && (
        <>
          <div className="grid gap-5 lg:grid-cols-2">
            <Panel title="System health">
              <ul className="space-y-1.5 text-sm">
                <li>Application: <Tag tone="green">running</Tag> <span className="text-slate-500">({ov.data.health.environment})</span></li>
                <li>Database: <Tag tone="green">connected</Tag> <span className="text-slate-500">{ov.data.health.database_response_ms} ms</span></li>
                <li>File storage ({ov.data.health.storage_provider}): {ov.data.health.storage === "connected" ? <Tag tone="green">connected</Tag> : <Tag tone="red">error</Tag>}
                  {ov.data.health.storage_note && <span className="ml-1 text-xs text-red-700">{ov.data.health.storage_note}</span>}</li>
              </ul>
              <div className="mt-3"><Button onClick={runTest} disabled={testing}>{testing ? "Running self-test…" : "Run self-test"}</Button></div>
              {test && (
                <ul className="mt-3 space-y-1 text-sm" data-testid="selftest">
                  {test.checks.map((c: any) => (
                    <li key={c.check}><span className={c.ok ? "text-emerald-700" : "text-red-700"}>{c.ok ? "✔" : "✖"}</span> <b>{c.check}</b> — {c.detail} <span className="text-xs text-slate-400">{c.ms} ms</span></li>
                  ))}
                  <li className="pt-1 font-medium">{test.all_ok ? "All checks passed." : "Some checks failed — see docs/DEPLOYMENT_GUIDE.md → Troubleshooting."}</li>
                </ul>
              )}
            </Panel>
            <Panel title="Free-tier usage">
              <div className="space-y-4">
                <Meter label="Database storage" l={ov.data.limits.database} />
                <Meter label="File storage" l={ov.data.limits.file_storage} />
              </div>
              <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-slate-500">{ov.data.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
            </Panel>
          </div>
          <Panel title="Usage">
            <div className="grid gap-3 text-sm sm:grid-cols-4">
              {Object.entries(ov.data.usage.counts).map(([k, v]) => <div key={k}><p className="text-slate-500">{k.replace("_", " ")}</p><p className="text-xl font-semibold">{String(v)}</p></div>)}
            </div>
            <div className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
              <div><p className="text-slate-500">Files by status</p><p>{Object.entries(ov.data.usage.files_by_status).map(([k, v]) => `${k}: ${v}`).join(" · ") || "—"}</p></div>
              <div><p className="text-slate-500">Analyses by status</p><p>{Object.entries(ov.data.usage.runs_by_status).map(([k, v]) => `${k}: ${v}`).join(" · ") || "—"}</p></div>
              <div><p className="text-slate-500">Last 24 hours</p><p>{Object.entries(ov.data.usage.activity_24h).map(([k, v]) => `${k.replace("_", " ")}: ${v}`).join(" · ")}</p></div>
            </div>
          </Panel>
          <Panel title="Recent errors">
            {ov.data.recent_errors.length === 0 ? <p className="text-sm text-slate-600">No recent failures.</p> : (
              <table className="w-full text-sm"><thead className="text-left text-slate-500"><tr><th className="py-1 pr-3 font-medium">When</th><th className="pr-3 font-medium">What</th><th className="pr-3 font-medium">Project</th><th className="font-medium">Message</th></tr></thead>
                <tbody>{ov.data.recent_errors.map((e: any, i: number) => <tr key={i} className="border-t border-slate-100"><td className="py-1 pr-3 text-xs">{fmtDate(e.when)}</td><td className="pr-3">{e.kind}</td><td className="pr-3 font-mono text-xs">{e.project}…</td><td>{e.what}</td></tr>)}</tbody></table>
            )}
          </Panel>
        </>
      )}
      <Panel title="Users">
        {users.loading ? <Spinner /> : (
          <table className="w-full text-sm"><thead className="text-left text-slate-500"><tr><th className="py-1 pr-3 font-medium">E-mail</th><th className="pr-3 font-medium">Joined</th><th className="pr-3 font-medium">Last sign-in</th><th className="pr-3 font-medium">Projects</th><th /></tr></thead>
            <tbody>{(users.data?.users || []).map((u) => (
              <tr key={u.id} className="border-t border-slate-100"><td className="py-1.5 pr-3">{u.email}</td><td className="pr-3 text-xs">{fmtDate(u.created_at)}</td><td className="pr-3 text-xs">{fmtDate(u.last_login_at)}</td><td className="pr-3">{u.projects}</td>
                <td><Button small kind="secondary" onClick={async () => { try { setLink(await api.post(`/api/admin/users/${u.id}/reset-link`)); } catch (e) { setErr(e); } }}>Create reset link</Button></td></tr>))}</tbody></table>
        )}
        {link && (
          <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm">
            <p><b>Password-reset link for {link.email}</b> (works once, expires in {link.expires_in_hours} h). Send it privately — anyone with it can set this user's password.</p>
            <input readOnly className="mt-2 w-full rounded border px-2 py-1 font-mono text-xs" value={link.link} onFocus={(e) => e.currentTarget.select()} />
          </div>
        )}
      </Panel>
      <Panel title="Backup">
        <p className="text-sm text-slate-600">Download all database contents as CSV files in a ZIP. Password hashes and login tokens are excluded; uploaded files stay in cloud storage. Keep the download confidential.</p>
        <a className="mt-2 inline-block rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium hover:bg-slate-50" href="/api/admin/backup">Download database backup</a>
      </Panel>
    </div>
  );
}
