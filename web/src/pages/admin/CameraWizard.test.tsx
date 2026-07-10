import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../../i18n";
import i18n from "../../i18n";
import type { CameraPreset, CameraTestResult } from "../../api/types";
import { ToastProvider } from "../../components/Toast";
import CameraWizard, { maskRtspPassword, previewUrl } from "./CameraWizard";

const PRESETS: CameraPreset[] = [
  {
    key: "hikvision",
    label: "Hikvision (main stream)",
    default_port: 554,
    needs_channel: true,
    url_template: "rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}01",
  },
  {
    key: "reolink",
    label: "Reolink",
    default_port: 554,
    needs_channel: true,
    url_template: "rtsp://{user}:{password}@{ip}:{port}/h264Preview_{channel:02d}_main",
  },
  {
    key: "tplink",
    label: "TP-Link Tapo / Vigi",
    default_port: 554,
    needs_channel: false,
    url_template: "rtsp://{user}:{password}@{ip}:{port}/stream1",
  },
  { key: "generic", label: "Other", default_port: 554, needs_channel: false, url_template: null },
];

const TEST_OK: CameraTestResult = {
  ok: true,
  source_type: "rtsp",
  source_url_masked: "rtsp://admin:•••@192.168.1.20:554/Streaming/Channels/101",
  source_url: "rtsp://admin:pw@192.168.1.20:554/Streaming/Channels/101",
  width: 1920,
  height: 1080,
  fps: 25,
  snapshot_b64: "aGVsbG8=",
  error: null,
};

const TEST_FAIL: CameraTestResult = {
  ok: false,
  source_type: "rtsp",
  source_url_masked: "rtsp://admin:•••@192.168.1.20:554/Streaming/Channels/101",
  source_url: null,
  width: null,
  height: null,
  fps: null,
  snapshot_b64: null,
  error: "Could not connect to the camera stream.",
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWizard(testResult: CameraTestResult) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL): Promise<Response> => {
      const url = String(input);
      if (url.includes("/api/camera-presets")) return Promise.resolve(jsonResponse(PRESETS));
      if (url.includes("/cameras/test-source")) return Promise.resolve(jsonResponse(testResult));
      return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 500, offset: 0 }));
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <CameraWizard rid={1} exitGroups={[]} />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("maskRtspPassword", () => {
  it("masks only the password part", () => {
    expect(maskRtspPassword("rtsp://admin:s3cret@10.0.0.1:554/x")).toBe(
      "rtsp://admin:•••@10.0.0.1:554/x",
    );
  });

  it("leaves credential-free URLs untouched", () => {
    expect(maskRtspPassword("rtsp://10.0.0.1:554/x")).toBe("rtsp://10.0.0.1:554/x");
    expect(maskRtspPassword("./video.mp4")).toBe("./video.mp4");
  });
});

describe("previewUrl", () => {
  const fields = { username: "admin", password: "pw", ip: "192.168.1.20", port: 554, channel: 3 };

  it("renders the hikvision template with a masked password and NVR channel", () => {
    expect(previewUrl(PRESETS[0].url_template as string, fields)).toBe(
      "rtsp://admin:•••@192.168.1.20:554/Streaming/Channels/301",
    );
  });

  it("zero-pads the reolink channel", () => {
    expect(previewUrl(PRESETS[1].url_template as string, fields)).toContain("h264Preview_03_main");
  });

  it("drops empty credentials and encodes special usernames", () => {
    const template = PRESETS[2].url_template as string;
    expect(previewUrl(template, { ...fields, username: "", password: "" })).toBe(
      "rtsp://192.168.1.20:554/stream1",
    );
    expect(previewUrl(template, { ...fields, username: "user@site" })).toContain("user%40site:•••@");
  });
});

describe("CameraWizard", () => {
  beforeEach(() => {
    void i18n.changeLanguage("de");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("walks type → details with a masked live URL preview", async () => {
    renderWizard(TEST_OK);
    // Step 1: three big type choices.
    expect(screen.getByText("IP-Kamera (RTSP)")).toBeTruthy();
    expect(screen.getByText("USB-Webcam")).toBeTruthy();
    expect(screen.getByText("Videodatei")).toBeTruthy();

    fireEvent.click(screen.getByText("IP-Kamera (RTSP)"));
    // Step 2: preset fields with channel (hikvision default needs one).
    expect(await screen.findByRole("option", { name: /Hikvision/ })).toBeTruthy();
    expect(screen.getByLabelText("Kamera-Marke")).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("IP-Adresse"), {
      target: { value: "192.168.1.20" },
    });
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "s3cret" } });
    expect(screen.getByLabelText("Kanal (NVR)")).toBeTruthy();

    const preview = screen.getByText(/Streaming\/Channels\/101/);
    expect(preview.textContent).toContain("rtsp://admin:•••@192.168.1.20:554");
    expect(preview.textContent).not.toContain("s3cret");
  });

  it("gates saving on a successful test and shows the snapshot", async () => {
    renderWizard(TEST_OK);
    fireEvent.click(screen.getByText("IP-Kamera (RTSP)"));
    fireEvent.change(await screen.findByLabelText("IP-Adresse"), {
      target: { value: "192.168.1.20" },
    });
    fireEvent.click(screen.getByText("Weiter"));

    // Step 3: cannot proceed before a successful test.
    const nextBefore = screen.getByText("Weiter") as HTMLButtonElement;
    expect(nextBefore.disabled).toBe(true);

    fireEvent.click(screen.getByText("Verbindung testen"));
    expect(await screen.findByText("Verbindung erfolgreich")).toBeTruthy();
    expect(screen.getByText(/1920×1080/)).toBeTruthy();
    expect((screen.getByAltText("Kamera-Testbild") as HTMLImageElement).src).toContain(
      "data:image/jpeg;base64,aGVsbG8=",
    );

    fireEvent.click(screen.getByText("Weiter"));
    // Step 4: save disabled until a name is entered.
    const save = screen.getByText("Speichern") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Pass links" } });
    expect((screen.getByText("Speichern") as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows the friendly error and hints when the test fails", async () => {
    renderWizard(TEST_FAIL);
    fireEvent.click(screen.getByText("IP-Kamera (RTSP)"));
    fireEvent.change(await screen.findByLabelText("IP-Adresse"), {
      target: { value: "192.168.1.20" },
    });
    fireEvent.click(screen.getByText("Weiter"));
    fireEvent.click(screen.getByText("Verbindung testen"));

    expect(await screen.findByText("Verbindung fehlgeschlagen")).toBeTruthy();
    expect(screen.getByText("Could not connect to the camera stream.")).toBeTruthy();
    expect(screen.getByText(/IP-Adresse prüfen/)).toBeTruthy();
    // Staying on the test step: no way forward.
    expect((screen.getByText("Weiter") as HTMLButtonElement).disabled).toBe(true);
  });

  it("lets a video file proceed without a test", async () => {
    renderWizard(TEST_OK);
    fireEvent.click(screen.getByText("Videodatei"));
    fireEvent.change(await screen.findByLabelText("Pfad zur Videodatei"), {
      target: { value: "./data/videos/demo.mp4" },
    });
    fireEvent.click(screen.getByText("Weiter"));
    expect(screen.getByText("Videodateien können auch ohne Test gespeichert werden.")).toBeTruthy();
    expect((screen.getByText("Weiter") as HTMLButtonElement).disabled).toBe(false);
  });
});
