/** Hand-written mirrors of `src/toskana/api/schemas.py`.
 *
 * Timestamps are integer Unix epoch **milliseconds, UTC** everywhere.
 * Fields serialized with an alias in Python (`in_` -> `"in"`) use the wire
 * name here.
 */

export type Direction = "out" | "in";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// -- restaurants -----------------------------------------------------------

export interface Restaurant {
  id: number;
  slug: string;
  name: string;
  timezone: string;
  locale_default: string;
  settings_json: string | null;
}

export interface RestaurantCreate {
  slug: string;
  name: string;
  timezone?: string;
  locale_default?: string;
  settings_json?: string | null;
}

export type RestaurantUpdate = Partial<Omit<RestaurantCreate, "slug">>;

// -- exit groups -----------------------------------------------------------

export type DedupStrategy = "primary_wins" | "first_wins";

export interface ExitGroup {
  id: number;
  restaurant_id: number;
  name: string;
  dedup_window_ms: number;
  dedup_strategy: DedupStrategy;
}

export interface ExitGroupCreate {
  name: string;
  dedup_window_ms?: number;
  dedup_strategy?: DedupStrategy;
}

export type ExitGroupUpdate = Partial<ExitGroupCreate>;

// -- cameras ---------------------------------------------------------------

export type SourceType = "rtsp" | "usb" | "file";

export interface Camera {
  id: number;
  restaurant_id: number;
  name: string;
  source_type: SourceType;
  source_url: string;
  exit_group_id: number | null;
  is_primary_in_group: boolean;
  target_fps: number;
  model_id: number | null;
  enabled: boolean;
}

export interface CameraCreate {
  name: string;
  source_type: SourceType;
  source_url: string;
  exit_group_id?: number | null;
  is_primary_in_group?: boolean;
  target_fps?: number;
  model_id?: number | null;
  enabled?: boolean;
}

export type CameraUpdate = Partial<CameraCreate>;

/** One vendor entry of the wizard dropdown (GET /camera-presets). */
export interface CameraPreset {
  key: string;
  label: string;
  default_port: number;
  needs_channel: boolean;
  /** {user}/{password}/{ip}/{port}/{channel} template; null = manual URL. */
  url_template: string | null;
}

/** Body of POST .../cameras/test-source: raw source OR vendor-preset fields.
 * Preset fields let the server build the URL so the browser never has to
 * assemble (or encode) the password itself. */
export interface CameraTestSourceRequest {
  source_type?: SourceType;
  source_url?: string;
  preset_key?: string;
  ip?: string;
  username?: string;
  password?: string;
  port?: number | null;
  channel?: number;
}

/** Probe outcome — always HTTP 200; a failed probe is `ok: false`. */
export interface CameraTestResult {
  ok: boolean;
  source_type: string;
  /** Probed URL with the password masked (safe to display). */
  source_url_masked: string | null;
  /** On success: the full URL (server-built credentials) to store on save. */
  source_url: string | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  snapshot_b64: string | null;
  error: string | null;
}

export interface CameraStatus {
  camera_id: number;
  name: string | null;
  running: boolean;
  frames: number;
  fps: number;
  last_frame_ts: number | null;
  gaps: number;
  backend: string | null;
  device: string | null;
  source_type: string | null;
  started_ts: number | null;
  last_error: string | null;
  /** M10 drift watchdog: null = not calibrated yet / camera never started. */
  drift_ok: boolean | null;
  drift_score: number | null;
}

// -- lines -----------------------------------------------------------------

export interface Line {
  id: number;
  restaurant_id: number;
  camera_id: number;
  name: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  count_directions: string;
  min_track_age: number;
  hysteresis_px: number;
  cooldown_ms: number;
  enabled: boolean;
}

export interface LineCreate {
  name?: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  count_directions?: string;
  min_track_age?: number;
  hysteresis_px?: number;
  cooldown_ms?: number;
  enabled?: boolean;
}

export type LineUpdate = Partial<LineCreate>;

