import { afterEach, describe, expect, it, vi } from "vitest";

import {
  authHeaders,
  getToken,
  notifyUnauthorized,
  onUnauthorized,
  setToken,
  withToken,
} from "./auth";

afterEach(() => setToken(null));

describe("token storage", () => {
  it("persists and clears the token", () => {
    expect(getToken()).toBeNull();
    setToken("abc");
    expect(getToken()).toBe("abc");
    setToken(null);
    expect(getToken()).toBeNull();
  });
});

describe("authHeaders", () => {
  it("is empty in open LAN mode and Bearer with a token", () => {
    expect(authHeaders()).toEqual({});
    setToken("s3cret");
    expect(authHeaders()).toEqual({ Authorization: "Bearer s3cret" });
  });
});

describe("withToken (media/WS URLs cannot set headers)", () => {
  it("leaves URLs untouched without a token", () => {
    expect(withToken("/api/stream/1")).toBe("/api/stream/1");
  });

  it("appends ?token= (or &token= after a query) URL-encoded", () => {
    setToken("a b/c");
    expect(withToken("/api/stream/1")).toBe("/api/stream/1?token=a%20b%2Fc");
    expect(withToken("/api/snapshot/1?t=5")).toBe("/api/snapshot/1?t=5&token=a%20b%2Fc");
    expect(withToken("ws://host/ws/live")).toBe("ws://host/ws/live?token=a%20b%2Fc");
  });
});

describe("unauthorized notifications", () => {
  it("notifies subscribers until they unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    notifyUnauthorized();
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    notifyUnauthorized();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
