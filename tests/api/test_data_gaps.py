"""Historical data-gaps endpoint: window/camera filters, pagination, tenant
isolation, and the ``gaps_count`` flag on the stats summary."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.conftest import ApiEnv, add_event
from toskana.db.models import DataGap

T0 = 1_751_000_000_000  # fixed epoch-ms base for deterministic filtering


def add_gap(
    session: Session,
    *,
    restaurant_id: int,
    camera_id: int,
    from_ts: int,
    to_ts: int | None,
    reason: str = "test_outage",
) -> None:
    session.add(
        DataGap(
            restaurant_id=restaurant_id,
            camera_id=camera_id,
            from_ts=from_ts,
            to_ts=to_ts,
            reason=reason,
        )
    )


@pytest.fixture()
def seeded_gaps(api_env: ApiEnv) -> None:
    """A: three gaps (one open-ended, two cameras); B: one gap."""
    ids = api_env.ids
    with api_env.session() as session:
        add_gap(
            session,
            restaurant_id=ids["rest_a"],
            camera_id=ids["cam_a1"],
            from_ts=T0 + 1_000,
            to_ts=T0 + 2_000,
            reason="capture_stall",
        )
        add_gap(
            session,
            restaurant_id=ids["rest_a"],
            camera_id=ids["cam_a2"],
            from_ts=T0 + 10_000,
            to_ts=T0 + 10_000,
            reason="drift_alarm",
        )
        add_gap(
            session,
            restaurant_id=ids["rest_a"],
            camera_id=ids["cam_a1"],
            from_ts=T0 + 20_000,
            to_ts=None,  # ongoing outage
            reason="pipeline_error",
        )
        add_gap(
            session,
            restaurant_id=ids["rest_b"],
            camera_id=ids["cam_b1"],
            from_ts=T0 + 1_000,
            to_ts=T0 + 2_000,
        )
        session.commit()


class TestListDataGaps:
    def test_lists_newest_first_with_fields(
        self, client: TestClient, api_env: ApiEnv, seeded_gaps: None
    ) -> None:
        rid = api_env.ids["rest_a"]
        body = client.get(f"/api/restaurants/{rid}/data-gaps").json()
        assert body["total"] == 3
        reasons = [row["reason"] for row in body["items"]]
        assert reasons == ["pipeline_error", "drift_alarm", "capture_stall"]
        ongoing = body["items"][0]
        assert ongoing["camera_id"] == api_env.ids["cam_a1"]
        assert ongoing["from_ts"] == T0 + 20_000
        assert ongoing["to_ts"] is None
        assert ongoing["restaurant_id"] == rid

    def test_window_and_camera_filters(
        self, client: TestClient, api_env: ApiEnv, seeded_gaps: None
    ) -> None:
        rid = api_env.ids["rest_a"]
        # Window [T0+5000, T0+15000): only the zero-length drift marker.
        body = client.get(
            f"/api/restaurants/{rid}/data-gaps",
            params={"from_ts": T0 + 5_000, "to_ts": T0 + 15_000},
        ).json()
        assert [row["reason"] for row in body["items"]] == ["drift_alarm"]
        # An open gap overlaps any window that starts after it began.
        body = client.get(
            f"/api/restaurants/{rid}/data-gaps", params={"from_ts": T0 + 100_000}
        ).json()
        assert [row["reason"] for row in body["items"]] == ["pipeline_error"]
        # camera filter
        body = client.get(
            f"/api/restaurants/{rid}/data-gaps", params={"camera_id": api_env.ids["cam_a2"]}
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["reason"] == "drift_alarm"

    def test_pagination(self, client: TestClient, api_env: ApiEnv, seeded_gaps: None) -> None:
        rid = api_env.ids["rest_a"]
        first = client.get(f"/api/restaurants/{rid}/data-gaps", params={"limit": 2}).json()
        assert (first["total"], len(first["items"]), first["offset"]) == (3, 2, 0)
        second = client.get(
            f"/api/restaurants/{rid}/data-gaps", params={"limit": 2, "offset": 2}
        ).json()
        assert len(second["items"]) == 1
        assert second["items"][0]["reason"] == "capture_stall"

    def test_tenant_isolation(self, client: TestClient, api_env: ApiEnv, seeded_gaps: None) -> None:
        body = client.get(f"/api/restaurants/{api_env.ids['rest_b']}/data-gaps").json()
        assert body["total"] == 1
        assert body["items"][0]["camera_id"] == api_env.ids["cam_b1"]
        assert client.get("/api/restaurants/999999/data-gaps").status_code == 404
        # A's camera id through B's URL yields nothing, never A's rows.
        cross = client.get(
            f"/api/restaurants/{api_env.ids['rest_b']}/data-gaps",
            params={"camera_id": api_env.ids["cam_a1"]},
        ).json()
        assert cross["total"] == 0


class TestSummaryGapsCount:
    def test_gaps_overlapping_today_are_counted(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        now = int(time.time() * 1000)
        with api_env.session() as session:
            add_event(
                session,
                restaurant_id=rid,
                camera_id=api_env.ids["cam_a1"],
                ts=now,
                category_id=api_env.ids["cat_a_drink"],
            )
            # Two gaps today (one open-ended), one long past, one for B.
            add_gap(
                session,
                restaurant_id=rid,
                camera_id=api_env.ids["cam_a1"],
                from_ts=now - 1_000,
                to_ts=now,
            )
            add_gap(
                session,
                restaurant_id=rid,
                camera_id=api_env.ids["cam_a1"],
                from_ts=now,
                to_ts=None,
                reason="pipeline_error",
            )
            add_gap(
                session,
                restaurant_id=rid,
                camera_id=api_env.ids["cam_a1"],
                from_ts=T0,
                to_ts=T0 + 1_000,
            )
            add_gap(
                session,
                restaurant_id=api_env.ids["rest_b"],
                camera_id=api_env.ids["cam_b1"],
                from_ts=now,
                to_ts=now,
            )
            session.commit()
        summary = client.get(f"/api/restaurants/{rid}/stats/summary").json()
        assert summary["gaps_count"] == 2
        assert summary["total_out"] == 1

    def test_no_gaps_is_zero(self, client: TestClient, api_env: ApiEnv) -> None:
        summary = client.get(f"/api/restaurants/{api_env.ids['rest_a']}/stats/summary").json()
        assert summary["gaps_count"] == 0
