"""Pydantic v2 request/response models for the REST API.

Conventions:

* ``*Create`` — POST body (required fields, sensible defaults).
* ``*Update`` — PUT/PATCH body: every field optional; only fields the client
  sent (``exclude_unset``) are applied.
* ``*Read`` — response model, built from ORM rows (``from_attributes=True``).
* ``Page[...]`` — standard paginated list envelope.

Timestamps are integer Unix epoch **milliseconds, UTC** everywhere, matching
the database schema.
"""

from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")

NormCoord = Annotated[float, Field(ge=0.0, le=1.0)]
Direction = Literal["out", "in"]


class APIModel(BaseModel):
    """Base for request/response bodies (frees the ``model_*`` field names)."""

    model_config = ConfigDict(protected_namespaces=())


class ORMModel(APIModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


class Page(APIModel, Generic[T]):
    """Paginated list envelope."""

    items: list[T]
    total: int
    limit: int
    offset: int


# -- restaurants ----------------------------------------------------------


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except Exception as exc:
        raise ValueError(f"unknown IANA timezone: {value!r}") from exc
    return value


class RestaurantCreate(APIModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9\-_]*$")
    name: str = Field(min_length=1, max_length=200)
    timezone: str = "Europe/Vienna"
    locale_default: str = "de"
    settings_json: str | None = None

    _tz = field_validator("timezone")(_validate_timezone)


class RestaurantUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = None
    locale_default: str | None = None
    settings_json: str | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        return None if value is None else _validate_timezone(value)


class RestaurantRead(ORMModel):
    id: int
    slug: str
    name: str
    timezone: str
    locale_default: str
    settings_json: str | None


# -- exit groups ------------------------------------------------------------


class ExitGroupCreate(APIModel):
    name: str = Field(min_length=1, max_length=200)
    dedup_window_ms: int = Field(default=2000, ge=0, le=60_000)
    dedup_strategy: Literal["primary_wins", "first_wins"] = "primary_wins"


class ExitGroupUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    dedup_window_ms: int | None = Field(default=None, ge=0, le=60_000)
    dedup_strategy: Literal["primary_wins", "first_wins"] | None = None


class ExitGroupRead(ORMModel):
    id: int
    restaurant_id: int
    name: str
    dedup_window_ms: int
    dedup_strategy: str


# -- cameras ------------------------------------------------------------------


class CameraCreate(APIModel):
    name: str = Field(min_length=1, max_length=200)
    source_type: Literal["rtsp", "usb", "file"]
    source_url: str = Field(min_length=1, max_length=1000)
    exit_group_id: int | None = None
    is_primary_in_group: bool = False
    target_fps: int = Field(default=15, ge=1, le=120)
    model_id: int | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _usb_source_is_index(self) -> CameraCreate:
        if self.source_type == "usb" and not self.source_url.strip().isdigit():
            raise ValueError("usb source_url must be a numeric device index")
        return self


class CameraUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    source_type: Literal["rtsp", "usb", "file"] | None = None
    source_url: str | None = Field(default=None, min_length=1, max_length=1000)
    exit_group_id: int | None = None
    is_primary_in_group: bool | None = None
    target_fps: int | None = Field(default=None, ge=1, le=120)
    model_id: int | None = None
    enabled: bool | None = None


class CameraRead(ORMModel):
    id: int
    restaurant_id: int
    name: str
    source_type: str
    source_url: str
    exit_group_id: int | None
    is_primary_in_group: bool
    target_fps: int
    model_id: int | None
    enabled: bool


class CameraStatus(APIModel):
    """Live pipeline status for one camera (all-None fields = never started)."""

    camera_id: int
    name: str | None = None
    running: bool = False
    frames: int = 0
    fps: float = 0.0
    last_frame_ts: int | None = None
    gaps: int = 0
    backend: str | None = None
    device: str | None = None
    source_type: str | None = None
    started_ts: int | None = None
    last_error: str | None = None


# -- lines ----------------------------------------------------------------------


class LineCreate(APIModel):
    name: str = Field(default="Line", max_length=200)
    x1: NormCoord
    y1: NormCoord
    x2: NormCoord
    y2: NormCoord
    count_directions: str = "out,in"
    min_track_age: int = Field(default=3, ge=0, le=100)
    hysteresis_px: int = Field(default=12, ge=0, le=200)
    cooldown_ms: int = Field(default=1500, ge=0, le=60_000)
    enabled: bool = True

    @field_validator("count_directions")
    @classmethod
    def _known_directions(cls, value: str) -> str:
        from toskana.vision.line_crossing import parse_count_directions

        parse_count_directions(value)  # raises ValueError on unknown vocabulary
        return value


class LineUpdate(APIModel):
    name: str | None = Field(default=None, max_length=200)
    x1: NormCoord | None = None
    y1: NormCoord | None = None
    x2: NormCoord | None = None
    y2: NormCoord | None = None
    count_directions: str | None = None
    min_track_age: int | None = Field(default=None, ge=0, le=100)
    hysteresis_px: int | None = Field(default=None, ge=0, le=200)
    cooldown_ms: int | None = Field(default=None, ge=0, le=60_000)
    enabled: bool | None = None

    @field_validator("count_directions")
    @classmethod
    def _known_directions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from toskana.vision.line_crossing import parse_count_directions

        parse_count_directions(value)
        return value


class LineRead(ORMModel):
    id: int
    restaurant_id: int
    camera_id: int
    name: str
    x1: float
    y1: float
    x2: float
    y2: float
    count_directions: str
    min_track_age: int
    hysteresis_px: int
    cooldown_ms: int
    enabled: bool


# -- categories & menu items ------------------------------------------------------


class CategoryCreate(APIModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9\-_]*$")
    name_de: str = Field(min_length=1, max_length=200)
    name_en: str = Field(min_length=1, max_length=200)
    color_hex: str = Field(default="#888888", pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
    sort_order: int = 0


class CategoryUpdate(APIModel):
    name_de: str | None = Field(default=None, min_length=1, max_length=200)
    name_en: str | None = Field(default=None, min_length=1, max_length=200)
    color_hex: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
    sort_order: int | None = None


class CategoryRead(ORMModel):
    id: int
    restaurant_id: int
    key: str
    name_de: str
    name_en: str
    color_hex: str
    sort_order: int


class MenuItemCreate(APIModel):
    category_id: int
    name: str = Field(min_length=1, max_length=200)
    price: float | None = Field(default=None, ge=0)
    is_active: bool = True


class MenuItemUpdate(APIModel):
    category_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price: float | None = Field(default=None, ge=0)
    is_active: bool | None = None


class MenuItemRead(ORMModel):
    id: int
    restaurant_id: int
    category_id: int
    name: str
    price: float | None
    is_active: bool


# -- class mappings -------------------------------------------------------------------


class MappingCreate(APIModel):
    model_id: int
    model_class_id: int = Field(ge=0)
    model_class_name: str = Field(min_length=1, max_length=200)
    category_id: int | None = None
    menu_item_id: int | None = None
    min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _has_target(self) -> MappingCreate:
        if self.category_id is None and self.menu_item_id is None:
            raise ValueError("mapping needs category_id or menu_item_id")
        return self


class MappingUpdate(APIModel):
    model_class_id: int | None = Field(default=None, ge=0)
    model_class_name: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: int | None = None
    menu_item_id: int | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class MappingBulkItem(APIModel):
    """One entry of the bulk mapping set for a model (model_id from the path)."""

    model_class_id: int = Field(ge=0)
    model_class_name: str = Field(min_length=1, max_length=200)
    category_id: int | None = None
    menu_item_id: int | None = None
    min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _has_target(self) -> MappingBulkItem:
        if self.category_id is None and self.menu_item_id is None:
            raise ValueError("mapping needs category_id or menu_item_id")
        return self


class MappingRead(ORMModel):
    id: int
    restaurant_id: int
    model_id: int
    model_class_id: int
    model_class_name: str
    category_id: int | None
    menu_item_id: int | None
    min_confidence: float


# -- models registry ---------------------------------------------------------------------


class ModelRead(ORMModel):
    id: int
    restaurant_id: int | None
    name: str
    version: str
    kind: str
    path: str
    classes_json: str
    metrics_json: str | None
    is_active: bool


# -- sessions (shifts) ---------------------------------------------------------------------


class SessionCreate(APIModel):
    name: str = Field(min_length=1, max_length=200)
    started_ts: int | None = None  # default: now
    note: str | None = None


class SessionUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    started_ts: int | None = None
    ended_ts: int | None = None
    note: str | None = None


class SessionRead(ORMModel):
    id: int
    restaurant_id: int
    name: str
    started_ts: int
    ended_ts: int | None
    note: str | None


# -- events ----------------------------------------------------------------------------------


class EventRead(ORMModel):
    id: str
    restaurant_id: int
    camera_id: int
    line_id: int | None
    session_id: int | None
    track_id: int
    category_id: int | None
    menu_item_id: int | None
    raw_class_name: str
    confidence: float
    direction: Direction
    ts: int
    frame_index: int | None
    anchor_x: float
    anchor_y: float
    snapshot_path: str | None
    dedup_group_id: str | None
    is_canonical: bool


class EventPatch(APIModel):
    """Manual dedup review: only the canonical flag is mutable."""

    is_canonical: bool


# -- stats -------------------------------------------------------------------------------------


class TimeseriesRow(APIModel):
    bucket_ts: int  # epoch ms of the local bucket start
    bucket_iso: str  # ISO8601 local bucket start (restaurant timezone)
    group: str | None  # group key value (category/camera id or direction); None = ungrouped
    out: int
    in_: int = Field(serialization_alias="in")
    net: int

    model_config = ConfigDict(populate_by_name=True)


class TimeseriesResponse(APIModel):
    bucket: Literal["hour", "day"]
    group_by: Literal["category", "camera", "direction"] | None
    timezone: str
    from_ts: int | None
    to_ts: int | None
    rows: list[TimeseriesRow]


class CategoryCounter(APIModel):
    category_id: int | None
    key: str | None
    name_de: str | None
    name_en: str | None
    color_hex: str | None
    out: int
    in_: int = Field(serialization_alias="in")
    net: int

    model_config = ConfigDict(populate_by_name=True)


class StatsSummary(APIModel):
    date: str  # local date (restaurant timezone)
    timezone: str
    from_ts: int
    to_ts: int
    categories: list[CategoryCounter]
    total_out: int
    total_in: int
    total_net: int


# -- reconcile ------------------------------------------------------------------------------------


class ReconcileRow(APIModel):
    category_key: str
    category_id: int | None
    date: str
    pos_quantity: float
    counted_out: int
    counted_in: int
    counted_net: int
    variance: float  # counted_net - pos_quantity
    variance_pct: float | None  # None when pos_quantity == 0
    unknown_category: bool = False


class ReconcileReport(APIModel):
    restaurant_id: int
    default_date: str
    timezone: str
    rows: list[ReconcileRow]
    total_pos_quantity: float
    total_counted_net: int


# -- system ----------------------------------------------------------------------------------------


class SystemHealth(APIModel):
    status: Literal["ok", "degraded"]
    db_ok: bool
    pipelines: list[CameraStatus]
    writer_written_events: int
    writer_written_gaps: int
    #: M8 cross-camera dedup (active only with a >=2-camera exit group).
    dedup_active: bool = False
    dedup_matches: int = 0
    dedup_demotions: int = 0


class SystemInfo(APIModel):
    version: str
    active_restaurant_slug: str
    active_restaurant: RestaurantRead | None
    db_path: str
    db_size_bytes: int | None
    torch_available: bool
    cuda_available: bool
    detector_backend: str
    device: str
    loop_file_sources: bool
    snapshots_dir: str


class MessageResponse(APIModel):
    detail: str


AnyPayload = dict[str, Any]
