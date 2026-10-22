import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, request, setOnUnauthorized } from "./api";

function reply(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setOnUnauthorized(() => {});
});

describe("request", () => {
  it("sends the dashboard header, JSON body and same-origin credentials", async () => {
    const fetchMock = vi.fn(async () => reply(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const out = await request<{ ok: boolean }>("/api/send", { method: "POST", body: { id: "1" } });
    expect(out).toEqual({ ok: true });
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(path).toBe("/api/send");
    expect(init.method).toBe("POST");
    expect(headers.get("X-Tickett-Dashboard")).toBe("1");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init.credentials).toBe("same-origin");
    expect(init.body).toBe(JSON.stringify({ id: "1" }));
  });

  it("sends the header on GET too, without a body", async () => {
    const fetchMock = vi.fn(async () => reply(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    await request("/api/me");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("GET");
    expect(new Headers(init.headers).get("X-Tickett-Dashboard")).toBe("1");
    expect(init.body).toBeUndefined();
  });

  it("on 401 calls onUnauthorized and rejects with ApiError(401) carrying the server's message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(401, { error: "Sessão terminada." })));
    const onUnauthorized = vi.fn();
    setOnUnauthorized(onUnauthorized);
    const err = await request("/api/state").then(() => null, (e: ApiError) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err?.status).toBe(401);
    expect(err?.message).toBe("Sessão terminada.");
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("rejects other errors with the server's message and does not call onUnauthorized", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(429, { error: "Demasiadas tentativas." })));
    const onUnauthorized = vi.fn();
    setOnUnauthorized(onUnauthorized);
    const err = await request("/api/login", { method: "POST", body: {} }).then(() => null, (e: ApiError) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err?.status).toBe(429);
    expect(err?.message).toBe("Demasiadas tentativas.");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("turns a network failure into ApiError(0) with a Portuguese message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const onUnauthorized = vi.fn();
    setOnUnauthorized(onUnauthorized);
    const err = await request("/api/state").then(() => null, (e: ApiError) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err?.status).toBe(0);
    expect(err?.message).toBe("Sem ligação ao servidor. Verifique a ligação e tente outra vez.");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("falls back to a generic message when the error body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("oops", { status: 502 })));
    const err = await request("/api/state").then(() => null, (e: ApiError) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err?.status).toBe(502);
    expect(err?.message).toBe("Falhou.");
  });
});
