"""Match measured crossings against ground truth and compute count metrics.

Pure functions, no I/O: the CLI runner (:mod:`toskana.eval.runner`) turns
videos + ``ground_truth.json`` into :class:`GTCrossing` /
:class:`MeasuredCrossing` lists and this module scores them.

Matching is greedy one-to-one by smallest ``|Δt|``: a measured crossing
matches a ground-truth crossing only when the **category and direction are
identical** and the timestamps are within ``tolerance_ms`` (default 2 s,
inclusive). Candidate pairs are sorted by ``|Δt|`` and consumed one-to-one,
so three tray items crossing together form three distinct pairs and a single
measured event can never satisfy two ground-truth crossings.

Metrics per category and overall: precision / recall / F1 over the matched
pairs, plus count MAE (mean absolute difference between ground-truth and
measured counts per category). A **per-hour-of-day breakdown** (bucketed
from the ground-truth timestamps) keeps peak-hour accuracy visible
separately from the average — for short synthetic clips this is one bucket,
for annotated 30-min service recordings it separates the rush.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_TOLERANCE_MS = 2000

#: Direction vocabulary normalization (GT files and DB rows both accepted).
_DIRECTIONS = {
    "positive": "positive",
    "out": "positive",
    "negative": "negative",
    "in": "negative",
}


def normalize_direction(value: str) -> str:
    """``out``/``positive`` -> ``positive``; ``in``/``negative`` -> ``negative``."""
    try:
        return _DIRECTIONS[value.strip().lower()]
    except KeyError:
        raise ValueError(f"unknown crossing direction: {value!r}") from None


@dataclass(frozen=True)
class GTCrossing:
    """One ground-truth crossing (category + direction + media timestamp)."""

    category: str
    direction: str  # positive | negative (normalized)
    ts_ms: int
    object_id: str | None = None


@dataclass(frozen=True)
class MeasuredCrossing:
    """One crossing the pipeline counted (canonical after dedup)."""

    category: str
    direction: str
    ts_ms: int
    event_id: str | None = None


def parse_gt_crossings(ground_truth: Mapping[str, Any]) -> list[GTCrossing]:
    """Extract crossings from a ``ground_truth.json`` payload.

    Handles both formats produced by ``tests/tools/make_synthetic_video``:
    single-camera files carry ``crossings``; two-camera files carry
    ``canonical_crossings`` (the physical truth dedup must reproduce).
    Timestamp key: ``crossing_ts_ms`` (preferred) or ``ts_ms``.
    """
    raw = ground_truth.get("canonical_crossings")
    if raw is None:
        raw = ground_truth.get("crossings")
    if raw is None:
        raise ValueError("ground truth has neither 'crossings' nor 'canonical_crossings'")
    crossings: list[GTCrossing] = []
    for row in raw:
        ts = row.get("crossing_ts_ms", row.get("ts_ms"))
        if ts is None:
            raise ValueError(f"ground-truth crossing without a timestamp: {row!r}")
        category = row.get("class_key", row.get("category"))
        if category is None:
            raise ValueError(f"ground-truth crossing without a class/category: {row!r}")
        crossings.append(
            GTCrossing(
                category=str(category),
                direction=normalize_direction(str(row["direction"])),
                ts_ms=int(ts),
                object_id=row.get("object_id"),
            )
        )
    return crossings


@dataclass(frozen=True)
class MatchResult:
    """Greedy one-to-one assignment between GT and measured crossings."""

    pairs: tuple[tuple[int, int], ...]  # (gt_index, measured_index)
    unmatched_gt: tuple[int, ...]  # false negatives
    unmatched_measured: tuple[int, ...]  # false positives


def match_crossings(
    gt: Sequence[GTCrossing],
    measured: Sequence[MeasuredCrossing],
    *,
    tolerance_ms: int = DEFAULT_TOLERANCE_MS,
) -> MatchResult:
    """Greedy 1:1 matching by smallest ``|Δt|`` (same category + direction).

    ``|Δt| == tolerance_ms`` still matches (inclusive bound).
    """
    candidates: list[tuple[int, int, int]] = []  # (|dt|, gt_i, meas_j)
    for i, g in enumerate(gt):
        for j, m in enumerate(measured):
            if g.category != m.category or g.direction != m.direction:
                continue
            dt = abs(g.ts_ms - m.ts_ms)
            if dt <= tolerance_ms:
                candidates.append((dt, i, j))
    candidates.sort()
    used_gt: set[int] = set()
    used_measured: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _dt, i, j in candidates:
        if i in used_gt or j in used_measured:
            continue
        pairs.append((i, j))
        used_gt.add(i)
        used_measured.add(j)
    return MatchResult(
        pairs=tuple(sorted(pairs)),
        unmatched_gt=tuple(i for i in range(len(gt)) if i not in used_gt),
        unmatched_measured=tuple(j for j in range(len(measured)) if j not in used_measured),
    )


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    """Precision/recall/F1 with the empty-denominator convention = perfect."""
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def hour_of_day(ts_ms: int) -> str:
    """Bucket key ``"00"``..``"23"``.

    Works for media-relative timestamps (short clips land in bucket 00,
    a 30-min recording spans one or two buckets) and for absolute UTC epoch
    milliseconds (real annotated footage) alike.
    """
    return f"{(ts_ms // 3_600_000) % 24:02d}"


def _bucket_metrics(
    keys: Sequence[str],
    tp_keys: Sequence[str],
    fp_keys: Sequence[str],
    fn_keys: Sequence[str],
    gt_keys: Sequence[str],
    measured_keys: Sequence[str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(set(keys)):
        tp = sum(1 for k in tp_keys if k == key)
        fp = sum(1 for k in fp_keys if k == key)
        fn = sum(1 for k in fn_keys if k == key)
        gt_count = sum(1 for k in gt_keys if k == key)
        measured_count = sum(1 for k in measured_keys if k == key)
        out[key] = {
            "gt": gt_count,
            "measured": measured_count,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "count_error": measured_count - gt_count,
            **_prf(tp, fp, fn),
        }
    return out


def compute_report(
    gt: Sequence[GTCrossing],
    measured: Sequence[MeasuredCrossing],
    *,
    tolerance_ms: int = DEFAULT_TOLERANCE_MS,
) -> dict[str, Any]:
    """Full metrics report: overall + per-category + per-hour-of-day.

    ``count_mae`` (overall) is the mean absolute count error over
    categories; per-category rows carry the signed ``count_error``.
    True/false negatives are bucketed by the **ground-truth** timestamp,
    false positives by the measured timestamp (there is no GT time for them).
    """
    match = match_crossings(gt, measured, tolerance_ms=tolerance_ms)
    tp = len(match.pairs)
    fp = len(match.unmatched_measured)
    fn = len(match.unmatched_gt)

    categories = [g.category for g in gt] + [m.category for m in measured]
    per_category = _bucket_metrics(
        categories,
        tp_keys=[gt[i].category for i, _ in match.pairs],
        fp_keys=[measured[j].category for j in match.unmatched_measured],
        fn_keys=[gt[i].category for i in match.unmatched_gt],
        gt_keys=[g.category for g in gt],
        measured_keys=[m.category for m in measured],
    )
    hours = [hour_of_day(g.ts_ms) for g in gt] + [hour_of_day(m.ts_ms) for m in measured]
    per_hour = _bucket_metrics(
        hours,
        tp_keys=[hour_of_day(gt[i].ts_ms) for i, _ in match.pairs],
        fp_keys=[hour_of_day(measured[j].ts_ms) for j in match.unmatched_measured],
        fn_keys=[hour_of_day(gt[i].ts_ms) for i in match.unmatched_gt],
        gt_keys=[hour_of_day(g.ts_ms) for g in gt],
        measured_keys=[hour_of_day(m.ts_ms) for m in measured],
    )
    count_errors = [abs(row["count_error"]) for row in per_category.values()]
    count_mae = round(sum(count_errors) / len(count_errors), 4) if count_errors else 0.0

    return {
        "tolerance_ms": tolerance_ms,
        "overall": {
            "gt": len(gt),
            "measured": len(measured),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "count_mae": count_mae,
            **_prf(tp, fp, fn),
        },
        "per_category": per_category,
        "per_hour": per_hour,
    }


#: Where a ``--min-score`` gate looks: the overall metrics, every per-hour
#: bucket, or both (the default — a bad rush hour must not hide in the mean).
MIN_SCORE_SCOPES = ("overall", "per-hour", "both")


def min_score_failures(
    report: Mapping[str, Any], min_score: float, scope: str = "both"
) -> list[str]:
    """Reasons a report fails the ``--min-score`` gate (empty = passes).

    Precision and recall are checked; ``scope`` selects the buckets:
    ``overall``, ``per-hour`` (every hour-of-day bucket) or ``both``.
    """
    if scope not in MIN_SCORE_SCOPES:
        raise ValueError(f"min-score scope must be one of {MIN_SCORE_SCOPES}: {scope!r}")
    failures: list[str] = []
    if scope in ("overall", "both"):
        overall = report["overall"]
        failures.extend(
            f"overall {metric} {overall[metric]:.4f}"
            for metric in ("precision", "recall")
            if overall[metric] < min_score
        )
    if scope in ("per-hour", "both"):
        for hour, row in report["per_hour"].items():
            failures.extend(
                f"hour {hour} {metric} {row[metric]:.4f}"
                for metric in ("precision", "recall")
                if row[metric] < min_score
            )
    return failures


def counts_by_category(
    crossings: Sequence[GTCrossing] | Sequence[MeasuredCrossing],
) -> dict[str, dict[str, int]]:
    """``{category: {direction: n}}`` — stored alongside eval runs for audit."""
    out: dict[str, dict[str, int]] = {}
    for crossing in crossings:
        per_direction = out.setdefault(crossing.category, {})
        per_direction[crossing.direction] = per_direction.get(crossing.direction, 0) + 1
    return {key: dict(sorted(value.items())) for key, value in sorted(out.items())}