// -- categories & menu items -------------------------------------------------

export interface Category {
  id: number;
  restaurant_id: number;
  key: string;
  name_de: string;
  name_en: string;
  color_hex: string;
  sort_order: number;
}

export interface CategoryCreate {
  key: string;
  name_de: string;
  name_en: string;
  color_hex?: string;
  sort_order?: number;
}

export type CategoryUpdate = Partial<Omit<CategoryCreate, "key">>;

export interface MenuItem {
  id: number;
  restaurant_id: number;
  category_id: number;
  name: string;
  price: number | null;
  is_active: boolean;
}

export interface MenuItemCreate {
  category_id: number;
  name: string;
  price?: number | null;
  is_active?: boolean;
}

export type MenuItemUpdate = Partial<MenuItemCreate>;

// -- class mappings ----------------------------------------------------------

export interface Mapping {
  id: number;
  restaurant_id: number;
  model_id: number;
  model_class_id: number;
  model_class_name: string;
  category_id: number | null;
  menu_item_id: number | null;
  min_confidence: number;
}

export interface MappingCreate {
  model_id: number;
  model_class_id: number;
  model_class_name: string;
  category_id?: number | null;
  menu_item_id?: number | null;
  min_confidence?: number;
}

export type MappingUpdate = Partial<Omit<MappingCreate, "model_id">>;

// -- models registry ----------------------------------------------------------

export interface ModelInfo {
  id: number;
  restaurant_id: number | null;
  name: string;
  version: string;
  kind: string;
  path: string;
  classes_json: string;
  metrics_json: string | null;
  is_active: boolean;
}

// -- events ---------------------------------------------------------------------

export interface EventRecord {
  id: string;
  restaurant_id: number;
  camera_id: number;
  line_id: number | null;
  session_id: number | null;
  track_id: number;
  category_id: number | null;
  menu_item_id: number | null;
  menu_item_name: string | null;
  raw_class_name: string;
  confidence: number;
  direction: Direction;
  ts: number;
  frame_index: number | null;
  anchor_x: number;
  anchor_y: number;
  snapshot_path: string | null;
  dedup_group_id: string | null;
  is_canonical: boolean;
}

// -- stats ------------------------------------------------------------------------

export type Bucket = "hour" | "day";
export type GroupBy = "category" | "camera" | "direction" | "menu_item";

export interface TimeseriesRow {
  bucket_ts: number;
  bucket_iso: string;
  group: string | null;
  out: number;
  in: number;
  net: number;
}

export interface TimeseriesResponse {
  bucket: Bucket;
  group_by: GroupBy | null;
  timezone: string;
  from_ts: number | null;
  to_ts: number | null;
  rows: TimeseriesRow[];
}

export interface CategoryCounter {
  category_id: number | null;
  key: string | null;
  name_de: string | null;
  name_en: string | null;
  color_hex: string | null;
  out: number;
  in: number;
  net: number;
}

export interface MenuItemCounter {
  menu_item_id: number;
  name: string;
  category_id: number | null;
  out: number;
  in: number;
  net: number;
}

export interface StatsSummary {
  date: string;
  timezone: string;
  from_ts: number;
  to_ts: number;
  categories: CategoryCounter[];
  /** Phase 2: only menu items with canonical events today. */
  menu_items: MenuItemCounter[];
  total_out: number;
  total_in: number;
  total_net: number;
  /** Data gaps overlapping the day: > 0 means today's counts may be incomplete. */
  gaps_count: number;
}

// -- data gaps ------------------------------------------------------------------------

export interface DataGapRecord {
  id: number;
  restaurant_id: number;
  camera_id: number;
  from_ts: number;
  to_ts: number | null; // null = ongoing
  reason: string;
}

// -- reconcile ------------------------------------------------------------------------

export interface ReconcileRow {
  category_key: string;
  category_id: number | null;
  date: string;
  pos_quantity: number;
  counted_out: number;
  counted_in: number;
  counted_net: number;
  variance: number;
  variance_pct: number | null;
  unknown_category: boolean;
}

