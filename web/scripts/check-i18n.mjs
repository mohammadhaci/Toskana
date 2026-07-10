#!/usr/bin/env node
/** Fails (exit 1) if the de/en translation catalogs have diverging key sets. */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const i18nDir = join(here, "..", "src", "i18n");

function flatten(obj, prefix = "", out = []) {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      flatten(value, path, out);
    } else {
      out.push(path);
    }
  }
  return out;
}

const de = JSON.parse(readFileSync(join(i18nDir, "de.json"), "utf8"));
const en = JSON.parse(readFileSync(join(i18nDir, "en.json"), "utf8"));

const deKeys = new Set(flatten(de));
const enKeys = new Set(flatten(en));

const missingInEn = [...deKeys].filter((k) => !enKeys.has(k)).sort();
const missingInDe = [...enKeys].filter((k) => !deKeys.has(k)).sort();

if (missingInEn.length || missingInDe.length) {
  if (missingInEn.length) console.error(`Missing in en.json:\n  ${missingInEn.join("\n  ")}`);
  if (missingInDe.length) console.error(`Missing in de.json:\n  ${missingInDe.join("\n  ")}`);
  process.exit(1);
}

console.log(`i18n OK: ${deKeys.size} keys in both de.json and en.json`);
