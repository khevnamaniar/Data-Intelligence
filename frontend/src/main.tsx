import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { api, User } from "./api";
import { match, navigate, useRoute, Link } from "./router";
import { Spinner } from "./ui";
import { ForgotPage, LoginPage, RegisterPage, ResetPage } from "./pages/Auth";
import { DashboardPage, NewProjectPage } from "./pages/Dashboard";
import { ProjectShell } from "./pages/Project";
import { AccountSettingsPage } from "./pages/AccountSettings";
import { AdminPage } from "./pages/Admin";

import { Auth, useAuth } from "./auth";

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const route = useRoute();
  useEffect(() => {
    api.get<{ user: User | null }>("/api/auth/me").then((r) => setUser(r.user)).catch(() => setUser(null)).finally(() => setReady(true));
  }, []);
  const logout = async () => {
    await api.post("/api/auth/logout");
    setUser(null);
    navigate("/login");
  };
  if (!ready) return <div className="p-10"><Spinner label="Loading…" /></div>;
  const publicPages: Record<string, React.ReactNode> = {
    "/login": <LoginPage />, "/register": <RegisterPage />, "/forgot": <ForgotPage />, "/reset-password": <ResetPage token={route.query.get("token") || ""} />,
  };
  let page: React.ReactNode;
  if (publicPages[route.path]) page = publicPages[route.path];
  else if (!user) {
    if (route.path !== "/login") setTimeout(() => navigate("/login"), 0);
    page = <LoginPage />;
  } else {
    let m: Record<string, string> | null;
    if (route.path === "/" || route.path === "/projects") page = <DashboardPage />;
    else if (route.path === "/projects/new") page = <NewProjectPage />;
    else if (route.path === "/settings") page = <AccountSettingsPage />;
    else if (route.path === "/admin" && user.is_admin) page = <AdminPage />;
    else if ((m = match("/p/:id", route.path)) || (m = match("/p/:id/:tab", route.path)) || (m = match("/p/:id/:tab/:sub", route.path)))
      page = <ProjectShell id={m.id} tab={m.tab || "overview"} sub={m.sub} query={route.query} />;
    else page = <div className="p-10">Page not found. <Link to="/" className="underline">Go to dashboard</Link></div>;
  }
  return (
    <Auth.Provider value={{ user, setUser, logout }}>
      {user && !publicPages[route.path] ? <Shell>{page}</Shell> : page}
    </Auth.Provider>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
          <Link to="/" className="font-semibold tracking-tight text-slate-900">◆ Data Intelligence</Link>
          <nav className="flex gap-4 text-sm text-slate-600">
            <Link to="/" className="hover:text-slate-900">Projects</Link>
            <Link to="/settings" className="hover:text-slate-900">Settings</Link>
            {user?.is_admin && <Link to="/admin" className="hover:text-slate-900">Admin</Link>}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm text-slate-600">
            <span>{user?.email}</span>
            <button onClick={logout} className="underline hover:text-slate-900">Sign out</button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-6">{children}</main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
