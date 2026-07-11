"""Multi-tenant ORM schema.

Every tenant-owned table carries ``restaurant_id`` so a future central
aggregation across branches stays possible (ULID event PKs, UTC timestamps).

Timestamps are stored as integer Unix epoch **milliseconds in UTC**
(columns suffixed ``_ts`` or named ``ts``).

Line coordinates are stored **normalized to 0..1** relative to the camera
frame so they survive resolution changes.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from toskana.db.base import Base


class Restaurant(Base):
    """A tenant: one restaurant (site) with its own menu, cameras and models."""

    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Vienna")
    locale_default: Mapped[str] = mapped_column(String(8), default="de")
    settings_json: Mapped[str | None] = mapped_column(Text, default=None)

    cameras: Mapped[list[Camera]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    exit_groups: Mapped[list[ExitGroup]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    categories: Mapped[list[Category]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    menu_items: Mapped[list[MenuItem]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    class_mappings: Mapped[list[ClassMapping]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    events: Mapped[list[Event]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[ServiceSession]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Restaurant {self.slug!r}>"


class ExitGroup(Base):
    """A physical exit covered by one or more cameras; scope of dedup."""

    __tablename__ = "exit_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    dedup_window_ms: Mapped[int] = mapped_column(Integer, default=2000)
    dedup_strategy: Mapped[str] = mapped_column(String(32), default="primary_wins")

    restaurant: Mapped[Restaurant] = relationship(back_populates="exit_groups")
    cameras: Mapped[list[Camera]] = relationship(back_populates="exit_group")

    __table_args__ = (
        CheckConstraint(
            "dedup_strategy IN ('primary_wins', 'first_wins')",
            name="ck_exit_groups_strategy",
        ),
    )


class Camera(Base):
    """A video source (RTSP stream, USB device, or video file)."""

    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(16))  # rtsp | usb | file
    source_url: Mapped[str] = mapped_column(String(1000))
    exit_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("exit_groups.id", ondelete="SET NULL"), default=None, index=True
    )
    is_primary_in_group: Mapped[bool] = mapped_column(Boolean, default=False)
    target_fps: Mapped[int] = mapped_column(Integer, default=15)
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("models_registry.id", ondelete="SET NULL"), default=None
    )  # per-camera model override; NULL = restaurant's active model
    detector_backend: Mapped[str | None] = mapped_column(
        String(16), default=None
    )  # per-camera override: synthetic | yolo; NULL = resolved global default
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    restaurant: Mapped[Restaurant] = relationship(back_populates="cameras")
    exit_group: Mapped[ExitGroup] = relationship(back_populates="cameras")
    lines: Mapped[list[Line]] = relationship(back_populates="camera", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("source_type IN ('rtsp', 'usb', 'file')", name="ck_cameras_source_type"),
    )


class Line(Base):
    """A virtual counting line on a camera image.

    Coordinates are normalized 0..1 relative to frame width/height so they
    survive resolution changes. Direction semantics: crossing from the A→B
    left side to the right side is ``out`` (kitchen → customers), the other
    way is ``in`` (returns).
    """

    __tablename__ = "lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Line")
    x1: Mapped[float] = mapped_column(Float)
    y1: Mapped[float] = mapped_column(Float)
    x2: Mapped[float] = mapped_column(Float)
    y2: Mapped[float] = mapped_column(Float)
    count_directions: Mapped[str] = mapped_column(String(16), default="out,in")
    # Per-line tuning knobs for the crossing state machine.
    min_track_age: Mapped[int] = mapped_column(Integer, default=3)
    hysteresis_px: Mapped[int] = mapped_column(Integer, default=12)  # at 640px reference width
    cooldown_ms: Mapped[int] = mapped_column(Integer, default=1500)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    camera: Mapped[Camera] = relationship(back_populates="lines")
    restaurant: Mapped[Restaurant] = relationship()

    __table_args__ = (
        CheckConstraint("x1 >= 0 AND x1 <= 1", name="ck_lines_x1"),
        CheckConstraint("y1 >= 0 AND y1 <= 1", name="ck_lines_y1"),
        CheckConstraint("x2 >= 0 AND x2 <= 1", name="ck_lines_x2"),
        CheckConstraint("y2 >= 0 AND y2 <= 1", name="ck_lines_y2"),
    )


class Category(Base):
    """Phase-1 generic category (drink, main, dessert, ...) per restaurant."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    name_de: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200))
    color_hex: Mapped[str] = mapped_column(String(9), default="#888888")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    restaurant: Mapped[Restaurant] = relationship(back_populates="categories")
    menu_items: Mapped[list[MenuItem]] = relationship(back_populates="category")

    __table_args__ = (
        UniqueConstraint("restaurant_id", "key", name="uq_categories_restaurant_key"),
    )


