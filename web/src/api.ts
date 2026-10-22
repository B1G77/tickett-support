// One door to the backend (src/webapp.py): same-origin cookie session, the CSRF header on every call,
// errors carry the server's own Portuguese message.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let onUnauthorized: () => void = () => {};

/** Called on any 401 (session gone): the app shows the login page. */
export function setOnUnauthorized(fn: () => void): void {
  onUnauthorized = fn;
}

export async function request<T>(path: string, opts: { method?: "GET" | "POST"; body?: unknown } = {}): Promise<T> {
  const method = opts.method ?? (opts.body === undefined ? "GET" : "POST");
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: { "X-Tickett-Dashboard": "1", "Content-Type": "application/json" },
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch {
    // server unreachable: the browser's own text ("Failed to fetch") is English
    throw new ApiError(0, "Sem ligação ao servidor. Verifique a ligação e tente outra vez.");
  }
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    // not JSON (proxy error page, empty body): handled below
  }
  if (!res.ok) {
    const msg = data && typeof data === "object" && typeof (data as { error?: unknown }).error === "string"
      ? (data as { error: string }).error
      : "Falhou.";
    if (res.status === 401) onUnauthorized();
    throw new ApiError(res.status, msg);
  }
  return data as T;
}
