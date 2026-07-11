"""M10 ACCEPTANCE: ``eval-counting`` scores 100% on every synthetic scenario.

The harness replays each generated clip through the real pipeline
(synthetic backend), matches the measured crossings against
``ground_truth.json`` and must report perfect precision/recall with zero
count error — including the two-camera scenario, where only the canonical
(post-dedup) counts are scored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.tools.make_synthetic_video import generate_scenario
from toskana.cli import main as cli_main
from toskana.db.base import make_engine, make_session_factory
from toskana.db.models import CountingEvalRun
from toskana.eval.counting import (
    GTCrossing,
    MeasuredCrossing,
    compute_report,
    match_crossings,
    min_score_failures,
)
from toskana.eval.runner import evaluate_videos
from toskana.vision.line_crossing import LineSpec

#: Scenario line: vertical at x=0.5, drawn top->bottom => positive = left->right.
LINE = LineSpec(x1=0.5, y1=0.0, x2=0.5, y2=1.0, line_id=None)
#: cam_b of two_cam_same_exit is mirrored: bottom->top keeps directions aligned.
LINE_B = LineSpec(x1=0.5, y1=1.0, x2=0.5, y2=0.0, line_id=None)

SINGLE_CAM_SCENARIOS = (
    "single_drink",
    "tray_carry_3",
    "reverse_return",
    "loiter_on_line",
    "occlusion_gap",
)


def _assert_perfect(report: dict) -> None:
    overall = report["overall"]
    assert overall["precision"] == 1.0
    assert overall["recall"] == 1.0
    assert overall["f1"] == 1.0
    assert overall["count_mae"] == 0.0
    assert overall["gt"] == overall["measured"] == overall["tp"]
    assert overall["fp"] == overall["fn"] == 0
    for section in ("per_category", "per_hour"):
        for row in report[section].values():
            assert row["precision"] == 1.0 and row["recall"] == 1.0
            assert row["count_error"] == 0


class TestScenariosScorePerfectly:
    @pytest.mark.parametrize("name", SINGLE_CAM_SCENARIOS)
    def test_single_camera_scenario(self, name: str, tmp_path: Path) -> None:
        generated = generate_scenario(name, tmp_path)
        outcome = evaluate_videos(generated.video_paths["cam"], LINE, generated.ground_truth)
        _assert_perfect(outcome.report)
        assert outcome.scenario == name
        assert outcome.raw_measured_total == len(outcome.measured)  # single cam: no dedup

    def test_two_cam_scenario_scores_canonical_counts(self, tmp_path: Path) -> None:
        generated = generate_scenario("two_cam_same_exit", tmp_path)
        outcome = evaluate_videos(
            generated.video_paths["cam_a"],
            LINE,
            generated.ground_truth,
            video2=generated.video_paths["cam_b"],
            line2=LINE_B,
        )
        _assert_perfect(outcome.report)
        gt_canonical = len(generated.ground_truth["canonical_crossings"])
        assert len(outcome.measured) == gt_canonical  # never the double
        assert outcome.raw_measured_total == 2 * gt_canonical  # both cams saw everything


class TestMetricsCatchErrors:
    def test_missed_and_spurious_crossings_are_reported(self) -> None:
        gt = [GTCrossing("drink", "positive", 1_000), GTCrossing("main", "positive", 5_000)]
        measured = [
            MeasuredCrossing("drink", "positive", 1_500),  # match (within tolerance)
            MeasuredCrossing("drink", "positive", 9_000),  # false positive
        ]
        overall = compute_report(gt, measured)["overall"]
        assert (overall["tp"], overall["fp"], overall["fn"]) == (1, 1, 1)
        assert overall["precision"] == 0.5 and overall["recall"] == 0.5

    def test_matching_is_one_to_one_and_category_direction_strict(self) -> None:
        gt = [GTCrossing("drink", "positive", 0), GTCrossing("drink", "positive", 100)]
        # One measured crossing can satisfy at most one GT crossing …
        match = match_crossings(gt, [MeasuredCrossing("drink", "positive", 100)])
        assert match.pairs == ((1, 0),) and match.unmatched_gt == (0,)
        # … and never one of a different category or direction.
        assert not match_crossings(gt, [MeasuredCrossing("main", "positive", 0)]).pairs
        assert not match_crossings(gt, [MeasuredCrossing("drink", "negative", 0)]).pairs


class TestMinScoreScope:
    """A perfect average must not hide a bad hour: scope 'both' (default)
    also gates every per-hour bucket."""

    #: Hour 10 is perfect (1 TP); hour 12 is all misses (1 FN + 1 FP).
    GT = [
        GTCrossing("drink", "positive", 10 * 3_600_000),
        GTCrossing("drink", "positive", 12 * 3_600_000),
    ]
    MEASURED = [
        MeasuredCrossing("drink", "positive", 10 * 3_600_000 + 500),
        MeasuredCrossing("drink", "positive", 12 * 3_600_000 + 60_000),  # outside tolerance
    ]

    def test_overall_scope_misses_the_bad_hour(self) -> None:
        report = compute_report(self.GT, self.MEASURED)
        # Overall precision/recall are 0.5 — a 0.4 gate passes on average …
        assert min_score_failures(report, 0.4, "overall") == []
        # … but the per-hour and default 'both' scopes catch hour 12.
        per_hour = min_score_failures(report, 0.4, "per-hour")
        assert per_hour == ["hour 12 precision 0.0000", "hour 12 recall 0.0000"]
        assert min_score_failures(report, 0.4, "both") == per_hour

    def test_both_scope_reports_overall_and_hours(self) -> None:
        report = compute_report(self.GT, self.MEASURED)
        failures = min_score_failures(report, 0.9, "both")
        assert "overall precision 0.5000" in failures
        assert "overall recall 0.5000" in failures
        assert "hour 12 precision 0.0000" in failures
        assert "hour 10 precision 1.0000" not in " ".join(failures)

    def test_perfect_report_passes_every_scope(self) -> None:
        gt = [GTCrossing("drink", "positive", 1_000)]
        report = compute_report(gt, [MeasuredCrossing("drink", "positive", 1_200)])
        for scope in ("overall", "per-hour", "both"):
            assert min_score_failures(report, 1.0, scope) == []

    def test_unknown_scope_raises(self) -> None:
        report = compute_report([], [])
        with pytest.raises(ValueError, match="scope"):
            min_score_failures(report, 0.9, "per-category")


class TestEvalCountingCli:
    def test_json_report_and_recorded_run(self, tmp_path: Path, capsys) -> None:
        generated = generate_scenario("tray_carry_3", tmp_path)
        db_path = str(tmp_path / "eval.db")
        exit_code = cli_main(
            [
                "eval-counting",
                "--video",
                str(generated.video_paths["cam"]),
                "--gt",
                str(generated.ground_truth_path),
                "--db",
                db_path,
                "--min-score",
                "1.0",
                "--json",
            ]
        )
        assert exit_code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["scenario"] == "tray_carry_3"
        assert report["overall"]["precision"] == 1.0
        assert report["overall"]["recall"] == 1.0
        assert report["per_category"]["drink"]["gt"] == 1
        assert report["per_category"]["main"]["gt"] == 2

        engine = make_engine(db_path)
        with make_session_factory(engine)() as session:
            (row,) = session.scalars(select(CountingEvalRun)).all()
        engine.dispose()
        assert row.id == report["run_id"]
        assert row.scenario == "tray_carry_3"
        assert json.loads(row.metrics_json)["overall"]["recall"] == 1.0
        assert json.loads(row.gt_counts_json) == json.loads(row.measured_counts_json)

    def test_min_score_gate_fails_on_wrong_line(self, tmp_path: Path, capsys) -> None:
        """A line nothing crosses measures zero events -> recall 0 -> exit 1."""
        generated = generate_scenario("single_drink", tmp_path)
        exit_code = cli_main(
            [
                "eval-counting",
                "--video",
                str(generated.video_paths["cam"]),
                "--gt",
                str(generated.ground_truth_path),
                "--line",
                "0.0,0.0,1.0,0.0",  # horizontal line at the top edge
                "--min-score",
                "0.9",
                "--json",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert json.loads(captured.out)["overall"]["recall"] == 0.0
        assert "below --min-score" in captured.err
        assert "scope both" in captured.err  # default scope

    def test_min_score_scope_flag_is_wired(self, tmp_path: Path, capsys) -> None:
        """--min-score-scope reaches the gate (perfect run passes 'per-hour')."""
        generated = generate_scenario("single_drink", tmp_path)
        exit_code = cli_main(
            [
                "eval-counting",
                "--video",
                str(generated.video_paths["cam"]),
                "--gt",
                str(generated.ground_truth_path),
                "--min-score",
                "1.0",
                "--min-score-scope",
                "per-hour",
                "--json",
            ]
        )
        assert exit_code == 0
        assert json.loads(capsys.readouterr().out)["overall"]["recall"] == 1.0
