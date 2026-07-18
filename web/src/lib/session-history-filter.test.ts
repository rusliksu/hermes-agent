import { describe, expect, it } from "vitest";

import {
  DEFAULT_SESSION_SOURCE,
  DEFAULT_SESSIONS_VIEW,
  SESSION_ROW_TITLE_CLASS,
  SESSION_SOURCE_BADGE_TEXT_CLASS,
  buildSessionSourceOptions,
  createLatestSessionListRequestGuard,
  resetSessionSourceListState,
  sessionSourceQuery,
} from "./session-history-filter";

describe("session history filter helpers", () => {
  it("defaults sessions to History and Telegram", () => {
    expect(DEFAULT_SESSIONS_VIEW).toBe("list");
    expect(DEFAULT_SESSION_SOURCE).toBe("telegram");
  });

  it("builds stable source options with deduped actual sources and All last", () => {
    expect(
      buildSessionSourceOptions({
        Telegram: 1,
        cron: 2,
        desktop: 3,
        cli: 4,
      }),
    ).toEqual([
      { value: "telegram", label: "Telegram" },
      { value: "cron", label: "Cron" },
      { value: "desktop", label: "Desktop" },
      { value: "cli", label: "Cli" },
      { value: "all", label: "All" },
    ]);
  });

  it("omits the source query only for All or blank values", () => {
    expect(sessionSourceQuery("telegram")).toBe("telegram");
    expect(sessionSourceQuery("all")).toBeUndefined();
    expect(sessionSourceQuery("All")).toBeUndefined();
    expect(sessionSourceQuery(" ")).toBeUndefined();
  });

  it("returns the reset state used when source changes", () => {
    const reset = resetSessionSourceListState();

    expect(reset.page).toBe(0);
    expect(reset.selectedIds.size).toBe(0);
    expect(reset.lastClickedIndex).toBeNull();
    expect(reset.expandedId).toBeNull();
  });

  it("keeps session row typography scoped to readable sans classes", () => {
    expect(SESSION_ROW_TITLE_CLASS).toContain("font-sans");
    expect(SESSION_ROW_TITLE_CLASS).not.toContain("font-mondwest");
    expect(SESSION_SOURCE_BADGE_TEXT_CLASS).toContain("font-sans");
    expect(SESSION_SOURCE_BADGE_TEXT_CLASS).toContain("normal-case");
    expect(SESSION_SOURCE_BADGE_TEXT_CLASS).toContain("tracking-normal");
    expect(SESSION_SOURCE_BADGE_TEXT_CLASS).not.toContain("font-compressed");
  });

  it("prevents stale list requests from overwriting list state", () => {
    const guard = createLatestSessionListRequestGuard();
    const state = { sessions: ["new"], total: 1, loading: true };
    const oldPageRequest = guard.next();
    const newSourceRequest = guard.next();

    if (guard.isLatest(oldPageRequest)) {
      state.sessions = ["old"];
      state.total = 99;
    }
    if (guard.isLatest(oldPageRequest)) {
      state.loading = false;
    }

    expect(state).toEqual({ sessions: ["new"], total: 1, loading: true });

    if (guard.isLatest(newSourceRequest)) {
      state.sessions = ["new-source"];
      state.total = 2;
    }

    const silentReloadRequest = guard.next();

    if (guard.isLatest(newSourceRequest)) {
      state.sessions = ["late-new-source"];
      state.total = 3;
    }
    if (guard.isLatest(newSourceRequest)) {
      state.loading = false;
    }

    expect(state).toEqual({ sessions: ["new-source"], total: 2, loading: true });

    if (guard.isLatest(silentReloadRequest)) {
      state.loading = false;
    }

    expect(state).toEqual({ sessions: ["new-source"], total: 2, loading: false });
  });
});
