// Thin API client. All requests are same-origin and carry the session cookie.
// State-changing requests send the X-Requested-With header the server requires (CSRF protection).

export type ErrorInfo = { what: string; why?: string; next?: string; can_still?: string; code?: string };

export class ApiError extends Error {
  info: ErrorInfo;
  status: number;
  constructor(status: number, info: ErrorInfo) {
    super(info.what);
    this.status = status;
    this.info = info;
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function request<T>(method: string, path: string, body?: unknown, isForm = false, attempt = 0): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "dip" };
  let payload: BodyInit | undefined;
  if (body !== undefined) {
    if (isForm) payload = body as FormData;
    else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  } catch {
    if (method === "GET" && attempt < 3) { await sleep(5000); return request<T>(method, path, body, isForm, attempt + 1); }
    throw new ApiError(0, { what: "Cannot reach the server.", why: "Your internet connection dropped, or the server is restarting.", next: "Check your connection and try again in a minute." });
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  // Free hosting puts the server to sleep after inactivity; while it wakes up, requests get 502/503/504
  // or an HTML "loading" page instead of JSON. Retry quietly a few times before showing a message.
  const waking = [502, 503, 504].includes(res.status) && !(data && data.error && data.error.code);
  if ((waking || (res.ok && text && data === null && path.startsWith("/api/"))) && method === "GET" && attempt < 6) {
    await sleep(5000);
    return request<T>(method, path, body, isForm, attempt + 1);
  }
  if (waking) {
    throw new ApiError(res.status, { what: "The server is waking up or busy.", why: "The free hosting plan pauses the server after 15 minutes without visitors; starting again takes about a minute.", next: "Wait a minute and try again." });
  }
  if (!res.ok) {
    const info: ErrorInfo = (data && (data.error as ErrorInfo)) || { what: `Request failed (${res.status}).` };
    if (data && Array.isArray(data.rejected)) (info as any).rejected = data.rejected;
    throw new ApiError(res.status, info);
  }
  return data as T;
}

export const api = {
  get: <T,>(p: string) => request<T>("GET", p),
  post: <T,>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  patch: <T,>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T,>(p: string) => request<T>("DELETE", p),
  upload: <T,>(p: string, form: FormData) => request<T>("POST", p, form, true),
};

// ---------------------------------------------------------------- shared types
export type User = { id: string; email: string; display_name: string; is_admin?: boolean };
export type Project = {
  id: string; name: string; description: string; industry: string; geography: string; time_period: string;
  business_objective: string; focus_entity: string; learning_objectives: string[]; role: string; is_demo: boolean;
  created_at: string; updated_at: string; file_count?: number; last_analysis?: string | null;
};
export type FileRow = {
  id: string; original_name: string; file_type: string; size_bytes: number; source_role: string; period_label: string;
  status: "uploaded" | "processing" | "processed" | "partial" | "failed"; extraction_json: any; error_json: ErrorInfo[] | null; created_at: string;
};
export type Evidence = {
  id: string; file_id: string | null; dataset_id: string | null; chunk_id: string | null; source_label: string;
  locator_json: any; excerpt: string; values_json: any; is_approximate: boolean | number;
};
export type Finding = {
  id: string; category: string; title: string; fact: string; interpretation: string; implication: string; hypothesis: string;
  validation: string; confidence: "high" | "medium" | "low"; is_approximate: boolean | number; score: number; rank: number;
  tags_json: string[]; chart_json: ChartSpec | null; details_json: any; evidence: Evidence[];
};
export type ChartSpec = {
  type: string; title?: string; question?: string; x_label?: string; y_label?: string; unit?: string; categories?: string[];
  series?: { name: string; values: (number | null)[]; style?: string }[];
  points?: { x: number; y: number; label?: string; size?: number; group?: string }[];
  rows?: string[]; cols?: string[]; values?: number[][]; note?: string; reference_line?: string; x_split?: number; y_split?: number;
};
export type RunSummary = { id: string; mode: string; status: string; progress: number; stage: string; created_at: string; completed_at: string | null; summary_json?: any; error_json?: ErrorInfo[] | null };

export function fmtBytes(n: number) {
  if (n > 1e6) return (n / 1e6).toFixed(1) + " MB";
  if (n > 1e3) return (n / 1e3).toFixed(0) + " KB";
  return n + " B";
}

export function fmtDate(s?: string | null) {
  if (!s) return "—";
  const d = new Date(s.includes("T") || s.includes("+") ? s : s.replace(" ", "T") + "Z");
  return isNaN(d.getTime()) ? s : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
