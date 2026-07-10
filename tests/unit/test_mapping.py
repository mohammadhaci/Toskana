from __future__ import annotations

from types import SimpleNamespace

from toskana.vision.detector import Detection
from toskana.vision.mapping import ClassMappingResolver, MappingRule, MenuItemInfo, Resolution

RULES = [
    MappingRule(model_class_id=41, model_class_name="cup", category_id=1, min_confidence=0.35),
    MappingRule(model_class_id=53, model_class_name="pizza", category_id=3, min_confidence=0.5),
    MappingRule(
        model_class_id=55,
        model_class_name="cake",
        category_id=5,
        menu_item_id=77,
        min_confidence=0.35,
    ),
]


def test_happy_path_resolves_to_category() -> None:
    resolver = ClassMappingResolver(RULES)
    resolution = resolver.resolve(41, "cup", 0.9)
    assert resolution == Resolution(
        status="mapped",
        raw_class_id=41,
        raw_class_name="cup",
        confidence=0.9,
        category_id=1,
        menu_item_id=None,
    )
    assert resolution.is_countable


def test_menu_item_mapping_resolves() -> None:
    resolution = ClassMappingResolver(RULES).resolve(55, "cake", 0.8)
    assert resolution.status == "mapped"
    assert resolution.category_id == 5
    assert resolution.menu_item_id == 77


def test_below_per_mapping_threshold_is_ignored() -> None:
    resolver = ClassMappingResolver(RULES)
    resolution = resolver.resolve(53, "pizza", 0.45)  # pizza requires 0.5
    assert resolution.status == "ignored"
    assert resolution.category_id is None
    assert resolution.menu_item_id is None
    assert not resolution.is_countable
    # The same confidence passes the default threshold of another class.
    assert resolver.resolve(41, "cup", 0.45).status == "mapped"


def test_unmapped_class_is_representable_not_dropped() -> None:
    resolution = ClassMappingResolver(RULES).resolve(0, "person", 0.99)
    assert resolution.status == "unmapped"
    assert resolution.category_id is None  # event persists with NULL category
    assert resolution.menu_item_id is None
    assert resolution.raw_class_name == "person"
    assert resolution.is_countable  # never silently dropped


def test_rules_from_plain_dict_rows() -> None:
    resolver = ClassMappingResolver(
        [
            {
                "model_class_id": 7,
                "model_class_name": "bowl",
                "category_id": 2,
                "menu_item_id": None,
                "min_confidence": 0.4,
            }
        ]
    )
    assert resolver.resolve(7, "bowl", 0.5).category_id == 2
    assert resolver.resolve(7, "bowl", 0.39).status == "ignored"


def test_rules_from_attribute_rows() -> None:
    # Mimics a class_mappings ORM row without coupling to SQLAlchemy.
    row = SimpleNamespace(
        model_class_id=9,
        model_class_name="bottle",
        category_id=4,
        menu_item_id=None,
        min_confidence=0.35,
    )
    assert ClassMappingResolver([row]).resolve(9, "bottle", 0.6).category_id == 4


def test_name_fallback_when_class_id_differs() -> None:
    # A retrained model may renumber classes; the name still resolves.
    resolution = ClassMappingResolver(RULES).resolve(99, "cup", 0.9)
    assert resolution.status == "mapped"
    assert resolution.category_id == 1


def test_resolve_detection_helper() -> None:
    detection = Detection(x1=0, y1=0, x2=10, y2=10, class_id=41, class_name="cup", confidence=0.7)
    resolution = ClassMappingResolver(RULES).resolve_detection(detection)
    assert resolution.status == "mapped"
    assert resolution.confidence == 0.7


# -- Phase 2 fallback chain (critic requirement #10) ----------------------------

ITEM_RULES = [
    # Menu-item target only: the category must be derived from the item.
    MappingRule(model_class_id=0, model_class_name="drink", menu_item_id=7, min_confidence=0.35),
    # Category target only: Phase-1 behaviour, no item attribution.
    MappingRule(model_class_id=1, model_class_name="main", category_id=3, min_confidence=0.35),
]

MENU_ITEMS = [
    MenuItemInfo(menu_item_id=7, category_id=1, name="Aperol Spritz"),
    MenuItemInfo(menu_item_id=8, category_id=3, name="Wiener Schnitzel"),
]


def test_menu_item_target_derives_category_and_name() -> None:
    resolver = ClassMappingResolver(ITEM_RULES, menu_items=MENU_ITEMS)
    resolution = resolver.resolve(0, "drink", 0.9)
    assert resolution.status == "mapped"
    assert resolution.menu_item_id == 7
    assert resolution.menu_item_name == "Aperol Spritz"
    assert resolution.category_id == 1  # derived via menu_items.category_id


def test_menu_item_target_respects_min_confidence() -> None:
    resolver = ClassMappingResolver(ITEM_RULES, menu_items=MENU_ITEMS)
    resolution = resolver.resolve(0, "drink", 0.2)  # below 0.35
    assert resolution.status == "ignored"
    assert not resolution.is_countable
    assert resolution.menu_item_id is None
    assert resolution.category_id is None


def test_category_fallback_when_no_item_mapping_exists() -> None:
    resolver = ClassMappingResolver(ITEM_RULES, menu_items=MENU_ITEMS)
    resolution = resolver.resolve(1, "main", 0.9)
    assert resolution.status == "mapped"
    assert resolution.category_id == 3  # category-only mapping stays Phase 1
    assert resolution.menu_item_id is None
    assert resolution.menu_item_name is None


def test_explicit_category_on_item_rule_wins_over_derivation() -> None:
    rule = MappingRule(model_class_id=2, model_class_name="dessert", category_id=99, menu_item_id=8)
    resolver = ClassMappingResolver([rule], menu_items=MENU_ITEMS)
    resolution = resolver.resolve(2, "dessert", 0.9)
    assert resolution.category_id == 99  # not overridden by the item's category (3)
    assert resolution.menu_item_id == 8
    assert resolution.menu_item_name == "Wiener Schnitzel"


def test_unknown_menu_item_id_keeps_category_none() -> None:
    # Item referenced by the mapping is missing from the lookup: never crash,
    # keep the item id on the event, leave the category NULL.
    resolver = ClassMappingResolver(ITEM_RULES, menu_items=())
    resolution = resolver.resolve(0, "drink", 0.9)
    assert resolution.status == "mapped"
    assert resolution.menu_item_id == 7
    assert resolution.menu_item_name is None
    assert resolution.category_id is None


def test_menu_items_from_orm_like_rows() -> None:
    # MenuItem ORM rows carry ``id`` (not ``menu_item_id``); from_row adapts.
    row = SimpleNamespace(id=12, category_id=4, name="Tiramisu")
    resolver = ClassMappingResolver(
        [MappingRule(model_class_id=5, model_class_name="dessert", menu_item_id=12)],
        menu_items=[row],
    )
    resolution = resolver.resolve(5, "dessert", 0.9)
    assert resolution.category_id == 4
    assert resolution.menu_item_name == "Tiramisu"
