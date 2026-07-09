"""Idempotent demo seed: restaurant, categories, cameras, exit group, lines,
shared pretrained model and its class mappings.

Re-running the seed never creates duplicates; existing rows are left as-is
(natural keys: restaurant slug, category key, camera name, model name+version,
mapping (model, class, restaurant)).
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from toskana.db.models import (
    Camera,
    Category,
    ClassMapping,
    ExitGroup,
    Line,
    ModelRegistry,
    Restaurant,
)

# COCO class names in YOLO (ultralytics) order; index = model_class_id.
COCO_CLASSES: list[str] = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]

CATEGORIES: list[dict[str, object]] = [
    {
        "key": "drink",
        "name_de": "Getränk",
        "name_en": "Drink",
        "color_hex": "#2E86DE",
        "sort_order": 0,
    },
    {
        "key": "coffee",
        "name_de": "Kaffee",
        "name_en": "Coffee",
        "color_hex": "#8B5A2B",
        "sort_order": 1,
    },
    {
        "key": "main",
        "name_de": "Hauptgericht",
        "name_en": "Main course",
        "color_hex": "#E67E22",
        "sort_order": 2,
    },
    {
        "key": "starter",
        "name_de": "Vorspeise",
        "name_en": "Starter",
        "color_hex": "#27AE60",
        "sort_order": 3,
    },
    {
        "key": "dessert",
        "name_de": "Nachspeise",
        "name_en": "Dessert",
        "color_hex": "#C0398B",
        "sort_order": 4,
    },
    {
        "key": "side",
        "name_de": "Beilage",
        "name_en": "Side dish",
        "color_hex": "#F1C40F",
        "sort_order": 5,
    },
]

# COCO class name -> category key (Phase 1 generic mapping).
CLASS_TO_CATEGORY: dict[str, str] = {
    "cup": "drink",
    "wine glass": "drink",
    "bottle": "drink",
    "bowl": "main",
    "pizza": "main",
    "sandwich": "main",
    "hot dog": "main",
    "cake": "dessert",
    "donut": "dessert",
    "banana": "side",
    "apple": "side",
    "orange": "side",
}

DEFAULT_MIN_CONFIDENCE = 0.35


def seed(session: Session) -> Restaurant:
    """Seed the demo restaurant. Idempotent: re-running creates no duplicates."""
    restaurant = session.scalar(select(Restaurant).where(Restaurant.slug == "toskana"))
    if restaurant is None:
        restaurant = Restaurant(
            slug="toskana",
            name="Trattoria Toskana",
            timezone="Europe/Vienna",
            locale_default="de",
        )
        session.add(restaurant)
        session.flush()

    categories: dict[str, Category] = {}
    for spec in CATEGORIES:
        key = str(spec["key"])
        cat = session.scalar(
            select(Category).where(Category.restaurant_id == restaurant.id, Category.key == key)
        )
        if cat is None:
            cat = Category(restaurant_id=restaurant.id, **spec)  # type: ignore[arg-type]
            session.add(cat)
            session.flush()
        categories[key] = cat

    exit_group = session.scalar(
        select(ExitGroup).where(
            ExitGroup.restaurant_id == restaurant.id, ExitGroup.name == "Pass 1"
        )
    )
    if exit_group is None:
        exit_group = ExitGroup(
            restaurant_id=restaurant.id,
            name="Pass 1",
            dedup_window_ms=2000,
            dedup_strategy="primary_wins",
        )
        session.add(exit_group)
        session.flush()

    camera_specs = [
        {"name": "Pass links", "source_url": "./data/videos/pass_left.mp4", "primary": True},
        {"name": "Pass rechts", "source_url": "./data/videos/pass_right.mp4", "primary": False},
    ]
    cameras: list[Camera] = []
    for spec in camera_specs:
        cam = session.scalar(
            select(Camera).where(Camera.restaurant_id == restaurant.id, Camera.name == spec["name"])
        )
        if cam is None:
            cam = Camera(
                restaurant_id=restaurant.id,
                name=str(spec["name"]),
                source_type="file",
                source_url=str(spec["source_url"]),
                exit_group_id=exit_group.id,
                is_primary_in_group=bool(spec["primary"]),
                target_fps=15,
            )
            session.add(cam)
            session.flush()
        cameras.append(cam)

    line_coords = [
        (0.20, 0.15, 0.85, 0.80),  # camera 1: diagonal-ish
        (0.15, 0.75, 0.80, 0.20),  # camera 2: other diagonal
    ]
    for cam, (x1, y1, x2, y2) in zip(cameras, line_coords, strict=True):
        line = session.scalar(select(Line).where(Line.camera_id == cam.id))
        if line is None:
            session.add(
                Line(
                    restaurant_id=restaurant.id,
                    camera_id=cam.id,
                    name=f"{cam.name} — Pass-Linie",
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    count_directions="out,in",
                )
            )
    session.flush()

    model = session.scalar(
        select(ModelRegistry).where(
            ModelRegistry.restaurant_id.is_(None),
            ModelRegistry.name == "yolov8n",
            ModelRegistry.version == "coco",
        )
    )
    if model is None:
        model = ModelRegistry(
            restaurant_id=None,  # shared across all restaurants
            name="yolov8n",
            version="coco",
            kind="pretrained",
            path="yolov8n.pt",
            classes_json=json.dumps(COCO_CLASSES),
            is_active=True,
        )
        session.add(model)
        session.flush()

    for class_name, category_key in CLASS_TO_CATEGORY.items():
        class_id = COCO_CLASSES.index(class_name)
        mapping = session.scalar(
            select(ClassMapping).where(
                ClassMapping.model_id == model.id,
                ClassMapping.model_class_id == class_id,
                ClassMapping.restaurant_id == restaurant.id,
            )
        )
        if mapping is None:
            session.add(
                ClassMapping(
                    restaurant_id=restaurant.id,
                    model_id=model.id,
                    model_class_id=class_id,
                    model_class_name=class_name,
                    category_id=categories[category_key].id,
                    min_confidence=DEFAULT_MIN_CONFIDENCE,
                )
            )

    session.commit()
    return restaurant
