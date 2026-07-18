import { afterEach, describe, expect, it, vi } from "vitest";

import { api, setManagementProfile } from "./api";

afterEach(() => {
  setManagementProfile("");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

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

describe("api.pruneSessions", () => {
  it("keeps prune payload independent from the History source filter", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = stubFetchJson({ ok: true, removed: 2 });

    await api.pruneSessions(90);

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/sessions/prune",
      expect.objectContaining({ method: "POST" }),
    );
    expect(JSON.parse(String(init.body))).toEqual({ older_than_days: 90 });
  });

  it("keeps the optional source argument backward compatible", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = stubFetchJson({ ok: true, removed: 2 });

    await api.pruneSessions(30, "telegram");

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      older_than_days: 30,
      source: "telegram",
    });
  });
});

describe("api.getModelOptions", () => {
  it("requests a live model refresh when asked", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = stubFetchJson({ providers: [] });

    await api.getModelOptions({ refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model/options?refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("keeps explicit profile scoping when refreshing", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = stubFetchJson({ providers: [] });

    await api.getModelOptions({ profile: "default", refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model/options?profile=default&refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});
