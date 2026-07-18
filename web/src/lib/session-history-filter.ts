export type SessionsView = "list" | "overview";

export const DEFAULT_SESSIONS_VIEW: SessionsView = "list";
export const DEFAULT_SESSION_SOURCE = "telegram";
export const ALL_SESSION_SOURCES = "all";

export const SESSION_ROW_TITLE_CLASS =
  "font-sans normal-case tracking-normal min-w-0 flex-1 truncate text-sm";

export const SESSION_SOURCE_BADGE_TEXT_CLASS =
  "font-sans normal-case tracking-normal";

export interface SessionSourceOption {
  value: string;
  label: string;
}

export interface SessionSourceListReset {
  page: 0;
  selectedIds: Set<string>;
  lastClickedIndex: null;
  expandedId: null;
}

export function sessionSourceQuery(source: string): string | undefined {
  const trimmed = source.trim();
  return trimmed && trimmed.toLowerCase() !== ALL_SESSION_SOURCES
    ? trimmed
    : undefined;
}

export function sessionSourceLabel(source: string): string {
  const normalized = source.trim();
  if (!normalized) return "Unknown";
  if (normalized.toLowerCase() === ALL_SESSION_SOURCES) return "All";
  return normalized
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

export function buildSessionSourceOptions(
  bySource: Record<string, number> | null | undefined,
): SessionSourceOption[] {
  const options: SessionSourceOption[] = [
    { value: "telegram", label: "Telegram" },
    { value: "cron", label: "Cron" },
  ];
  const seen = new Set(options.map((option) => option.value.toLowerCase()));
  const actualSources = Object.keys(bySource ?? {})
    .map((source) => source.trim())
    .filter(Boolean)
    .filter((source) => {
      const key = source.toLowerCase();
      if (key === ALL_SESSION_SOURCES || seen.has(key)) return false;
      seen.add(key);
      return true;
    });

  for (const source of actualSources) {
    options.push({ value: source, label: sessionSourceLabel(source) });
  }
  options.push({ value: ALL_SESSION_SOURCES, label: "All" });
  return options;
}

export function resetSessionSourceListState(): SessionSourceListReset {
  return {
    page: 0,
    selectedIds: new Set<string>(),
    lastClickedIndex: null,
    expandedId: null,
  };
}
