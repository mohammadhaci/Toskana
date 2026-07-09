"""Exhaustive tests for the pure geometry helpers."""

from __future__ import annotations

import math

import pytest

from toskana.vision.geometry import (
    anchor_point,
    denormalize_line,
    perpendicular_distance_px,
    segments_intersect,
    side_of_line,
)

# A vertical counting line drawn top -> bottom (like the synthetic scenarios).
V_A = (320.0, 0.0)
V_B = (320.0, 360.0)


class TestSideOfLine:
    def test_vertical_line_left_is_negative_right_is_positive(self) -> None:
        # Documented convention: for a top->bottom vertical line,
        # image-left = -1 and image-right = +1, so positive crossings
        # (-1 -> +1) are left -> right, matching the scenario ground truth.
        assert side_of_line((60.0, 180.0), V_A, V_B) == -1
        assert side_of_line((580.0, 180.0), V_A, V_B) == 1

    def test_point_exactly_on_line_is_zero(self) -> None:
        assert side_of_line((320.0, 123.0), V_A, V_B) == 0
        # Also beyond the segment's extent: side is about the infinite line.
        assert side_of_line((320.0, -50.0), V_A, V_B) == 0

    def test_reversing_the_line_flips_the_sign(self) -> None:
        p = (60.0, 180.0)
        assert side_of_line(p, V_A, V_B) == -side_of_line(p, V_B, V_A)

    def test_horizontal_line(self) -> None:
        a, b = (0.0, 100.0), (640.0, 100.0)  # left -> right
        assert side_of_line((320.0, 200.0), a, b) == -1  # below
        assert side_of_line((320.0, 50.0), a, b) == 1  # above

    def test_diagonal_line(self) -> None:
        a, b = (0.0, 0.0), (100.0, 100.0)
        assert side_of_line((90.0, 10.0), a, b) == 1
        assert side_of_line((10.0, 90.0), a, b) == -1
        assert side_of_line((50.0, 50.0), a, b) == 0


class TestPerpendicularDistance:
    def test_vertical_line(self) -> None:
        assert perpendicular_distance_px((300.0, 180.0), V_A, V_B) == pytest.approx(20.0)
        assert perpendicular_distance_px((345.0, 10.0), V_A, V_B) == pytest.approx(25.0)
        assert perpendicular_distance_px((320.0, 999.0), V_A, V_B) == pytest.approx(0.0)

    def test_distance_is_unsigned(self) -> None:
        left = perpendicular_distance_px((310.0, 50.0), V_A, V_B)
        right = perpendicular_distance_px((330.0, 50.0), V_A, V_B)
        assert left == pytest.approx(right) == pytest.approx(10.0)

    def test_diagonal_line(self) -> None:
        a, b = (0.0, 0.0), (10.0, 10.0)
        assert perpendicular_distance_px((10.0, 0.0), a, b) == pytest.approx(math.sqrt(50.0))

    def test_measures_infinite_line_not_segment(self) -> None:
        # Point beyond the segment end: still perpendicular distance to the line.
        assert perpendicular_distance_px((330.0, 1000.0), V_A, V_B) == pytest.approx(10.0)

    def test_degenerate_segment_falls_back_to_point_distance(self) -> None:
        assert perpendicular_distance_px((3.0, 4.0), (0.0, 0.0), (0.0, 0.0)) == pytest.approx(5.0)


class TestSegmentsIntersect:
    def test_proper_crossing(self) -> None:
        assert segments_intersect((300.0, 100.0), (340.0, 100.0), V_A, V_B)

    def test_no_intersection_parallel(self) -> None:
        assert not segments_intersect((0.0, 0.0), (0.0, 360.0), V_A, V_B)

    def test_no_intersection_disjoint(self) -> None:
        assert not segments_intersect((0.0, 0.0), (100.0, 100.0), V_A, V_B)

    def test_crossing_line_extension_but_not_segment(self) -> None:
        # Moves across x=320 but below the segment's end (y > 360).
        assert not segments_intersect((300.0, 400.0), (340.0, 400.0), V_A, V_B)

    def test_touching_at_segment_endpoint_counts(self) -> None:
        assert segments_intersect((300.0, 360.0), (340.0, 360.0), V_A, V_B)
        assert segments_intersect((260.0, -60.0), (320.0, 0.0), V_A, V_B)

    def test_t_junction_counts(self) -> None:
        # Movement ends exactly on the line.
        assert segments_intersect((300.0, 100.0), (320.0, 100.0), V_A, V_B)

    def test_shared_endpoint_counts(self) -> None:
        assert segments_intersect((320.0, 0.0), (100.0, 50.0), V_A, V_B)

    def test_collinear_overlapping(self) -> None:
        assert segments_intersect((320.0, 100.0), (320.0, 200.0), V_A, V_B)

    def test_collinear_disjoint(self) -> None:
        assert not segments_intersect((320.0, 400.0), (320.0, 500.0), V_A, V_B)

    def test_collinear_touching_at_one_point(self) -> None:
        assert segments_intersect((320.0, 360.0), (320.0, 500.0), V_A, V_B)

    def test_degenerate_point_on_segment(self) -> None:
        assert segments_intersect((320.0, 100.0), (320.0, 100.0), V_A, V_B)

    def test_degenerate_point_off_segment(self) -> None:
        assert not segments_intersect((321.0, 100.0), (321.0, 100.0), V_A, V_B)

    def test_symmetry(self) -> None:
        p1, p2 = (300.0, 100.0), (340.0, 120.0)
        assert segments_intersect(p1, p2, V_A, V_B) == segments_intersect(V_A, V_B, p1, p2)


class TestDenormalizeLine:
    def test_vertical_center_line(self) -> None:
        a, b = denormalize_line((0.5, 0.0, 0.5, 1.0), 640, 360)
        assert a == (320.0, 0.0)
        assert b == (320.0, 360.0)

    def test_arbitrary_line_and_resolution(self) -> None:
        a, b = denormalize_line((0.25, 0.1, 0.75, 0.9), 1920, 1080)
        assert a == (480.0, 108.0)
        assert b == (1440.0, 972.0)


class TestAnchorPoint:
    BBOX = (100.0, 50.0, 140.0, 110.0)

    def test_bottom_center(self) -> None:
        assert anchor_point(self.BBOX, "bottom_center") == (120.0, 110.0)

    def test_center(self) -> None:
        assert anchor_point(self.BBOX, "center") == (120.0, 80.0)

    def test_default_is_bottom_center(self) -> None:
        assert anchor_point(self.BBOX) == (120.0, 110.0)

    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="anchor mode"):
            anchor_point(self.BBOX, "top_left")
