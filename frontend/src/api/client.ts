/** Dashboard API client: automatically attaches the Cognito id token and handles date-range parameters. */
import { getConfig } from "../config";
import { getIdToken, getAccessToken } from "../auth/auth";
import type {
  DateRange,
  DepartmentsResponse,
  FiltersResponse,
  ModelConcentrationResponse,
  ModelMultiplier,
  ModelMultipliersResponse,
  ModelsResponse,
  ReportResponse,
  SummaryResponse,
  TopUsersResponse,
  TrendResponse,
  UsersResponse,
} from "./types";

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function get<T>(path: string): Promise<T> {
  const { apiEndpoint } = getConfig();
  const token = getIdToken();
  const resp = await fetch(`${apiEndpoint}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (resp.status === 401 || resp.status === 403) {
    throw new ApiAuthError(resp.status);
  }
  if (!resp.ok) {
    throw new Error(`API ${path} failed: ${resp.status} ${await resp.text()}`);
  }
  return (await resp.json()) as T;
}

async function mutate<T>(method: "POST" | "PUT", path: string, body?: unknown): Promise<T> {
  const { apiEndpoint } = getConfig();
  const token = getIdToken();
  const resp = await fetch(`${apiEndpoint}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401 || resp.status === 403) throw new ApiAuthError(resp.status);
  if (!resp.ok) {
    let detail = await resp.text();
    try {
      detail = JSON.parse(detail).detail ?? detail;
    } catch {
      /* keep text */
    }
    throw new Error(detail || `${method} ${path} failed: ${resp.status}`);
  }
  return (await resp.json()) as T;
}

export class ApiAuthError extends Error {
  constructor(public status: number) {
    super(`Unauthorized (${status})`);
  }
}

function rangeParams(r: DateRange): Record<string, string | undefined> {
  return { period: r.period, from: r.from, to: r.to };
}

export const api = {
  summary: (r: DateRange) => get<SummaryResponse>(`/api/summary${qs(rangeParams(r))}`),
  trend: (r: DateRange) => get<TrendResponse>(`/api/trend${qs(rangeParams(r))}`),
  models: (r: DateRange, email?: string) =>
    get<ModelsResponse>(`/api/models${qs({ ...rangeParams(r), email })}`),
  departments: (r: DateRange) =>
    get<DepartmentsResponse>(`/api/departments${qs(rangeParams(r))}`),
  modelConcentration: (r: DateRange, models: string[], threshold: number) =>
    get<ModelConcentrationResponse>(
      `/api/model-concentration${qs({ ...rangeParams(r), models: models.join(","), threshold })}`
    ),
  topUsers: (r: DateRange, limit?: number) =>
    get<TopUsersResponse>(`/api/top-users${qs({ ...rangeParams(r), limit })}`),
  users: (
    r: DateRange,
    opts: { department?: string; search?: string; sort?: string; direction?: string; page?: number; page_size?: number }
  ) => get<UsersResponse>(`/api/users${qs({ ...rangeParams(r), ...opts })}`),
  // CSV export: needs the Authorization header, so use fetch+blob to trigger the download (not <a href>).
  // Scope matches the current department tab + search; includes zero-usage users, unpaginated.
  usersExportCsv: async (
    r: DateRange,
    opts: { department?: string; search?: string; sort?: string; direction?: string }
  ): Promise<void> => {
    const { apiEndpoint } = getConfig();
    const token = getIdToken();
    const path = `/api/users/export${qs({ ...rangeParams(r), ...opts })}`;
    const resp = await fetch(`${apiEndpoint}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (resp.status === 401 || resp.status === 403) throw new ApiAuthError(resp.status);
    if (!resp.ok) throw new Error(`Export failed: ${resp.status} ${await resp.text()}`);
    // Parse the filename from Content-Disposition (prefer filename*=UTF-8''), falling back to a default name.
    const cd = resp.headers.get("Content-Disposition") || "";
    let filename = `kiro-users-${new Date().toISOString().slice(0, 10).replace(/-/g, "")}.csv`;
    const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
    const plain = /filename="?([^";]+)"?/i.exec(cd);
    if (star) filename = decodeURIComponent(star[1]);
    else if (plain) filename = plain[1];
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
  filters: () => get<FiltersResponse>(`/api/filters`),
  report: (r: DateRange) => get<ReportResponse>(`/api/report${qs(rangeParams(r))}`),

  // Model multiplier maintenance
  modelMultipliers: () => get<ModelMultipliersResponse>(`/api/model-multipliers`),
  updateModelMultipliers: (items: Pick<ModelMultiplier, "model" | "multiplier">[]) =>
    mutate<{ ok: boolean; updated: number }>("PUT", `/api/model-multipliers`, { items }),
  chat: async (prompt: string, sessionId?: string): Promise<{ reply: string; session_id: string }> => {
    const { apiEndpoint } = getConfig();
    const resp = await fetch(`${apiEndpoint}/api/chat`, {
      method: "POST",
      headers: {
        // header uses the id token to pass API Gateway; body passes the access token to AgentCore
        Authorization: `Bearer ${getIdToken() ?? ""}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ prompt, session_id: sessionId, access_token: getAccessToken() }),
    });
    if (resp.status === 401 || resp.status === 403) throw new ApiAuthError(resp.status);
    if (!resp.ok) throw new Error(`Chat failed: ${resp.status} ${await resp.text()}`);
    return (await resp.json()) as { reply: string; session_id: string };
  },
};
