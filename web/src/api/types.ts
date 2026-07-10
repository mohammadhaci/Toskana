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
