/** Typed fetch wrapper for the Toskana REST API (same-origin `/api`). */

import { authHeaders, notifyUnauthorized, withToken } from "./auth";
import type {
  AnalysisJob,
  AnalysisJobCreate,
  Camera,
  CameraCreate,
  CameraPreset,
  CameraStatus,
  CameraTestResult,
  CameraTestSourceRequest,
  CameraUpdate,
  Category,
  CategoryCreate,
  CategoryUpdate,
  DataGapRecord,
  EventRecord,
  ExitGroup,
  ExitGroupCreate,
  ExitGroupUpdate,
  Line,
  LineCreate,
  LineUpdate,
  Mapping,
  MappingCreate,
  MappingUpdate,
  MenuItem,
  MenuItemCreate,
  MenuItemUpdate,
  ModelInfo,
  Page,
  ReconcileReport,
  ReconcileRun,
  Restaurant,
  RestaurantCreate,
  RestaurantUpdate,
  RetentionRunResult,
  StatsSummary,
  SystemHealth,
  SystemInfo,
  TimeseriesResponse,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export type QueryValue = string | number | boolean | null | undefined;

/** Serialize query params deterministically (sorted keys, skip null/undefined/""). */
export function toQueryString(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const key of Object.keys(params).sort()) {
    const value = params[key];
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { ...authHeaders(), ...init?.headers },
  });
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(); // server wants an API token
    let detail = response.statusText || `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json(body: unknown, method: string): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

const PAGE_ALL = "?limit=500";

export const api = {
  // -- system ---------------------------------------------------------------
  systemInfo: () => request<SystemInfo>("/system/info"),
  systemHealth: () => request<SystemHealth>("/system/health"),
  runRetention: () => request<RetentionRunResult>("/system/retention/run", { method: "POST" }),

  // -- restaurants ------------------------------------------------------------
  listRestaurants: () => request<Page<Restaurant>>(`/restaurants${PAGE_ALL}`),
  createRestaurant: (body: RestaurantCreate) => request<Restaurant>("/restaurants", json(body, "POST")),
  updateRestaurant: (id: number, body: RestaurantUpdate) =>
    request<Restaurant>(`/restaurants/${id}`, json(body, "PUT")),
  deleteRestaurant: (id: number) => request<void>(`/restaurants/${id}`, { method: "DELETE" }),

  // -- cameras -----------------------------------------------------------------
  listCameras: (rid: number) => request<Page<Camera>>(`/restaurants/${rid}/cameras${PAGE_ALL}`),
  createCamera: (rid: number, body: CameraCreate) =>
    request<Camera>(`/restaurants/${rid}/cameras`, json(body, "POST")),
  updateCamera: (rid: number, id: number, body: CameraUpdate) =>
    request<Camera>(`/restaurants/${rid}/cameras/${id}`, json(body, "PUT")),
  deleteCamera: (rid: number, id: number) =>
    request<void>(`/restaurants/${rid}/cameras/${id}`, { method: "DELETE" }),
  cameraStatus: (id: number) => request<CameraStatus>(`/cameras/${id}/status`),
  startCamera: (id: number) => request<CameraStatus>(`/cameras/${id}/start`, { method: "POST" }),
  stopCamera: (id: number) => request<CameraStatus>(`/cameras/${id}/stop`, { method: "POST" }),
  restartCamera: (id: number) => request<CameraStatus>(`/cameras/${id}/restart`, { method: "POST" }),
  calibrateCamera: (id: number) =>
    request<CameraStatus>(`/cameras/${id}/calibrate`, { method: "POST" }),
  listCameraPresets: () => request<CameraPreset[]>("/camera-presets"),
  testCameraSource: (rid: number, body: CameraTestSourceRequest) =>
    request<CameraTestResult>(`/restaurants/${rid}/cameras/test-source`, json(body, "POST")),

  // -- lines ---------------------------------------------------------------------
  listLines: (cameraId: number) => request<Page<Line>>(`/cameras/${cameraId}/lines${PAGE_ALL}`),
  createLine: (cameraId: number, body: LineCreate) =>
    request<Line>(`/cameras/${cameraId}/lines`, json(body, "POST")),
  updateLine: (cameraId: number, lineId: number, body: LineUpdate) =>
    request<Line>(`/cameras/${cameraId}/lines/${lineId}`, json(body, "PUT")),
  deleteLine: (cameraId: number, lineId: number) =>
    request<void>(`/cameras/${cameraId}/lines/${lineId}`, { method: "DELETE" }),

  // -- exit groups ------------------------------------------------------------------
  listExitGroups: (rid: number) => request<Page<ExitGroup>>(`/restaurants/${rid}/exit-groups${PAGE_ALL}`),
  createExitGroup: (rid: number, body: ExitGroupCreate) =>
    request<ExitGroup>(`/restaurants/${rid}/exit-groups`, json(body, "POST")),
  updateExitGroup: (rid: number, id: number, body: ExitGroupUpdate) =>
    request<ExitGroup>(`/restaurants/${rid}/exit-groups/${id}`, json(body, "PUT")),
  deleteExitGroup: (rid: number, id: number) =>
    request<void>(`/restaurants/${rid}/exit-groups/${id}`, { method: "DELETE" }),

  // -- categories & menu items --------------------------------------------------------
  listCategories: (rid: number) => request<Page<Category>>(`/restaurants/${rid}/categories${PAGE_ALL}`),
  createCategory: (rid: number, body: CategoryCreate) =>
    request<Category>(`/restaurants/${rid}/categories`, json(body, "POST")),
  updateCategory: (rid: number, id: number, body: CategoryUpdate) =>
    request<Category>(`/restaurants/${rid}/categories/${id}`, json(body, "PUT")),
  deleteCategory: (rid: number, id: number) =>
    request<void>(`/restaurants/${rid}/categories/${id}`, { method: "DELETE" }),

  listMenuItems: (rid: number) => request<Page<MenuItem>>(`/restaurants/${rid}/menu-items${PAGE_ALL}`),
  createMenuItem: (rid: number, body: MenuItemCreate) =>
    request<MenuItem>(`/restaurants/${rid}/menu-items`, json(body, "POST")),
  updateMenuItem: (rid: number, id: number, body: MenuItemUpdate) =>
    request<MenuItem>(`/restaurants/${rid}/menu-items/${id}`, json(body, "PUT")),
  deleteMenuItem: (rid: number, id: number) =>
    request<void>(`/restaurants/${rid}/menu-items/${id}`, { method: "DELETE" }),

  // -- mappings --------------------------------------------------------------------------
  listMappings: (rid: number, modelId?: number) =>
    request<Page<Mapping>>(
      `/restaurants/${rid}/mappings${toQueryString({ limit: 500, model_id: modelId })}`,
    ),
  createMapping: (rid: number, body: MappingCreate) =>
    request<Mapping>(`/restaurants/${rid}/mappings`, json(body, "POST")),
  updateMapping: (rid: number, id: number, body: MappingUpdate) =>
    request<Mapping>(`/restaurants/${rid}/mappings/${id}`, json(body, "PUT")),
  deleteMapping: (rid: number, id: number) =>
    request<void>(`/restaurants/${rid}/mappings/${id}`, { method: "DELETE" }),

  // -- models ------------------------------------------------------------------------------
  listModels: (rid: number) => request<Page<ModelInfo>>(`/restaurants/${rid}/models${PAGE_ALL}`),
  activateModel: (rid: number, id: number) =>
    request<ModelInfo>(`/restaurants/${rid}/models/${id}/activate`, { method: "POST" }),

  // -- events --------------------------------------------------------------------------------
  listEvents: (rid: number, query: string) => request<Page<EventRecord>>(`/restaurants/${rid}/events${query}`),
  patchEvent: (rid: number, eventId: string, isCanonical: boolean) =>
    request<EventRecord>(
      `/restaurants/${rid}/events/${eventId}`,
      json({ is_canonical: isCanonical }, "PATCH"),
    ),

  // -- data gaps -------------------------------------------------------------------------------
  listDataGaps: (rid: number, params: { from_ts?: number; to_ts?: number; camera_id?: number }) =>
    request<Page<DataGapRecord>>(
      `/restaurants/${rid}/data-gaps${toQueryString({ limit: 100, ...params })}`,
    ),

  // -- stats -----------------------------------------------------------------------------------
  timeseries: (
    rid: number,
    params: { bucket: "hour" | "day"; from_ts?: number; to_ts?: number; group_by?: string },
  ) => request<TimeseriesResponse>(`/restaurants/${rid}/stats/timeseries${toQueryString(params)}`),
  statsSummary: (rid: number) => request<StatsSummary>(`/restaurants/${rid}/stats/summary`),

  // -- reconcile ----------------------------------------------------------------------------------
  reconcile: (rid: number, file: File, date?: string) => {
    const form = new FormData();
    form.append("file", file);
    return request<ReconcileReport>(
      `/restaurants/${rid}/reconcile${toQueryString({ date })}`,
      { method: "POST", body: form },
    );
  },
  listReconcileRuns: (rid: number) =>
    request<Page<ReconcileRun>>(`/restaurants/${rid}/reconcile-runs${PAGE_ALL}`),

  // -- video analysis -------------------------------------------------------------------------------
  listAnalysisJobs: (rid: number) => request<AnalysisJob[]>(`/restaurants/${rid}/analysis`),
  getAnalysisJob: (rid: number, jobId: string) =>
    request<AnalysisJob>(`/restaurants/${rid}/analysis/${jobId}`),
  createAnalysisJob: (rid: number, body: AnalysisJobCreate) => {
    const form = new FormData();
    if (body.file) form.append("file", body.file);
    if (body.url) form.append("url", body.url);
    if (body.backend) form.append("backend", body.backend);
    if (body.line) {
      form.append("x1", String(body.line.x1));
      form.append("y1", String(body.line.y1));
      form.append("x2", String(body.line.x2));
      form.append("y2", String(body.line.y2));
    }
    if (body.count_directions) form.append("count_directions", body.count_directions);
    return request<AnalysisJob>(`/restaurants/${rid}/analysis`, { method: "POST", body: form });
  },
  cancelAnalysisJob: (rid: number, jobId: string) =>
    request<AnalysisJob>(`/restaurants/${rid}/analysis/${jobId}/cancel`, { method: "POST" }),
};

/** URL helpers for media endpoints consumed by <img>/<a> directly.
 * These cannot send headers, so a stored API token rides along as ?token=. */
export const mediaUrl = {
  stream: (cameraId: number) => withToken(`/api/stream/${cameraId}`),
  snapshot: (cameraId: number, cacheBust?: number) =>
    withToken(`/api/snapshot/${cameraId}${cacheBust ? `?t=${cacheBust}` : ""}`),
  eventSnapshot: (rid: number, eventId: string) =>
    withToken(`/api/restaurants/${rid}/events/${eventId}/snapshot`),
  eventsCsv: (rid: number, query: string) =>
    withToken(`/api/restaurants/${rid}/events/export.csv${query}`),
};
