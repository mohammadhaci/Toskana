"""Pure 2D geometry for the line-crossing engine.

All functions work in **image pixel coordinates** (origin top-left, y grows
downward). The counting line is a *finite directed segment* ``a -> b``.

Side convention (used everywhere in the counting stack):

* ``side_of_line(p, a, b)`` is the sign of the cross product
  ``(p - a) x (b - a)`` (z-component, evaluated in image coordinates).
* For a vertical line drawn top->bottom (``a`` above ``b``), points with a
  **larger x** (image right) are on side ``+1`` and points to the left are
  on side ``-1``. A *positive* crossing (side ``-1`` -> ``+1``) is therefore
  **left -> right**, matching the synthetic scenarios' ground truth
  (``positive_direction = left_to_right``).
"""

from __future__ import annotations

Point = tuple[float, float]
LineNorm = tuple[float, float, float, float]  # x1, y1, x2, y2 normalized 0..1

ANCHOR_MODES = ("bottom_center", "center")


def cross_z(p: Point, a: Point, b: Point) -> float:
    """z-component of the cross product ``(p - a) x (b - a)``."""
    return (p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])


def side_of_line(p: Point, a: Point, b: Point) -> int:
    """Which side of the (infinite) directed line ``a -> b`` is ``p`` on?

    Returns ``-1`` | ``0`` | ``1`` (0 = exactly on the line). For a vertical
    top->bottom line, image-left is ``-1`` and image-right is ``+1``.
    """
    value = cross_z(p, a, b)
    if value > 0.0:
        return 1
    if value < 0.0:
        return -1
    return 0


def perpendicular_distance_px(p: Point, a: Point, b: Point) -> float:
    """Unsigned perpendicular distance from ``p`` to the infinite line ``a-b``.

    Degenerate segment (``a == b``) falls back to the point distance.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0.0:
        px, py = p[0] - a[0], p[1] - a[1]
        return (px * px + py * py) ** 0.5
    return abs(cross_z(p, a, b)) / length


def _on_segment(p: Point, a: Point, b: Point) -> bool:
    """Is collinear point ``p`` within the bounding box of segment ``a-b``?"""
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def segments_intersect(p1: Point, p2: Point, a: Point, b: Point) -> bool:
    """Do segments ``p1-p2`` and ``a-b`` intersect (touching counts)?

    Handles all edge cases: proper crossings, shared endpoints, T-junctions,
    collinear overlaps and degenerate (zero-length) segments.
    """
    d1 = cross_z(a, p1, p2)  # orientation of a relative to p1->p2
    d2 = cross_z(b, p1, p2)
    d3 = cross_z(p1, a, b)
    d4 = cross_z(p2, a, b)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True  # proper crossing

    if d1 == 0 and _on_segment(a, p1, p2):
        return True
    if d2 == 0 and _on_segment(b, p1, p2):
        return True
    if d3 == 0 and _on_segment(p1, a, b):
        return True
    if d4 == 0 and _on_segment(p2, a, b):
        return True
    return False


def denormalize_line(line_norm: LineNorm, frame_w: float, frame_h: float) -> tuple[Point, Point]:
    """Convert normalized (0..1) line endpoints to pixel endpoints ``(a, b)``."""
    x1, y1, x2, y2 = line_norm
    return ((x1 * frame_w, y1 * frame_h), (x2 * frame_w, y2 * frame_h))


def anchor_point(bbox: tuple[float, float, float, float], mode: str = "bottom_center") -> Point:
    """Anchor point of an ``xyxy`` bbox: ``bottom_center`` (default) or ``center``."""
    x1, y1, x2, y2 = bbox
    if mode == "bottom_center":
        return ((x1 + x2) / 2.0, y2)
    if mode == "center":
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    raise ValueError(f"anchor mode must be one of {ANCHOR_MODES}: {mode!r}")
