import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../i18n";
import i18n from "../i18n";
import type { AnalysisJob } from "../api/types";
import { ToastProvider } from "../components/Toast";
import AnalysisPage from "./AnalysisPage";

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

const DONE_JOB: AnalysisJob = {
  id: "01JDONE",
  restaurant_id: 1,
  status: "done",
  video_name: "mittagsservice.mp4",
  source_url: null,
  backend: "synthetic",
  camera_id: 42,
  line_id: 9,
  frames_done: 120,
  frames_total: 120,
  counts: { out: { drink: 1, main: 2 }, in: {} },
  total_out: 3,
  total_in: 0,
  error: null,
  created_ts: 1735000000000,
  finished_ts: 1735000010000,
};

const RUNNING_JOB: AnalysisJob = {
  ...DONE_JOB,
  id: "01JRUN",
  status: "running",
  video_name: "abendservice.mp4",
  frames_done: 30,
  finished_ts: null,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(jobs: AnalysisJob[]) {
  return (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (url.includes("/api/system/info")) return Promise.resolve(jsonResponse(SYSTEM_INFO));
    if (url.includes("/analysis")) return Promise.resolve(jsonResponse(jobs));
    return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 500, offset: 0 }));
  };
}

function renderPage(jobs: AnalysisJob[]) {
  vi.stubGlobal("fetch", vi.fn(mockFetch(jobs)));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter
          initialEntries={["/analysis"]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <AnalysisPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("AnalysisPage", () => {
  beforeEach(() => {
    void i18n.changeLanguage("de");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the upload form and the empty state (German)", async () => {
    renderPage([]);
    expect(await screen.findByText("Video analysieren")).toBeTruthy();
    expect(screen.getByText(/Video hier ablegen/)).toBeTruthy();
    expect(screen.getByLabelText("… oder Video-Link einfügen")).toBeTruthy();
    expect(await screen.findByText("Noch keine Analysen.")).toBeTruthy();
    // Submit stays disabled until a file or a URL is chosen.
    expect((screen.getByText("Analyse starten") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows job rows with status badge, counts and the events link", async () => {
    renderPage([RUNNING_JOB, DONE_JOB]);
    expect(await screen.findByText("mittagsservice.mp4")).toBeTruthy();
    expect(screen.getByText("abendservice.mp4")).toBeTruthy();
    expect(screen.getByText("Fertig")).toBeTruthy();
    expect(screen.getByText("Analysiert")).toBeTruthy();
    // Counts table of the finished job (1 drink, 2 mains out).
    expect(screen.getAllByText("drink").length).toBeGreaterThan(0);
    // Done job links to the Events page filtered by its camera.
    const link = screen.getByText("Gezählte Ereignisse anzeigen") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/events?camera_id=42");
    // The running job offers a cancel button; the done one does not.
    expect(screen.getAllByText("Abbrechen")).toHaveLength(1);
  });
});
