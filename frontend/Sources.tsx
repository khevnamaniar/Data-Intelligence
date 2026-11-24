import { useRef, useState } from "react";
import { api, ApiError, FileRow, fmtBytes, fmtDate } from "../api";
import { Button, Drawer, ErrorBox, inputCls, Panel, Spinner, Tag, useAsync } from "../ui";
import type { ProjectDetail } from "./Project";
import { ErrorList, SourceSummary } from "./Project";

const ROLES: [string, string][] = [
  ["auto", "Detect automatically"], ["report", "Report / document"], ["dataset", "Dataset"], ["sales_data", "Sales data"],
  ["marketing_data", "Marketing data"], ["competitor_data", "Competitor data"], ["product_data", "Product data"],
  ["customer_data", "Customer data"], ["survey", "Survey"], ["previous_strategy", "Previous strategy"],
  ["current_strategy", "Current strategy"], ["proposed_strategy", "Proposed strategy"], ["plan", "Plan"], ["other", "Other"],
];
const STATUS_TONE: Record<string, "green" | "amber" | "red" | "slate" | "blue"> = { processed: "green", partial: "amber", failed: "red", uploaded: "slate", processing: "blue" };
const ACCEPT = ".pdf,.xlsx,.csv,.tsv,.docx,.pptx,.txt,.md,.markdown,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff";

