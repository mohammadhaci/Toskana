import { describe, expect, it } from "vitest";

import de from "./de.json";
import en from "./en.json";

function flatten(obj: Record<string, unknown>, prefix = ""): string[] {
  const keys: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      keys.push(...flatten(value as Record<string, unknown>, path));
    } else {
      keys.push(path);
    }
  }
  return keys;
}

describe("i18n catalogs", () => {
  it("de and en have identical key sets", () => {
    const deKeys = flatten(de).sort();
    const enKeys = flatten(en).sort();
    expect(deKeys).toEqual(enKeys);
  });

  it("no empty translations", () => {
    for (const catalog of [de, en]) {
      const check = (obj: Record<string, unknown>, prefix: string) => {
        for (const [key, value] of Object.entries(obj)) {
          const path = prefix ? `${prefix}.${key}` : key;
          if (value !== null && typeof value === "object") {
            check(value as Record<string, unknown>, path);
          } else {
            expect(String(value).length, `empty translation at ${path}`).toBeGreaterThan(0);
          }
        }
      };
      check(catalog, "");
    }
  });
});
