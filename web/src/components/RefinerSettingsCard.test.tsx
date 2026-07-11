import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../i18n";
import i18n from "../i18n";
import type { RefinerSettings, RefinerTestResult } from "../api/types";
import { ToastProvider } from "./Toast";
import RefinerSettingsCard from "./RefinerSettingsCard";

const SETTINGS: RefinerSettings = {
  provider: "off",
  model: "claude-haiku-4-5",
  base_url: "http://localhost:11434/v1",
  has_api_key: false,
  only_below_confidence: 1,
  match_menu_items: true,
  max_per_minute: 30,
};

const TEST_OK: RefinerTestResult = {
  ok: true,
  latency_ms: 640,
  reply: { category_key: "drink", menu_item_name: "Spritzer", confidence: 0.9, is_item: true },
  error: null,
};

const TEST_FAIL: RefinerTestResult = {
  ok: false,
  latency_ms: null,
  reply: null,
  error: "refiner backend unreachable at http://localhost:11434/v1",
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function renderCard(settings: RefinerSettings, testResult: RefinerTestResult) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (url.includes("/api/settings/refiner/test")) {
        return Promise.resolve(jsonResponse(testResult));
      }
      if (url.includes("/api/settings/refiner")) {
        return Promise.resolve(jsonResponse(settings));
      }
      return Promise.resolve(jsonResponse({}));
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <RefinerSettingsCard />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return calls;
}

describe("RefinerSettingsCard", () => {
  beforeEach(() => {
    void i18n.changeLanguage("de");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the base URL (with quick-fill) only for the local provider", async () => {
    renderCard(SETTINGS, TEST_OK);
    const provider = (await screen.findByLabelText("Anbieter")) as HTMLSelectElement;
    // provider=off: no base_url, no api_key field.
    expect(screen.queryByLabelText("Server-Adresse (Base URL)")).toBeNull();
    expect(screen.queryByLabelText("API-Schlüssel")).toBeNull();

    fireEvent.change(provider, { target: { value: "openai_compatible" } });
    const baseUrl = screen.getByLabelText("Server-Adresse (Base URL)") as HTMLInputElement;
    expect(baseUrl.value).toBe("http://localhost:11434/v1");
    fireEvent.click(screen.getByText("LM Studio :1234"));
    expect((screen.getByLabelText("Server-Adresse (Base URL)") as HTMLInputElement).value).toBe(
      "http://localhost:1234/v1",
    );

    fireEvent.change(provider, { target: { value: "anthropic" } });
    expect(screen.queryByLabelText("Server-Adresse (Base URL)")).toBeNull();
    expect(screen.getByLabelText("API-Schlüssel")).toBeTruthy();
  });

  it("posts the current form values on test and shows the verdict", async () => {
    const calls = renderCard(SETTINGS, TEST_OK);
    const provider = (await screen.findByLabelText("Anbieter")) as HTMLSelectElement;
    fireEvent.change(provider, { target: { value: "openai_compatible" } });
    fireEvent.change(screen.getByLabelText("Modell"), { target: { value: "llava" } });
    fireEvent.change(screen.getByLabelText("Nur unter Konfidenz prüfen"), {
      target: { value: "0.65" },
    });

    fireEvent.click(screen.getByText("Verbindung testen"));
    expect(await screen.findByText("Verbindung erfolgreich")).toBeTruthy();
    expect(screen.getByText(/640 ms/)).toBeTruthy();
    expect(screen.getByText("Spritzer")).toBeTruthy();
    expect(screen.getByText("90%")).toBeTruthy();

    const testCall = calls.find((c) => c.url.includes("/test"));
    expect(testCall?.method).toBe("POST");
    expect(testCall?.body).toMatchObject({
      provider: "openai_compatible",
      model: "llava",
      base_url: "http://localhost:11434/v1",
      only_below_confidence: 0.65,
      match_menu_items: true,
      max_per_minute: 30,
    });
    // No key typed: none is sent (the server falls back to the saved one).
    expect(testCall?.body).not.toHaveProperty("api_key");
  });

  it("shows the friendly error box when the test fails", async () => {
    renderCard(SETTINGS, TEST_FAIL);
    const provider = (await screen.findByLabelText("Anbieter")) as HTMLSelectElement;
    fireEvent.change(provider, { target: { value: "openai_compatible" } });
    fireEvent.click(screen.getByText("Verbindung testen"));

    expect(await screen.findByText("Verbindung fehlgeschlagen")).toBeTruthy();
    expect(screen.getByText(/unreachable/)).toBeTruthy();
  });

  it("saves via PUT, sending the api_key only when one was typed", async () => {
    const calls = renderCard({ ...SETTINGS, provider: "anthropic" }, TEST_OK);
    const apiKey = await screen.findByLabelText("API-Schlüssel");
    fireEvent.change(apiKey, { target: { value: "sk-ant-new" } });
    fireEvent.click(screen.getByText("Speichern"));

    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT");
      expect(put).toBeTruthy();
      expect(put?.url).toContain("/api/settings/refiner");
      expect(put?.body).toMatchObject({ provider: "anthropic", api_key: "sk-ant-new" });
    });
  });

  it("keeps the saved key on save when the field stays empty", async () => {
    const calls = renderCard({ ...SETTINGS, provider: "anthropic", has_api_key: true }, TEST_OK);
    const apiKey = (await screen.findByLabelText("API-Schlüssel")) as HTMLInputElement;
    expect(apiKey.placeholder).toBe("••••• gespeichert");
    fireEvent.click(screen.getByText("Speichern"));

    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT");
      expect(put).toBeTruthy();
      expect(put?.body).not.toHaveProperty("api_key");
    });
  });

  it("clears the saved key when requested", async () => {
    const calls = renderCard({ ...SETTINGS, provider: "anthropic", has_api_key: true }, TEST_OK);
    await screen.findByLabelText("API-Schlüssel");
    fireEvent.click(screen.getByText("Gespeicherten Schlüssel löschen"));
    expect(screen.getByText("Der Schlüssel wird beim Speichern gelöscht.")).toBeTruthy();
    fireEvent.click(screen.getByText("Speichern"));

    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT");
      expect(put?.body).toMatchObject({ api_key: "" });
    });
  });
});
