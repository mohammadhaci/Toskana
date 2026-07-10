"""Event log endpoints: filters, pagination, detail, canonical review,
CSV export, snapshot serving and tenant isolation."""

from __future__ import annotations

import csv
import io

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import ApiEnv, add_event

T0 = 1_751_000_000_000  # fixed epoch-ms base for deterministic filtering


@pytest.fixture()
def seeded_events(api_env: ApiEnv) -> dict[str, str]:
    """Five events: four for A (one non-canonical duplicate), one for B."""
    ids = api_env.ids
    with api_env.session() as session:
        events = {
            "a_drink_out": add_event(
                session,
                restaurant_id=ids["rest_a"],
                camera_id=ids["cam_a1"],
                ts=T0 + 1_000,
                direction="out",
                category_id=ids["cat_a_drink"],
                raw_class_name="cup",
                line_id=ids["line_a1"],
            ),
            "a_drink_in": add_event(
                session,
                restaurant_id=ids["rest_a"],
                camera_id=ids["cam_a1"],
                ts=T0 + 2_000,
                direction="in",
                category_id=ids["cat_a_drink"],
                raw_class_name="cup",
            ),
            "a_main_out": add_event(
                session,
                restaurant_id=ids["rest_a"],
                camera_id=ids["cam_a2"],
                ts=T0 + 3_000,
                direction="out",
                category_id=ids["cat_a_main"],
                raw_class_name="bowl",
            ),
            "a_dup": add_event(
                session,
                restaurant_id=ids["rest_a"],
                camera_id=ids["cam_a2"],
                ts=T0 + 4_000,
                direction="out",
                category_id=ids["cat_a_drink"],
                is_canonical=False,
                raw_class_name="cup",
            ),
            "b_drink_out": add_event(
                session,
                restaurant_id=ids["rest_b"],
                camera_id=ids["cam_b1"],
                ts=T0 + 2_500,
                direction="out",
                category_id=ids["cat_b_drink"],
                raw_class_name="cup",
            ),
        }
        session.commit()
    return events


def _list(client: TestClient, restaurant_id: int, **params: object) -> dict:
    response = client.get(f"/api/restaurants/{restaurant_id}/events", params=params)
    assert response.status_code == 200
    return response.json()


