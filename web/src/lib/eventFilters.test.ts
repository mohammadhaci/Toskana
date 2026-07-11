import { describe, expect, it } from "vitest";

import { defaultEventFilters, filtersToQuery, localInputToMs, msToLocalInput } from "./eventFilters";

describe("filtersToQuery", () => {
  it("serializes defaults to canonical_only + limit only", () => {
    expect(filtersToQuery(defaultEventFilters)).toBe("?canonical_only=true&limit=50");
  });

  it("serializes all set filters deterministically (sorted keys)", () => {
    const query = filtersToQuery({
      ...defaultEventFilters,
      from_ts: 1000,
      to_ts: 2000,
      camera_id: 3,
      category_id: 7,
      direction: "out",
      canonical_only: false,
      q: " pizza ",
      offset: 100,
    });
    expect(query).toBe(
      "?camera_id=3&canonical_only=false&category_id=7&direction=out&from_ts=1000&limit=50&offset=100&q=pizza&to_ts=2000",
    );
  });

  it("omits paging for the CSV export link", () => {
    const query = filtersToQuery({ ...defaultEventFilters, camera_id: 2, offset: 100 }, false);
    expect(query).toBe("?camera_id=2&canonical_only=true");
  });

  it("skips offset 0 and empty q", () => {
    const query = filtersToQuery({ ...defaultEventFilters, q: "   " });
    expect(query).not.toContain("offset=");
    expect(query).not.toContain("q=");
  });
});

describe("datetime-local helpers", () => {
  it("round-trips a local timestamp at minute precision", () => {
    const value = "2026-07-10T13:45";
    const ms = localInputToMs(value);
    expect(ms).not.toBeNull();
    expect(msToLocalInput(ms)).toBe(value);
  });

  it("handles empty and invalid input", () => {
    expect(localInputToMs("")).toBeNull();
    expect(localInputToMs("garbage")).toBeNull();
    expect(msToLocalInput(null)).toBe("");
  });
});
