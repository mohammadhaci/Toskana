/** Analysis API client: URL/method mapping and multipart form encoding. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";
import type { AnalysisJob } from "./types";

const JOB: AnalysisJob = {
  id: "01JJOB",
  restaurant_id: 1,
  status: "queued",
  video_name: "clip.mp4",
  source_url: null,
  backend: "synthetic",
  camera_id: null,
  line_id: null,
  frames_done: 0,
  frames_total: null,
  counts: { out: {}, in: {} },
  total_out: 0,
  total_in: 0,
  error: null,
  created_ts: 1,
  finished_ts: null,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("analysis api client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset().mockResolvedValue(jsonResponse(JOB));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("lists and fetches jobs with restaurant-scoped URLs", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([JOB]));
    await expect(api.listAnalysisJobs(7)).resolves.toEqual([JOB]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/restaurants/7/analysis");

    await api.getAnalysisJob(7, "01JJOB");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/restaurants/7/analysis/01JJOB");
  });

  it("posts uploads as multipart form data with all fields", async () => {
    const file = new File(["not a real video"], "kitchen pass.mp4", { type: "video/mp4" });
    await api.createAnalysisJob(7, {
      file,
      backend: "synthetic",
      line: { x1: 0.4, y1: 0, x2: 0.4, y2: 1 },
      count_directions: "out",
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/restaurants/7/analysis");
    expect(init.method).toBe("POST");
    const form = init.body as FormData;
    expect(form).toBeInstanceOf(FormData);
    expect(form.get("file")).toBeInstanceOf(File);
    expect((form.get("file") as File).name).toBe("kitchen pass.mp4");
    expect(form.get("url")).toBeNull();
    expect(form.get("backend")).toBe("synthetic");
    expect(form.get("x1")).toBe("0.4");
    expect(form.get("y2")).toBe("1");
    expect(form.get("count_directions")).toBe("out");
  });

  it("posts URL jobs without a file part and defaults the line", async () => {
    await api.createAnalysisJob(7, { url: "https://youtu.be/x", backend: "yolo" });
    const form = fetchMock.mock.calls[0][1].body as FormData;
    expect(form.get("file")).toBeNull();
    expect(form.get("url")).toBe("https://youtu.be/x");
    expect(form.get("backend")).toBe("yolo");
    expect(form.get("x1")).toBeNull(); // server default: vertical line at 0.5
  });

  it("cancels via POST .../{id}/cancel", async () => {
    await api.cancelAnalysisJob(7, "01JJOB");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/restaurants/7/analysis/01JJOB/cancel");
    expect(init.method).toBe("POST");
  });

  it("surfaces the server's detail message on errors", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "provide either a video file or a url (not both)" }), {
        status: 422,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(api.createAnalysisJob(7, { url: "https://x" })).rejects.toMatchObject({
      status: 422,
      detail: "provide either a video file or a url (not both)",
    });
  });
});
