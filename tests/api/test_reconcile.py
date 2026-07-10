"""POS reconciliation: CSV upload, per-category variance report, unknown
categories, malformed input, and the persisted run history."""

from __future__ import annotations

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.api.conftest import ApiEnv, add_event
from toskana.db.models import ReconcileRun

VIENNA = ZoneInfo("Europe/Vienna")
DAY = "2026-07-09"


def _upload(client: TestClient, restaurant_id: int, content: str, **params: object) -> object:
    return client.post(
        f"/api/restaurants/{restaurant_id}/reconcile",
        params=params,
        files={"file": ("pos.csv", content.encode("utf-8"), "text/csv")},
    )


def _seed_day(api_env: ApiEnv) -> None:
    """A: 3x drink out + 1x drink in + 1x main out on 2026-07-09 (local)."""
    ids = api_env.ids
    noon = int(datetime(2026, 7, 9, 12, 0, tzinfo=VIENNA).timestamp() * 1000)
    with api_env.session() as session:
        for offset, category, direction in (
            (0, "cat_a_drink", "out"),
            (1_000, "cat_a_drink", "out"),
            (2_000, "cat_a_drink", "out"),
            (3_000, "cat_a_drink", "in"),
            (4_000, "cat_a_main", "out"),
        ):
            add_event(
                session,
                restaurant_id=ids["rest_a"],
                camera_id=ids["cam_a1"],
                ts=noon + offset,
                direction=direction,
                category_id=ids[category],
            )
        # A non-canonical duplicate and a B event must not count.
        add_event(
            session,
            restaurant_id=ids["rest_a"],
            camera_id=ids["cam_a1"],
            ts=noon + 5_000,
            category_id=ids["cat_a_drink"],
            is_canonical=False,
        )
        add_event(
            session,
            restaurant_id=ids["rest_b"],
            camera_id=ids["cam_b1"],
            ts=noon + 6_000,
            category_id=ids["cat_b_drink"],
        )
        session.commit()


