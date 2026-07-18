import { afterEach, describe, expect, it, vi } from "vitest";

import { api, setManagementProfile } from "./api";

const SESSION_HEADER = "X-Hermes-Session-Token";

afterEach(() => {
  setManagementProfile("");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonFetchMock(body: unknown = { ok: true }) {
  return vi.fn<typeof fetch>(
    async () =>
      new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
  );
}

function stubFetchJson(body: unknown) {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify(body), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("api.getSessions", () => {
  it("keeps the default URL backward compatible without source", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = stubFetchJson({ sessions: [], total: 0 });

    await api.getSessions();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/sessions?limit=20&offset=0&order=created",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("preserves positional profile and order arguments", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = stubFetchJson({ sessions: [], total: 0 });

    await api.getSessions(30, 5, "coder", "recent");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/sessions?limit=30&offset=5&order=recent&profile=coder",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("adds source when a concrete source is selected", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = stubFetchJson({ sessions: [], total: 0 });

    await api.getSessions(20, 40, { source: "telegram" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/sessions?limit=20&offset=40&order=created&source=telegram",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("omits source for all while preserving query params", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = stubFetchJson({ sessions: [], total: 0 });

    await api.getSessions(10, 20, {
      order: "recent",
      profile: "ops",
      source: "all",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/sessions?limit=10&offset=20&order=recent&profile=ops",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});

describe("api.getModelOptions", () => {
  it("requests a live model refresh when asked", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = jsonFetchMock({ providers: [] });
    vi.stubGlobal("fetch", fetchMock);

    await api.getModelOptions({ refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model/options?refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("keeps explicit profile scoping when refreshing", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = jsonFetchMock({ providers: [] });
    vi.stubGlobal("fetch", fetchMock);

    await api.getModelOptions({ profile: "default", refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model/options?profile=default&refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});

describe("api OAuth helpers", () => {
  it("starts OAuth login in gated mode without requiring an injected session token", async () => {
    vi.stubGlobal("window", { __HERMES_AUTH_REQUIRED__: true });
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.startOAuthLogin("openai-codex");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/providers/oauth/openai-codex/start",
      expect.objectContaining({
        body: "{}",
        credentials: "include",
        method: "POST",
      }),
    );
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.has(SESSION_HEADER)).toBe(false);
  });

  it("still sends the injected session token for OAuth login in loopback mode", async () => {
    vi.stubGlobal("window", { __HERMES_SESSION_TOKEN__: "loopback-token" });
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.startOAuthLogin("openai-codex");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get(SESSION_HEADER)).toBe("loopback-token");
  });

  it("runs provider auth mutations in gated mode via cookie auth", async () => {
    vi.stubGlobal("window", { __HERMES_AUTH_REQUIRED__: true });
    const fetchMock = jsonFetchMock({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    await api.disconnectOAuthProvider("anthropic");
    await api.submitOAuthCode("anthropic", "oauth-session", "code-123");
    await api.cancelOAuthSession("oauth-session");
    await api.revealEnvVar("OPENAI_API_KEY");

    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit;
      expect(init.credentials).toBe("include");
      expect((init.headers as Headers).has(SESSION_HEADER)).toBe(false);
    }
  });
});
