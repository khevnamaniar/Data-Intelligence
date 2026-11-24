import { useState } from "react";
import { api, User } from "../api";
import { useAuth } from "../auth";
import { Link, navigate } from "../router";
import { Button, ErrorBox, Field, inputCls } from "../ui";

function Card({ title, children, footer }: { title: string; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <p className="mb-6 text-center text-lg font-semibold tracking-tight">◆ Data Intelligence Platform</p>
        <div className="rounded-lg border border-slate-200 bg-white p-6">
          <h1 className="mb-4 text-xl font-semibold">{title}</h1>
          {children}
        </div>
        {footer && <div className="mt-4 text-center text-sm text-slate-600">{footer}</div>}
      </div>
    </div>
  );
}

export function LoginPage() {
  const { setUser } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api.post<{ user: User }>("/api/auth/login", { email, password });
      setUser(r.user);
      navigate("/");
    } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return (
    <Card title="Sign in" footer={<>No account? <Link to="/register" className="underline">Create one</Link></>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="E-mail"><input className={inputCls} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" /></Field>
        <Field label="Password"><input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" /></Field>
        <ErrorBox error={error} />
        <Button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
        <p className="text-sm"><Link to="/forgot" className="text-slate-600 underline">Forgot your password?</Link></p>
      </form>
    </Card>
  );
}

export function RegisterPage() {
  const { setUser } = useAuth();
  const [form, setForm] = useState({ display_name: "", email: "", password: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api.post<{ user: User }>("/api/auth/register", form);
      setUser(r.user);
      navigate("/");
    } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return (
    <Card title="Create your account" footer={<>Already registered? <Link to="/login" className="underline">Sign in</Link></>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Your name"><input className={inputCls} value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
        <Field label="E-mail"><input className={inputCls} type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
        <Field label="Password" hint="At least 10 characters, with upper- and lower-case letters and a number.">
          <input className={inputCls} type="password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" />
        </Field>
        <ErrorBox error={error} />
        <Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create account"}</Button>
      </form>
    </Card>
  );
}

export function ForgotPage() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState("");
  const [error, setError] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const r = await api.post<{ message: string }>("/api/auth/password-reset/request", { email });
      setDone(r.message);
    } catch (err) { setError(err); }
  };
  return (
    <Card title="Reset your password" footer={<Link to="/login" className="underline">Back to sign in</Link>}>
      {done ? (
        <p className="text-sm text-slate-700">{done} In local development the link is printed in the backend terminal window instead of being e-mailed.</p>
      ) : (
        <form onSubmit={submit} className="space-y-3">
          <Field label="E-mail"><input className={inputCls} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <ErrorBox error={error} />
          <Button type="submit">Send reset link</Button>
        </form>
      )}
    </Card>
  );
}

export function ResetPage({ token }: { token: string }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post("/api/auth/password-reset/confirm", { token, password });
      setDone(true);
    } catch (err) { setError(err); }
  };
  return (
    <Card title="Choose a new password">
      {done ? <p className="text-sm">Password changed. <Link to="/login" className="underline">Sign in</Link></p> : (
        <form onSubmit={submit} className="space-y-3">
          <Field label="New password"><input className={inputCls} type="password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <ErrorBox error={error} />
          <Button type="submit">Save new password</Button>
        </form>
      )}
    </Card>
  );
}