export interface ReconcileReport {
  restaurant_id: number;
  default_date: string;
  timezone: string;
  rows: ReconcileRow[];
  total_pos_quantity: number;
  total_counted_net: number;
  /** id of the persisted reconcile_runs row (report history). */
  run_id: string | null;
}

export interface ReconcileRun {
  id: string;
  restaurant_id: number;
  date: string;
  uploaded_filename: string | null;
  rows: ReconcileRow[];
  total_pos_quantity: number;
  total_counted_net: number;
  created_ts: number;
}

// -- video analysis --------------------------------------------------------------------

export type AnalysisStatus =
  | "queued"
  | "downloading"
  | "running"
  | "done"
  | "error"
  | "cancelled";

export type AnalysisBackend = "yolo" | "synthetic";

/** One in-memory analysis job (jobs do not survive a server restart). */
export interface AnalysisJob {
  id: string;
  restaurant_id: number;
  status: AnalysisStatus;
  video_name: string;
  source_url: string | null;
  backend: string;
  /** Camera/line rows the job counts through (null until the video is ready). */
  camera_id: number | null;
  line_id: number | null;
  frames_done: number;
  frames_total: number | null;
  /** counts[direction][class_name] with direction in {out, in}. */
  counts: Record<string, Record<string, number>>;
  total_out: number;
  total_in: number;
  error: string | null;
  created_ts: number;
  finished_ts: number | null;
}

/** Multipart form for POST /restaurants/{rid}/analysis (file XOR url). */
export interface AnalysisJobCreate {
  file?: File;
  url?: string;
  backend?: AnalysisBackend;
  line?: { x1: number; y1: number; x2: number; y2: number };
  count_directions?: string;
}

// -- system --------------------------------------------------------------------------

export interface SystemHealth {
  status: "ok" | "degraded";
  db_ok: boolean;
  pipelines: CameraStatus[];
  writer_written_events: number;
  writer_written_gaps: number;
  /** Present once the M8 dedup engine is wired (optional for forward-compat). */
  dedup_active?: boolean;
  dedup_matches?: number;
  dedup_demotions?: number;
}

export interface SystemInfo {
  version: string;
  active_restaurant_slug: string;
  active_restaurant: Restaurant | null;
  db_path: string;
  db_size_bytes: number | null;
  torch_available: boolean;
  cuda_available: boolean;
  detector_backend: string;
  device: string;
  loop_file_sources: boolean;
  snapshots_dir: string;
  snapshot_retention_days: number;
}

export interface RetentionRunResult {
  snapshot_retention_days: number;
  cutoff_ms: number;
  deleted_snapshots: number;
  cleared_events: number;
  orphans_removed: number;
  removed_dirs: number;
  runs: number;
}

// -- WS /ws/live message shapes ----------------------------------------------------------

export interface LiveCounter {
  category_id: number | null;
  key: string | null;
  name_de: string | null;
  name_en: string | null;
  color_hex: string | null;
  out: number;
  in: number;
  net: number;
}

export interface LiveCrossingEvent {
  id: string;
  restaurant_id: number;
  camera_id: number;
  line_id: number | null;
  track_id: number;
  category_id: number | null;
  menu_item_id: number | null;
  menu_item_name?: string | null;
  raw_class_name: string;
  class_name?: string;
  confidence: number;
  direction: Direction;
  ts: number;
  frame_index: number | null;
  anchor_x: number;
  anchor_y: number;
  snapshot_path: string | null;
}

export interface LiveGap {
  restaurant_id: number;
  camera_id: number;
  from_ts: number;
  to_ts: number;
  reason: string;
}

export type LiveMessage =
  | { type: "hello"; counters: LiveCounter[] }
  | { type: "crossing"; event: LiveCrossingEvent }
  | { type: "gap"; gap: LiveGap }
  | { type: string; [key: string]: unknown };
