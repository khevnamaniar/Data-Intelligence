import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { navigate } from "../router";
import { Button, ErrorBox, Field, inputCls, Panel, Spinner, Tag, useAsync } from "../ui";

export function AccountSettingsPage() {
  const { user, setUser } = useAuth();
  const sys = useAsync(() => api.get<any>("/api/system"), []);
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [del, setDel] = useState({ email: "", password: "" });
  return (
    <div className="max-w-3xl space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      <Panel title="Account">
        <p className="text-sm text-slate-700">Signed in as <b>{user?.email}</b></p>
      </Panel>
      <Panel title="Platform capabilities">
        {sys.loading ? <Spinner /> : sys.data && (
          <ul className="space-y-2 text-sm">
            <li><b>AI provider:</b> {sys.data.ai.enabled ? <Tag tone="green">{sys.data.ai.provider}</Tag> : <Tag>Not configured</Tag>} <span className="text-slate-500">— {sys.data.ai.note} Uploaded files are never sent to an AI service.</span></li>
            <li><b>OCR (reading text in images/scans):</b> {sys.data.ocr.tesseract_installed && sys.data.ocr.enabled ? <Tag tone="green">Available</Tag> : <Tag tone="amber">Not available</Tag>}</li>
            <li><b>File storage:</b> {sys.data.storage.provider}</li>
            <li><b>Database:</b> {sys.data.database.dialect}</li>
            <li><b>Upload limits:</b> {sys.data.limits.max_upload_mb} MB per file, {sys.data.limits.max_files_per_upload} files per upload</li>
          </ul>
        )}
      </Panel>
      <Panel title="Change password">
        <form className="space-y-3" onSubmit={async (e) => {
          e.preventDefault(); setErr(null); setMsg("");
          try { await api.post("/api/auth/change-password", pw); setMsg("Password changed."); setPw({ current_password: "", new_password: "" }); } catch (x) { setErr(x); }
        }}>
          <Field label="Current password"><input type="password" className={inputCls} value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
          <Field label="New password"><input type="password" className={inputCls} value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
          <ErrorBox error={err} />
          {msg && <p className="text-sm text-emerald-700">{msg}</p>}
          <Button type="submit">Change password</Button>
        </form>
      </Panel>
      <Panel title="Delete account">
        <p className="mb-3 text-sm text-slate-600">Permanently deletes your account, all your projects and every uploaded file.</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <input className={inputCls} placeholder="Your e-mail" value={del.email} onChange={(e) => setDel({ ...del, email: e.target.value })} />
          <input className={inputCls} type="password" placeholder="Your password" value={del.password} onChange={(e) => setDel({ ...del, password: e.target.value })} />
        </div>
        <div className="mt-3"><Button kind="danger" onClick={async () => {
          try { await api.post("/api/auth/delete-account", del); setUser(null); navigate("/login"); } catch (x) { setErr(x); }
        }}>Delete my account</Button></div>
      </Panel>
    </div>
  );
}