export function SourcesPage({ d, reload }: { d: ProjectDetail; reload: () => void }) {
  const pid = d.project.id;
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [rejected, setRejected] = useState<any[]>([]);
  const [detail, setDetail] = useState<FileRow | null>(null);
  const [url, setUrl] = useState("");
  const [fetchUrl, setFetchUrl] = useState(false);

  const upload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    const form = new FormData();
    list.forEach((f) => form.append("files", f, f.name));
    setBusy(true); setError(null); setRejected([]);
    try {
      const r = await api.upload<{ accepted: any[]; rejected: any[] }>(`/api/projects/${pid}/files`, form);
      setRejected(r.rejected);
    } catch (e) {
      setError(e);
      if (e instanceof ApiError && (e.info as any).rejected) setRejected((e.info as any).rejected);
    } finally { setBusy(false); reload(); }
  };

  return (
    <div className="space-y-5">
      <Panel title="Upload sources">
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
          className={`rounded-lg border-2 border-dashed p-8 text-center ${drag ? "border-slate-600 bg-slate-50" : "border-slate-300"}`}>
          <p className="font-medium text-slate-800">Drop files here, or</p>
          <div className="mt-2"><Button onClick={() => input.current?.click()} disabled={busy}>{busy ? "Uploading…" : "Choose files"}</Button></div>
          <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" data-testid="file-input" onChange={(e) => e.target.files && upload(e.target.files)} />
          <p className="mt-3 text-xs text-slate-500">PDF, Excel (.xlsx), CSV, Word (.docx), PowerPoint (.pptx), TXT, Markdown, images & screenshots. Several files at once is fine.
            Files are stored privately and are never sent to external services.</p>
        </div>
        <div className="mt-3 space-y-2">
          <ErrorBox error={error} onClose={() => setError(null)} />
          {rejected.map((r, i) => <ErrorBox key={i} error={new ApiError(0, { what: `${r.name}: ${r.what}`, why: r.why, next: r.next })} />)}
        </div>
        <form className="mt-4 flex flex-wrap items-center gap-2" onSubmit={async (e) => {
          e.preventDefault();
          try { await api.post(`/api/projects/${pid}/urls`, { url, fetch: fetchUrl }); setUrl(""); reload(); } catch (x) { setError(x); }
        }}>
          <input className={inputCls + " max-w-md"} placeholder="Add a web page or reference URL (https://…)" value={url} onChange={(e) => setUrl(e.target.value)} />
          <label className="flex items-center gap-1 text-xs text-slate-600"><input type="checkbox" checked={fetchUrl} onChange={(e) => setFetchUrl(e.target.checked)} /> Fetch and analyse the page (optional)</label>
          <Button kind="secondary" type="submit" disabled={!url}>Add URL</Button>
        </form>
        {d.urls.length > 0 && (
          <ul className="mt-2 space-y-1 text-sm">{d.urls.map((u) => (
            <li key={u.id} className="flex items-center gap-2"><a href={u.url} target="_blank" rel="noreferrer noopener" className="text-blue-700 underline">{u.title || u.url}</a>
              <Tag>{u.fetch_status.replace(/_/g, " ")}</Tag>
              <button className="text-xs text-slate-500 underline" onClick={async () => { await api.del(`/api/projects/${pid}/urls/${u.id}`); reload(); }}>remove</button></li>
          ))}</ul>
        )}
      </Panel>

      <SourceSummary d={d} />
      <Panel title={`Files (${d.files.length})`}>
        {d.files.length === 0 ? <p className="text-sm text-slate-600">No files yet.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500"><tr>
                <th className="py-2 pr-3 font-medium">File</th><th className="py-2 pr-3 font-medium">Status</th><th className="py-2 pr-3 font-medium">Extracted</th>
                <th className="py-2 pr-3 font-medium">Role</th><th className="py-2 pr-3 font-medium">Period label</th><th className="py-2 font-medium"></th></tr></thead>
              <tbody>
                {d.files.map((f) => (
                  <tr key={f.id} className="border-t border-slate-100 align-top" data-testid="file-row">
                    <td className="py-2 pr-3"><span className="font-medium text-slate-900">{f.original_name}</span><div className="text-xs text-slate-500">{f.file_type.toUpperCase()} · {fmtBytes(f.size_bytes)} · {fmtDate(f.created_at)}</div>
                      {f.error_json && f.error_json.length > 0 && <div className="mt-1 text-xs text-red-700">{f.error_json[0].what} {f.error_json[0].why}</div>}</td>
                    <td className="py-2 pr-3">{f.status === "processing" || f.status === "uploaded" ? <Spinner label={f.status} /> : <Tag tone={STATUS_TONE[f.status]}>{f.status}</Tag>}</td>
                    <td className="py-2 pr-3 text-xs text-slate-600">{f.extraction_json ? `${f.extraction_json.tables} table(s), ${f.extraction_json.chunks} text block(s)${f.extraction_json.quality_issues ? `, ${f.extraction_json.quality_issues} quality note(s)` : ""}` : "—"}</td>
                    <td className="py-2 pr-3">
                      <select aria-label="File role" className="rounded border border-slate-300 bg-white px-1.5 py-1 text-xs" value={f.source_role}
                        onChange={async (e) => { await api.patch(`/api/projects/${pid}/files/${f.id}`, { source_role: e.target.value }); reload(); }}>
                        {ROLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                    </td>
                    <td className="py-2 pr-3"><input aria-label="Period label" className="w-24 rounded border border-slate-300 px-1.5 py-1 text-xs" defaultValue={f.period_label} placeholder="e.g. 2024"
                      onBlur={async (e) => { if (e.target.value !== f.period_label) { await api.patch(`/api/projects/${pid}/files/${f.id}`, { period_label: e.target.value }); reload(); } }} /></td>
                    <td className="py-2 whitespace-nowrap">
                      <Button small kind="ghost" onClick={() => setDetail(f)}>Details</Button>
                      <a className="px-2 text-xs text-slate-600 underline" href={`/api/projects/${pid}/files/${f.id}/download`}>Download</a>
                      <Button small kind="ghost" onClick={async () => { await api.post(`/api/projects/${pid}/files/${f.id}/reprocess`); reload(); }}>Re-process</Button>
                      <Button small kind="danger" onClick={async () => {
                        if (!window.confirm(`Delete ${f.original_name}? It is removed from storage permanently.`)) return;
                        await api.del(`/api/projects/${pid}/files/${f.id}`); reload();
                      }}>Delete</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <QualityReport pid={pid} key={d.files.map((f) => f.status).join()} />
      {detail && <FileDetail pid={pid} file={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

function QualityReport({ pid }: { pid: string }) {
  const { data, loading } = useAsync(() => api.get<{ issues: any[] }>(`/api/projects/${pid}/quality`), [pid]);
  if (loading) return null;
  if (!data || !data.issues.length) return null;
  return (
    <Panel title={`Data Quality Report (${data.issues.length})`}>
      <p className="mb-2 text-xs text-slate-500">Original files are never changed. Fixes such as harmonised spellings apply only to the analysis copy and are recorded in each file's details.</p>
      <div className="max-h-[28rem] overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-white text-left text-slate-500"><tr><th className="py-1.5 pr-3 font-medium">Issue</th><th className="pr-3 font-medium">Source</th><th className="pr-3 font-medium">Field</th><th className="pr-3 font-medium">Severity</th><th className="font-medium">Suggested resolution</th></tr></thead>
          <tbody>{data.issues.map((i) => (
            <tr key={i.id} className="border-t border-slate-100 align-top">
              <td className="py-1.5 pr-3">{i.description}{i.locator_json?.rows && <div className="text-xs text-slate-500">Rows: {i.locator_json.rows}</div>}</td>
              <td className="pr-3 text-xs">{i.source}{i.locator_json?.sheet ? ` › ${i.locator_json.sheet}` : ""}{i.locator_json?.page ? ` › p.${i.locator_json.page}` : ""}</td>
              <td className="pr-3 text-xs">{i.field}</td>
              <td className="pr-3"><Tag tone={i.severity === "high" ? "red" : i.severity === "medium" ? "amber" : "slate"}>{i.severity}</Tag></td>
              <td className="text-xs text-slate-700">{i.suggested_resolution}</td>
            </tr>))}</tbody>
        </table>
      </div>
    </Panel>
  );
}

function FileDetail({ pid, file, onClose }: { pid: string; file: FileRow; onClose: () => void }) {
  const { data, error, loading } = useAsync(() => api.get<any>(`/api/projects/${pid}/files/${file.id}`), [file.id]);
  const [preview, setPreview] = useState<string | null>(null);
  return (
    <Drawer open onClose={onClose} title={file.original_name}>
      {loading ? <Spinner /> : error ? <ErrorBox error={error} /> : (
        <div className="space-y-5 text-sm">
          <ErrorList errors={data.file.error_json} />
          {data.file.extraction_json?.warnings?.length > 0 && <ul className="list-disc pl-5 text-amber-800">{data.file.extraction_json.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>}
          {data.datasets.map((ds: any) => (
            <div key={ds.id} className="rounded border border-slate-200 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="font-medium">{ds.name} <span className="text-xs text-slate-500">({ds.row_count} rows × {ds.column_count} columns)</span> {!!ds.is_approximate && <Tag tone="amber">Approximate / visually interpreted</Tag>}</p>
                <Button small kind="secondary" onClick={() => setPreview(preview === ds.id ? null : ds.id)}>{preview === ds.id ? "Hide rows" : "Preview rows"}</Button>
              </div>
              <table className="mt-2 w-full text-xs">
                <thead className="text-left text-slate-500"><tr><th className="pr-2 font-medium">Column</th><th className="pr-2 font-medium">Type</th><th className="pr-2 font-medium">Detected meaning</th><th className="pr-2 font-medium">Unit</th><th className="font-medium">Missing</th></tr></thead>
                <tbody>{ds.columns.map((c: any) => <tr key={c.position} className="border-t border-slate-100"><td className="pr-2">{c.normalized_name}</td><td className="pr-2">{c.data_type}</td><td className="pr-2">{c.semantic_role}</td><td className="pr-2">{c.unit}</td><td>{c.missing_count}</td></tr>)}</tbody>
              </table>
              {ds.transformations_json?.length > 0 && (
                <details className="mt-2 text-xs"><summary className="cursor-pointer text-slate-600">Recorded transformations ({ds.transformations_json.length})</summary>
                  <ul className="mt-1 list-disc pl-5">{ds.transformations_json.map((t: any, i: number) => <li key={i}><b>{t.field}</b>: {t.action} — {t.detail}</li>)}</ul></details>
              )}
              {preview === ds.id && <Preview pid={pid} did={ds.id} />}
            </div>
          ))}
          {data.chunks.length > 0 && (
            <div>
              <p className="mb-1 font-medium">Extracted text ({data.chunks.length} blocks shown)</p>
              <div className="max-h-96 space-y-1 overflow-auto rounded border border-slate-200 p-2">
                {data.chunks.map((c: any) => (
                  <p key={c.id} className={c.kind === "heading" || c.kind === "slide_title" ? "font-semibold" : "text-slate-700"}>
                    <span className="mr-1 text-[10px] uppercase text-slate-400">{c.kind}{c.locator_json?.page ? ` p.${c.locator_json.page}` : ""}{c.locator_json?.slide ? ` slide ${c.locator_json.slide}` : ""}</span>
                    {c.text}{!!c.is_approximate && <span className="ml-1 text-xs text-amber-700">(OCR, approximate)</span>}
                  </p>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}

function Preview({ pid, did }: { pid: string; did: string }) {
  const { data, loading } = useAsync(() => api.get<any>(`/api/projects/${pid}/datasets/${did}/preview?limit=30`), [did]);
  if (loading || !data) return <Spinner />;
  return (
    <div className="mt-2 max-h-72 overflow-auto rounded border">
      <table className="w-full text-xs"><thead className="sticky top-0 bg-slate-50"><tr>{data.columns.map((c: string) => <th key={c} className="px-2 py-1 text-left font-medium">{c === "__row__" ? "Row" : c}</th>)}</tr></thead>
        <tbody>{data.rows.map((r: any, i: number) => <tr key={i} className="border-t border-slate-100">{data.columns.map((c: string) => <td key={c} className="px-2 py-0.5">{r[c] === null ? "" : String(r[c])}</td>)}</tr>)}</tbody></table>
      <p className="p-1 text-xs text-slate-500">Showing {data.rows.length} of {data.total} rows (normalised copy).</p>
    </div>
  );
}
