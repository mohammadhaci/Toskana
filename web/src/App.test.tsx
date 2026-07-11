import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "./i18n";
import i18n from "./i18n";
import App from "./App";
import { ToastProvider } from "./components/Toast";

const SYSTEM_INFO = {
  version: "0.1.0",
  active_restaurant_slug: "demo",
  active_restaurant: {
    id: 1,
    slug: "demo",
    name: "Trattoria Demo",
    timezone: "Europe/Vienna",
    locale_default: "de",
    settings_json: null,
  },
  db_path: "/tmp/t.db",
  db_size_bytes: 1024,
  torch_available: false,
  cuda_available: false,
  detector_backend: "synthetic",
  device: "cpu",
  loop_file_sources: true,
  snapshots_dir: "/tmp/snaps",
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(input: RequestInfo | URL): Promise<Response> {
  const url = String(input);
  if (url.includes("/api/system/info")) return Promise.resolve(jsonResponse(SYSTEM_INFO));
  if (url.includes("/cameras")) {
    return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 500, offset: 0 }));
  }
  return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 500, offset: 0 }));
}

function renderApp(path = "/live") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter
          initialEntries={[path]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <App />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("App smoke test", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(mockFetch));
    void i18n.changeLanguage("de");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the live page inside the shell (German default)", async () => {
    renderApp("/live");
    // sidebar nav labels (German)
    expect(await screen.findByText("Statistiken")).toBeTruthy();
    expect(screen.getByText("Ereignisse")).toBeTruthy();
    // active restaurant name lands in the sidebar brand once /system/info resolves
    await waitFor(() => expect(screen.getByText("Trattoria Demo")).toBeTruthy());
    // empty camera state with guidance
    expect(await screen.findByText("Keine Kameras angelegt.")).toBeTruthy();
  });

  it("switches the whole UI to English via the language switcher", async () => {
    renderApp("/live");
    await screen.findByText("Statistiken");
    await i18n.changeLanguage("en");
    expect(await screen.findByText("Statistics")).toBeTruthy();
    expect(screen.getByText("Reconciliation")).toBeTruthy();
  });

  it("renders a not-found state for unknown routes", async () => {
    void i18n.changeLanguage("de");
    renderApp("/definitely-not-a-page");
    expect(await screen.findByText("Seite nicht gefunden")).toBeTruthy();
  });
});