class MenuItem(Base):
    """Phase-2 named menu item, belonging to a category."""

    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    price: Mapped[float | None] = mapped_column(Float, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    restaurant: Mapped[Restaurant] = relationship(back_populates="menu_items")
    category: Mapped[Category] = relationship(back_populates="menu_items")

    __table_args__ = (
        UniqueConstraint("restaurant_id", "name", name="uq_menu_items_restaurant_name"),
    )


class ModelRegistry(Base):
    """Registry of detection models. ``restaurant_id`` NULL = shared/pretrained."""

    __tablename__ = "models_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), default=None, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(64), default="1")
    kind: Mapped[str] = mapped_column(String(16))  # pretrained | finetuned
    path: Mapped[str] = mapped_column(String(1000))
    classes_json: Mapped[str] = mapped_column(Text)  # JSON list: class id -> name
    metrics_json: Mapped[str | None] = mapped_column(Text, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    class_mappings: Mapped[list[ClassMapping]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("kind IN ('pretrained', 'finetuned')", name="ck_models_registry_kind"),
        UniqueConstraint(
            "restaurant_id", "name", "version", name="uq_models_registry_owner_name_version"
        ),
    )


class ClassMapping(Base):
    """Maps a model output class to a category OR a menu item for a restaurant.

    Swapping the active model atomically swaps the mapping set in effect.
    """

    __tablename__ = "class_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("models_registry.id", ondelete="CASCADE"), index=True
    )
    model_class_id: Mapped[int] = mapped_column(Integer)
    model_class_name: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), default=None
    )
    menu_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), default=None
    )
    min_confidence: Mapped[float] = mapped_column(Float, default=0.35)

    restaurant: Mapped[Restaurant] = relationship(back_populates="class_mappings")
    model: Mapped[ModelRegistry] = relationship(back_populates="class_mappings")
    category: Mapped[Category] = relationship()
    menu_item: Mapped[MenuItem] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "model_id", "model_class_id", "restaurant_id", name="uq_class_mappings_model_class"
        ),
        CheckConstraint(
            "(category_id IS NOT NULL) OR (menu_item_id IS NOT NULL)",
            name="ck_class_mappings_target",
        ),
    )