class TestReconcile:
    def test_variance_report(self, client: TestClient, api_env: ApiEnv) -> None:
        _seed_day(api_env)
        csv_body = f"category_key,quantity,date\ndrink,2,{DAY}\nmain,1,{DAY}\ndessert,0,{DAY}\n"
        response = _upload(client, api_env.ids["rest_a"], csv_body)
        assert response.status_code == 200
        body = response.json()
        assert body["timezone"] == "Europe/Vienna"
        by_key = {row["category_key"]: row for row in body["rows"]}

        drink = by_key["drink"]
        assert drink["date"] == DAY
        assert drink["category_id"] == api_env.ids["cat_a_drink"]
        assert (drink["counted_out"], drink["counted_in"], drink["counted_net"]) == (3, 1, 2)
        assert drink["pos_quantity"] == 2.0
        assert drink["variance"] == 0.0  # counted_net - pos_quantity
        assert drink["variance_pct"] == 0.0
        assert drink["unknown_category"] is False

        main = by_key["main"]
        assert (main["counted_net"], main["pos_quantity"], main["variance"]) == (1, 1.0, 0.0)

        dessert = by_key["dessert"]
        assert dessert["counted_net"] == 0
        assert dessert["variance_pct"] is None  # pos_quantity == 0

        assert body["total_pos_quantity"] == 3.0
        assert body["total_counted_net"] == 3

    def test_shortage_variance_pct(self, client: TestClient, api_env: ApiEnv) -> None:
        _seed_day(api_env)
        response = _upload(
            client, api_env.ids["rest_a"], f"category_key,quantity,date\ndrink,4,{DAY}\n"
        )
        row = response.json()["rows"][0]
        assert row["counted_net"] == 2
        assert row["variance"] == -2.0
        assert row["variance_pct"] == -50.0

    def test_unknown_category_reported_not_crashed(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        _seed_day(api_env)
        response = _upload(
            client, api_env.ids["rest_a"], f"category_key,quantity,date\ntruffle,5,{DAY}\n"
        )
        assert response.status_code == 200
        row = response.json()["rows"][0]
        assert row["unknown_category"] is True
        assert row["category_id"] is None
        assert (row["counted_out"], row["counted_in"], row["counted_net"]) == (0, 0, 0)
        assert row["variance"] == -5.0

    def test_date_query_param_default(self, client: TestClient, api_env: ApiEnv) -> None:
        _seed_day(api_env)
        # Rows without a date column fall back to the ?date= query parameter.
        response = _upload(
            client, api_env.ids["rest_a"], "category_key,quantity\ndrink,2\n", date=DAY
        )
        assert response.status_code == 200
        body = response.json()
        assert body["default_date"] == DAY
        assert body["rows"][0]["counted_net"] == 2

    def test_malformed_csv_422(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        # Missing required columns.
        response = _upload(client, rid, "foo,bar\n1,2\n")
        assert response.status_code == 422
        assert "missing column" in response.json()["detail"]
        # Non-numeric quantity.
        assert _upload(client, rid, "category_key,quantity\ndrink,many\n").status_code == 422
        # Empty category key.
        assert _upload(client, rid, "category_key,quantity\n,3\n").status_code == 422
        # Bad date cell / bad date param.
        assert (
            _upload(client, rid, "category_key,quantity,date\ndrink,1,09.07.2026\n").status_code
            == 422
        )
        assert (
            _upload(client, rid, "category_key,quantity\ndrink,1\n", date="not-a-date").status_code
            == 422
        )
        # Not UTF-8.
        bad_bytes = b"category_key,quantity\n\xff\xfe,1\n"
        response = client.post(
            f"/api/restaurants/{rid}/reconcile",
            files={"file": ("pos.csv", bad_bytes, "text/csv")},
        )
        assert response.status_code == 422

    def test_tenant_isolation(self, client: TestClient, api_env: ApiEnv) -> None:
        _seed_day(api_env)
        # B has a 'drink' category too, but only B's single event counts.
        response = _upload(
            client, api_env.ids["rest_b"], f"category_key,quantity,date\ndrink,1,{DAY}\n"
        )
        assert response.json()["rows"][0]["counted_net"] == 1
        assert (
            client.post(
                "/api/restaurants/999999/reconcile",
                files={"file": ("pos.csv", b"category_key,quantity\n", "text/csv")},
            ).status_code
            == 404
        )


class TestReconcileRuns:
    def test_upload_persists_run(self, client: TestClient, api_env: ApiEnv) -> None:
        _seed_day(api_env)
        rid = api_env.ids["rest_a"]
        body = _upload(
            client, rid, f"category_key,quantity,date\ndrink,2,{DAY}\nmain,1,{DAY}\n"
        ).json()
        assert isinstance(body["run_id"], str) and len(body["run_id"]) == 26  # ULID

        with api_env.session() as session:
            (run,) = session.scalars(select(ReconcileRun)).all()
        assert run.id == body["run_id"]
        assert run.restaurant_id == rid
        assert run.date == body["default_date"]
        assert run.uploaded_filename == "pos.csv"
        assert run.total_pos_quantity == 3.0
        assert run.total_counted_net == 3
        assert run.created_ts > 0
        assert json.loads(run.rows_json) == body["rows"]  # exact row payload round-trips

    def test_history_lists_newest_first(self, client: TestClient, api_env: ApiEnv) -> None:
        _seed_day(api_env)
        rid = api_env.ids["rest_a"]
        first = _upload(client, rid, "category_key,quantity\ndrink,2\n", date=DAY).json()
        time.sleep(0.002)  # distinct created_ts (ms) => deterministic ordering
        second = _upload(client, rid, "category_key,quantity\nmain,1\n", date=DAY).json()

        history = client.get(f"/api/restaurants/{rid}/reconcile-runs").json()
        assert history["total"] == 2
        assert [run["id"] for run in history["items"]] == [second["run_id"], first["run_id"]]
        newest = history["items"][0]
        assert newest["uploaded_filename"] == "pos.csv"
        assert newest["date"] == DAY
        assert newest["total_counted_net"] == 1
        assert newest["rows"][0]["category_key"] == "main"
        assert newest["rows"][0]["variance"] == 0.0

    def test_history_tenant_isolation(self, client: TestClient, api_env: ApiEnv) -> None:
        _seed_day(api_env)
        _upload(client, api_env.ids["rest_a"], f"category_key,quantity,date\ndrink,2,{DAY}\n")
        empty = client.get(f"/api/restaurants/{api_env.ids['rest_b']}/reconcile-runs").json()
        assert empty["total"] == 0 and empty["items"] == []
        assert client.get("/api/restaurants/999999/reconcile-runs").status_code == 404
