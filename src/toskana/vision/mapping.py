"""Resolve raw model classes to categories / menu items.

Works on plain rules (dicts, dataclasses or any attribute-bearing rows such
as ``class_mappings`` ORM objects) so it stays decoupled from the SQLAlchemy
session. Resolution never silently drops a detection:

* ``mapped``   — a mapping exists and confidence >= its ``min_confidence``.
* ``ignored``  — a mapping exists but confidence is below its threshold.
* ``unmapped`` — no mapping for this class; events can still be persisted
  with a NULL category (``category_id``/``menu_item_id`` both ``None``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

ResolutionStatus = Literal["mapped", "ignored", "unmapped"]


@dataclass(frozen=True)
class MappingRule:
    """One ``class_mappings`` row, decoupled from the ORM."""

    model_class_id: int
    model_class_name: str
    category_id: int | None = None
    menu_item_id: int | None = None
    min_confidence: float = 0.35

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | Any) -> MappingRule:
        """Build a rule from a dict-like or attribute-bearing row."""
        if isinstance(row, MappingRule):
            return row
        if isinstance(row, Mapping):
            get: Any = row.get
        else:

            def get(key: str, default: Any = None) -> Any:
                return getattr(row, key, default)

        return cls(
            model_class_id=int(get("model_class_id")),
            model_class_name=str(get("model_class_name")),
            category_id=get("category_id"),
            menu_item_id=get("menu_item_id"),
            min_confidence=float(get("min_confidence", 0.35)),
        )


@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving one raw detection class."""

    status: ResolutionStatus
    raw_class_id: int
    raw_class_name: str
    confidence: float
    category_id: int | None = None
    menu_item_id: int | None = None

    @property
    def is_countable(self) -> bool:
        """Whether the detection passed its confidence gate.

        ``unmapped`` classes are countable (persisted with NULL category);
        only ``ignored`` (below per-mapping ``min_confidence``) is not.
        """
        return self.status != "ignored"


class ClassMappingResolver:
    """Resolve raw model classes using the active model's mapping set."""

    def __init__(self, rules: Iterable[MappingRule | Mapping[str, Any] | Any]) -> None:
        self._by_id: dict[int, MappingRule] = {}
        self._by_name: dict[str, MappingRule] = {}
        for raw in rules:
            rule = MappingRule.from_row(raw)
            self._by_id[rule.model_class_id] = rule
            self._by_name[rule.model_class_name] = rule

    def resolve(self, class_id: int, class_name: str, confidence: float) -> Resolution:
        rule = self._by_id.get(class_id)
        if rule is None:
            rule = self._by_name.get(class_name)
        if rule is None:
            return Resolution(
                status="unmapped",
                raw_class_id=class_id,
                raw_class_name=class_name,
                confidence=confidence,
            )
        if confidence < rule.min_confidence:
            return Resolution(
                status="ignored",
                raw_class_id=class_id,
                raw_class_name=class_name,
                confidence=confidence,
            )
        return Resolution(
            status="mapped",
            raw_class_id=class_id,
            raw_class_name=class_name,
            confidence=confidence,
            category_id=rule.category_id,
            menu_item_id=rule.menu_item_id,
        )

    def resolve_detection(self, detection: Any) -> Resolution:
        """Resolve anything with ``class_id``/``class_name``/``confidence``."""
        return self.resolve(detection.class_id, detection.class_name, detection.confidence)