class ServiceSession(Base):
    """A service session / shift used to slice statistics."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    started_ts: Mapped[int] = mapped_column(Integer)  # UTC epoch ms
    ended_ts: Mapped[int | None] = mapped_column(Integer, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)

    restaurant: Mapped[Restaurant] = relationship(back_populates="sessions")


class Event(Base):
    """Append-only crossing log. Duplicates are kept, never deleted.

    Cross-camera duplicates within an exit group share ``dedup_group_id``;
    exactly one event per group is ``is_canonical`` (auditable, re-tunable).
    """

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # ULID
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"))
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("lines.id", ondelete="SET NULL"), default=None
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), default=None, index=True
    )
    track_id: Mapped[int] = mapped_column(Integer)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), default=None
    )
    menu_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_items.id", ondelete="SET NULL"), default=None
    )
    raw_class_name: Mapped[str] = mapped_column(String(200))
    confidence: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(8))  # out | in
    ts: Mapped[int] = mapped_column(Integer)  # UTC epoch ms
    frame_index: Mapped[int | None] = mapped_column(Integer, default=None)
    anchor_x: Mapped[float] = mapped_column(Float)  # normalized 0..1
    anchor_y: Mapped[float] = mapped_column(Float)
    snapshot_path: Mapped[str | None] = mapped_column(String(1000), default=None)
    dedup_group_id: Mapped[str | None] = mapped_column(String(26), default=None)
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=True)
    #: AI Event Refiner (vision LLM): True once the event was verified.
    refined: Mapped[bool] = mapped_column(Boolean, default=False)
    #: short audit note: backend/model + original category + verdict.
    refiner_note: Mapped[str | None] = mapped_column(Text, default=None)

    restaurant: Mapped[Restaurant] = relationship(back_populates="events")
    camera: Mapped[Camera] = relationship()
    line: Mapped[Line] = relationship()
    category: Mapped[Category] = relationship()
    menu_item: Mapped[MenuItem] = relationship()
    session: Mapped[ServiceSession] = relationship()

    @property
    def menu_item_name(self) -> str | None:
        """Denormalized item name for API serialization (Phase 2)."""
        return self.menu_item.name if self.menu_item is not None else None

    __table_args__ = (
        CheckConstraint("direction IN ('out', 'in')", name="ck_events_direction"),
        Index("ix_events_restaurant_ts", "restaurant_id", "ts"),
        Index("ix_events_camera_ts", "camera_id", "ts"),
        Index("ix_events_dedup_group", "dedup_group_id"),
        Index("ix_events_restaurant_canonical_ts", "restaurant_id", "is_canonical", "ts"),
    )


class DataGap(Base):
    """A recorded interval where a camera produced no data (outage, startup)."""

    __tablename__ = "data_gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    from_ts: Mapped[int] = mapped_column(Integer)  # UTC epoch ms
    to_ts: Mapped[int | None] = mapped_column(Integer, default=None)  # NULL = ongoing
    reason: Mapped[str] = mapped_column(String(200))

    camera: Mapped[Camera] = relationship()

    __table_args__ = (Index("ix_data_gaps_camera_from_ts", "camera_id", "from_ts"),)


class ReconcileRun(Base):
    """A persisted POS reconciliation report (upload + computed variances)."""

    __tablename__ = "reconcile_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # ULID (creation-ordered)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[str] = mapped_column(String(10))  # default local date (YYYY-MM-DD)
    uploaded_filename: Mapped[str | None] = mapped_column(String(255), default=None)
    rows_json: Mapped[str] = mapped_column(Text)  # JSON list of ReconcileRow dicts
    total_pos_quantity: Mapped[float] = mapped_column(Float)
    total_counted_net: Mapped[int] = mapped_column(Integer)
    created_ts: Mapped[int] = mapped_column(Integer)  # UTC epoch ms

    restaurant: Mapped[Restaurant] = relationship()

    __table_args__ = (Index("ix_reconcile_runs_restaurant_created", "restaurant_id", "created_ts"),)


class CountingEvalRun(Base):
    """Result of an evaluation campaign against ground truth."""

    __tablename__ = "counting_eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    created_ts: Mapped[int] = mapped_column(Integer)  # UTC epoch ms
    name: Mapped[str] = mapped_column(String(200))
    scenario: Mapped[str | None] = mapped_column(String(200), default=None)
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("models_registry.id", ondelete="SET NULL"), default=None
    )
    ground_truth_path: Mapped[str | None] = mapped_column(String(1000), default=None)
    video_ref: Mapped[str | None] = mapped_column(String(2000), default=None)
    config_json: Mapped[str | None] = mapped_column(Text, default=None)  # CLI/backend/line config
    gt_counts_json: Mapped[str | None] = mapped_column(Text, default=None)
    measured_counts_json: Mapped[str | None] = mapped_column(Text, default=None)
    metrics_json: Mapped[str] = mapped_column(Text)  # precision/recall/MAE per category & hour
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    model: Mapped[ModelRegistry] = relationship()
