import type { Category, LiveCounter } from "../api/types";

/** Localized display name for a category-ish record (falls back to key). */
export function categoryLabel(
  record: Pick<Category | LiveCounter, "name_de" | "name_en"> & { key?: string | null },
  lang: string,
  fallback = "?",
): string {
  const name = lang.startsWith("de") ? record.name_de : record.name_en;
  return name ?? record.name_en ?? record.name_de ?? record.key ?? fallback;
}

export function formatTime(ms: number, lang: string): string {
  return new Date(ms).toLocaleTimeString(lang.startsWith("de") ? "de-AT" : "en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDateTime(ms: number, lang: string): string {
  return new Date(ms).toLocaleString(lang.startsWith("de") ? "de-AT" : "en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = "B";
  for (const next of units) {
    if (value < 1024) break;
    value /= 1024;
    unit = next;
  }
  return `${value.toFixed(1)} ${unit}`;
}