class TestListFilters:
    def test_canonical_only_default(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rid = api_env.ids["rest_a"]
        listed = _list(client, rid)
        assert listed["total"] == 3  # the non-canonical duplicate is hidden
        assert seeded_events["a_dup"] not in {row["id"] for row in listed["items"]}

        with_dups = _list(client, rid, canonical_only=False)
        assert with_dups["total"] == 4
        assert seeded_events["a_dup"] in {row["id"] for row in with_dups["items"]}

    def test_time_window(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rid = api_env.ids["rest_a"]
        # from_ts inclusive, to_ts exclusive.
        listed = _list(client, rid, from_ts=T0 + 2_000, to_ts=T0 + 3_000)
        assert [row["id"] for row in listed["items"]] == [seeded_events["a_drink_in"]]
        listed = _list(client, rid, from_ts=T0 + 2_000, to_ts=T0 + 3_001)
        assert {row["id"] for row in listed["items"]} == {
            seeded_events["a_drink_in"],
            seeded_events["a_main_out"],
        }

    def test_camera_category_direction_and_q(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rid, ids = api_env.ids["rest_a"], api_env.ids
        by_camera = _list(client, rid, camera_id=ids["cam_a2"])
        assert [row["id"] for row in by_camera["items"]] == [seeded_events["a_main_out"]]

        by_category = _list(client, rid, category_id=ids["cat_a_drink"])
        assert {row["id"] for row in by_category["items"]} == {
            seeded_events["a_drink_out"],
            seeded_events["a_drink_in"],
        }

        by_direction = _list(client, rid, direction="in")
        assert [row["id"] for row in by_direction["items"]] == [seeded_events["a_drink_in"]]
        assert (
            client.get(
                f"/api/restaurants/{rid}/events", params={"direction": "sideways"}
            ).status_code
            == 422
        )

        by_q = _list(client, rid, q="bo")  # substring of raw_class_name "bowl"
        assert [row["id"] for row in by_q["items"]] == [seeded_events["a_main_out"]]

    def test_pagination_newest_first(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rid = api_env.ids["rest_a"]
        first = _list(client, rid, limit=2, offset=0)
        second = _list(client, rid, limit=2, offset=2)
        assert first["total"] == second["total"] == 3
        assert len(first["items"]) == 2 and len(second["items"]) == 1
        ordered = [row["id"] for row in first["items"] + second["items"]]
        assert ordered == [
            seeded_events["a_main_out"],
            seeded_events["a_drink_in"],
            seeded_events["a_drink_out"],
        ]

    def test_tenant_isolation(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rid_b = api_env.ids["rest_b"]
        listed = _list(client, rid_b)
        assert [row["id"] for row in listed["items"]] == [seeded_events["b_drink_out"]]
        # Filtering B's list by A's camera never leaks A's events.
        cross = _list(client, rid_b, camera_id=api_env.ids["cam_a1"])
        assert cross["total"] == 0
        assert client.get("/api/restaurants/999999/events").status_code == 404


class TestDetailAndPatch:
    def test_detail(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rid = api_env.ids["rest_a"]
        event_id = seeded_events["a_drink_out"]
        got = client.get(f"/api/restaurants/{rid}/events/{event_id}")
        assert got.status_code == 200
        body = got.json()
        assert body["id"] == event_id
        assert body["direction"] == "out"
        assert body["camera_id"] == api_env.ids["cam_a1"]
        assert body["ts"] == T0 + 1_000
        assert body["is_canonical"] is True

    def test_detail_cross_tenant_404(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rid_b = api_env.ids["rest_b"]
        event_a = seeded_events["a_drink_out"]
        assert client.get(f"/api/restaurants/{rid_b}/events/{event_a}").status_code == 404
        assert (
            client.patch(
                f"/api/restaurants/{rid_b}/events/{event_a}", json={"is_canonical": False}
            ).status_code
            == 404
        )

    def test_patch_canonical_toggle_shows_in_review_list(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rid = api_env.ids["rest_a"]
        event_id = seeded_events["a_drink_out"]
        demoted = client.patch(
            f"/api/restaurants/{rid}/events/{event_id}", json={"is_canonical": False}
        )
        assert demoted.status_code == 200
        assert demoted.json()["is_canonical"] is False

        # Hidden from the default (canonical) list ...
        assert event_id not in {row["id"] for row in _list(client, rid)["items"]}
        # ... but auditable with canonical_only=false.
        review = _list(client, rid, canonical_only=False)
        flags = {row["id"]: row["is_canonical"] for row in review["items"]}
        assert flags[event_id] is False

        promoted = client.patch(
            f"/api/restaurants/{rid}/events/{event_id}", json={"is_canonical": True}
        )
        assert promoted.json()["is_canonical"] is True
        assert event_id in {row["id"] for row in _list(client, rid)["items"]}


class TestCsvExport:
    def _rows(self, client: TestClient, restaurant_id: int, **params: object) -> list[dict]:
        response = client.get(f"/api/restaurants/{restaurant_id}/events/export.csv", params=params)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "events.csv" in response.headers["content-disposition"]
        reader = csv.DictReader(io.StringIO(response.text))
        assert reader.fieldnames == [
            "id",
            "ts",
            "ts_iso_utc",
            "camera_id",
            "camera_name",
            "line_id",
            "direction",
            "category_id",
            "category_key",
            "menu_item_id",
            "raw_class_name",
            "confidence",
            "is_canonical",
            "dedup_group_id",
            "snapshot_path",
        ]
        return list(reader)

    def test_export_respects_filters_and_canonical_only(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rid = api_env.ids["rest_a"]
        rows = self._rows(client, rid)
        assert len(rows) == 3  # canonical only by default
        assert [row["id"] for row in rows] == [  # export is oldest-first
            seeded_events["a_drink_out"],
            seeded_events["a_drink_in"],
            seeded_events["a_main_out"],
        ]
        first = rows[0]
        assert first["ts"] == str(T0 + 1_000)
        assert first["camera_name"] == "A cam 1"
        assert first["category_key"] == "drink"
        assert first["is_canonical"] == "1"

        all_rows = self._rows(client, rid, canonical_only=False)
        assert len(all_rows) == 4

        out_only = self._rows(client, rid, direction="out")
        assert {row["direction"] for row in out_only} == {"out"}
        assert len(out_only) == 2

    def test_export_is_tenant_scoped(
        self, client: TestClient, api_env: ApiEnv, seeded_events: dict[str, str]
    ) -> None:
        rows = self._rows(client, api_env.ids["rest_b"])
        assert [row["id"] for row in rows] == [seeded_events["b_drink_out"]]


class TestSnapshot:
    def _make_jpeg(self, api_env: ApiEnv, relative: str) -> None:
        path = api_env.snapshots_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".jpg", np.full((8, 8, 3), 128, np.uint8))
        assert ok
        path.write_bytes(encoded.tobytes())

    def test_snapshot_served_as_jpeg(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        self._make_jpeg(api_env, "2026/ev1.jpg")
        with api_env.session() as session:
            event_id = add_event(
                session,
                restaurant_id=rid,
                camera_id=api_env.ids["cam_a1"],
                ts=T0,
                snapshot_path="2026/ev1.jpg",
            )
            session.commit()
        response = client.get(f"/api/restaurants/{rid}/events/{event_id}/snapshot")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content.startswith(b"\xff\xd8")  # JPEG SOI marker

    def test_missing_file_and_missing_path_404(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        with api_env.session() as session:
            gone = add_event(
                session,
                restaurant_id=rid,
                camera_id=api_env.ids["cam_a1"],
                ts=T0,
                snapshot_path="nope/missing.jpg",
            )
            bare = add_event(session, restaurant_id=rid, camera_id=api_env.ids["cam_a1"], ts=T0 + 1)
            session.commit()
        assert client.get(f"/api/restaurants/{rid}/events/{gone}/snapshot").status_code == 404
        assert client.get(f"/api/restaurants/{rid}/events/{bare}/snapshot").status_code == 404

    def test_snapshot_cross_tenant_404(self, client: TestClient, api_env: ApiEnv) -> None:
        rid_a, rid_b = api_env.ids["rest_a"], api_env.ids["rest_b"]
        self._make_jpeg(api_env, "2026/ev2.jpg")
        with api_env.session() as session:
            event_id = add_event(
                session,
                restaurant_id=rid_a,
                camera_id=api_env.ids["cam_a1"],
                ts=T0,
                snapshot_path="2026/ev2.jpg",
            )
            session.commit()
        assert client.get(f"/api/restaurants/{rid_b}/events/{event_id}/snapshot").status_code == 404
