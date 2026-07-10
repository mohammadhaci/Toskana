"""Aggregated stats: hourly/daily bucketing in the restaurant timezone
(including both DST transitions), grouping, net counts and the today summary."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from tests.api.conftest import ApiEnv, add_event

VIENNA = ZoneInfo("Europe/Vienna")

#: Europe/Vienna 2026 DST transitions.
SPRING_DAY = "2026-03-29"  # 02:00 CET -> 03:00 CEST (23h day)
FALL_DAY = "2026-10-25"  # 03:00 CEST -> 02:00 CET (25h day)


def local_ms(year: int, month: int, day: int, hour: int, minute: int = 0, fold: int = 0) -> int:
    dt = datetime(year, month, day, hour, minute, tzinfo=VIENNA, fold=fold)
    return int(dt.timestamp() * 1000)


def _timeseries(client: TestClient, restaurant_id: int, **params: object) -> dict:
    response = client.get(f"/api/restaurants/{restaurant_id}/stats/timeseries", params=params)
    assert response.status_code == 200
    return response.json()


class TestDstBucketing:
    def test_spring_forward_hour_buckets_skip_missing_hour(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        rid, cam = api_env.ids["rest_a"], api_env.ids["cam_a1"]
        # 02:xx does not exist locally on the spring-forward day.
        hours = [0, 1, 3, 4]
        with api_env.session() as session:
            for hour in hours:
                add_event(
                    session, restaurant_id=rid, camera_id=cam, ts=local_ms(2026, 3, 29, hour, 30)
                )
            session.commit()

        day_from, day_to = local_ms(2026, 3, 29, 0), local_ms(2026, 3, 30, 0)
        assert day_to - day_from == 23 * 3_600_000  # the local day is 23h long

        body = _timeseries(client, rid, bucket="hour", from_ts=day_from, to_ts=day_to)
        assert body["timezone"] == "Europe/Vienna"
        iso_hours = [row["bucket_iso"] for row in body["rows"]]
        assert iso_hours == [
            f"{SPRING_DAY}T00:00:00+01:00",
            f"{SPRING_DAY}T01:00:00+01:00",
            f"{SPRING_DAY}T03:00:00+02:00",  # the 02:00 bucket never appears
            f"{SPRING_DAY}T04:00:00+02:00",
        ]
        assert all(row["out"] == 1 and row["in"] == 0 for row in body["rows"])
        assert sum(row["out"] for row in body["rows"]) == len(hours)  # nothing dropped

    def test_fall_back_repeated_hour_keeps_both_occurrences(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        rid, cam = api_env.ids["rest_a"], api_env.ids["cam_a1"]
        first = local_ms(2026, 10, 25, 2, 30, fold=0)  # 02:30 CEST
        second = local_ms(2026, 10, 25, 2, 30, fold=1)  # 02:30 CET, one hour later
        assert second - first == 3_600_000
        with api_env.session() as session:
            add_event(session, restaurant_id=rid, camera_id=cam, ts=first)
            add_event(session, restaurant_id=rid, camera_id=cam, ts=second)
            session.commit()

        day_from, day_to = local_ms(2026, 10, 25, 0), local_ms(2026, 10, 26, 0)
        assert day_to - day_from == 25 * 3_600_000  # the local day is 25h long

        hourly = _timeseries(client, rid, bucket="hour", from_ts=day_from, to_ts=day_to)
        assert [row["bucket_iso"] for row in hourly["rows"]] == [
            f"{FALL_DAY}T02:00:00+02:00",  # two distinct buckets, not merged ...
            f"{FALL_DAY}T02:00:00+01:00",
        ]
        assert [row["out"] for row in hourly["rows"]] == [1, 1]  # ... and none duplicated

        daily = _timeseries(client, rid, bucket="day", from_ts=day_from, to_ts=day_to)
        assert len(daily["rows"]) == 1
        assert daily["rows"][0]["out"] == 2

    def test_day_bucketing_uses_local_timezone(self, client: TestClient, api_env: ApiEnv) -> None:
        rid, cam = api_env.ids["rest_a"], api_env.ids["cam_a1"]
        with api_env.session() as session:
            # Local 2026-03-28 23:30 and 2026-03-29 00:30; the second one is
            # still on UTC day 03-28 (23:30Z) but must land in the local 03-29
            # day bucket.
            add_event(session, restaurant_id=rid, camera_id=cam, ts=local_ms(2026, 3, 28, 23, 30))
            add_event(session, restaurant_id=rid, camera_id=cam, ts=local_ms(2026, 3, 29, 0, 30))
            session.commit()
        body = _timeseries(
            client,
            rid,
            bucket="day",
            from_ts=local_ms(2026, 3, 28, 0),
            to_ts=local_ms(2026, 3, 30, 0),
        )
        assert [(row["bucket_iso"][:10], row["out"]) for row in body["rows"]] == [
            ("2026-03-28", 1),
            (SPRING_DAY, 1),
        ]


class TestGroupingAndNet:
    HOUR = local_ms(2026, 3, 29, 12)  # fixed local hour on the spring-forward day

    def _seed(self, api_env: ApiEnv) -> None:
        ids = api_env.ids
        rid = ids["rest_a"]
        with api_env.session() as session:
            for offset, camera, category, direction, canonical in (
                (0, "cam_a1", "cat_a_drink", "out", True),
                (1_000, "cam_a1", "cat_a_drink", "out", True),
                (2_000, "cam_a1", "cat_a_drink", "in", True),
                (3_000, "cam_a2", "cat_a_main", "out", True),
                (4_000, "cam_a1", "cat_a_drink", "out", False),  # suppressed duplicate
            ):
                add_event(
                    session,
                    restaurant_id=rid,
                    camera_id=ids[camera],
                    ts=self.HOUR + offset,
                    direction=direction,
                    category_id=ids[category],
                    is_canonical=canonical,
                )
            # B's event in the same hour must never leak into A's stats.
            add_event(
                session,
                restaurant_id=ids["rest_b"],
                camera_id=ids["cam_b1"],
                ts=self.HOUR,
                category_id=ids["cat_b_drink"],
            )
            session.commit()

    def test_ungrouped_net_and_canonical_only(self, client: TestClient, api_env: ApiEnv) -> None:
        self._seed(api_env)
        body = _timeseries(client, api_env.ids["rest_a"], bucket="hour")
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        # 3 canonical out + 1 in; the non-canonical duplicate is not counted.
        assert (row["out"], row["in"], row["net"]) == (3, 1, 2)

    def test_group_by_category(self, client: TestClient, api_env: ApiEnv) -> None:
        self._seed(api_env)
        body = _timeseries(client, api_env.ids["rest_a"], bucket="hour", group_by="category")
        by_group = {row["group"]: row for row in body["rows"]}
        drink = by_group[str(api_env.ids["cat_a_drink"])]
        main = by_group[str(api_env.ids["cat_a_main"])]
        assert (drink["out"], drink["in"], drink["net"]) == (2, 1, 1)
        assert (main["out"], main["in"], main["net"]) == (1, 0, 1)

    def test_group_by_camera(self, client: TestClient, api_env: ApiEnv) -> None:
        self._seed(api_env)
        body = _timeseries(client, api_env.ids["rest_a"], bucket="hour", group_by="camera")
        by_group = {row["group"]: (row["out"], row["in"]) for row in body["rows"]}
        assert by_group == {
            str(api_env.ids["cam_a1"]): (2, 1),
            str(api_env.ids["cam_a2"]): (1, 0),
        }

    def test_group_by_direction(self, client: TestClient, api_env: ApiEnv) -> None:
        self._seed(api_env)
        body = _timeseries(client, api_env.ids["rest_a"], bucket="hour", group_by="direction")
        by_group = {row["group"]: row for row in body["rows"]}
        assert by_group["out"]["out"] == 3 and by_group["out"]["in"] == 0
        assert by_group["in"]["in"] == 1 and by_group["in"]["out"] == 0
        assert (
            client.get(
                f"/api/restaurants/{api_env.ids['rest_a']}/stats/timeseries",
                params={"group_by": "weather"},
            ).status_code
            == 422
        )

    def test_tenant_isolation(self, client: TestClient, api_env: ApiEnv) -> None:
        self._seed(api_env)
        body = _timeseries(client, api_env.ids["rest_b"], bucket="hour")
        assert len(body["rows"]) == 1
        assert body["rows"][0]["out"] == 1  # only B's own event
        assert client.get("/api/restaurants/999999/stats/timeseries").status_code == 404


class TestSummary:
    def test_today_totals_per_category(self, client: TestClient, api_env: ApiEnv) -> None:
        ids = api_env.ids
        rid = ids["rest_a"]
        now_ms = int(time.time() * 1000)
        with api_env.session() as session:
            add_event(
                session,
                restaurant_id=rid,
                camera_id=ids["cam_a1"],
                ts=now_ms,
                direction="out",
                category_id=ids["cat_a_drink"],
            )
            add_event(
                session,
                restaurant_id=rid,
                camera_id=ids["cam_a1"],
                ts=now_ms + 1,
                direction="out",
                category_id=ids["cat_a_drink"],
            )
            add_event(
                session,
                restaurant_id=rid,
                camera_id=ids["cam_a1"],
                ts=now_ms + 2,
                direction="in",
                category_id=ids["cat_a_drink"],
            )
            add_event(
                session,
                restaurant_id=rid,
                camera_id=ids["cam_a2"],
                ts=now_ms + 3,
                direction="out",
                category_id=ids["cat_a_main"],
            )
            # Unmapped class -> NULL category, still visible in the summary.
            add_event(
                session,
                restaurant_id=rid,
                camera_id=ids["cam_a1"],
                ts=now_ms + 4,
                direction="out",
                category_id=None,
                raw_class_name="mystery",
            )
            # Not today's business: non-canonical + yesterday + tenant B.
            add_event(
                session,
                restaurant_id=rid,
                camera_id=ids["cam_a1"],
                ts=now_ms + 5,
                direction="out",
                category_id=ids["cat_a_drink"],
                is_canonical=False,
            )
            add_event(
                session,
                restaurant_id=rid,
                camera_id=ids["cam_a1"],
                ts=now_ms - 3 * 24 * 3_600_000,
                direction="out",
                category_id=ids["cat_a_drink"],
            )
            add_event(
                session,
                restaurant_id=ids["rest_b"],
                camera_id=ids["cam_b1"],
                ts=now_ms,
                direction="out",
                category_id=ids["cat_b_drink"],
            )
            session.commit()

        response = client.get(f"/api/restaurants/{rid}/stats/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["timezone"] == "Europe/Vienna"
        assert body["from_ts"] <= now_ms < body["to_ts"]

        by_key = {row["key"]: row for row in body["categories"]}
        assert (by_key["drink"]["out"], by_key["drink"]["in"], by_key["drink"]["net"]) == (2, 1, 1)
        assert (by_key["main"]["out"], by_key["main"]["net"]) == (1, 1)
        assert (by_key["dessert"]["out"], by_key["dessert"]["in"]) == (0, 0)
        assert (by_key[None]["out"], by_key[None]["category_id"]) == (1, None)
        assert body["total_out"] == 4
        assert body["total_in"] == 1
        assert body["total_net"] == 3
