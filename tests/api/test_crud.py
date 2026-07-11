"""CRUD happy paths, validation errors, pagination and tenant isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.conftest import ApiEnv


class TestRestaurants:
    def test_crud_roundtrip(self, client: TestClient) -> None:
        created = client.post(
            "/api/restaurants",
            json={"slug": "new-place", "name": "New Place", "timezone": "Europe/Berlin"},
        )
        assert created.status_code == 201
        rid = created.json()["id"]

        got = client.get(f"/api/restaurants/{rid}")
        assert got.status_code == 200
        assert got.json()["timezone"] == "Europe/Berlin"

        updated = client.put(f"/api/restaurants/{rid}", json={"name": "Renamed"})
        assert updated.status_code == 200
        assert updated.json()["name"] == "Renamed"
        assert updated.json()["timezone"] == "Europe/Berlin"  # untouched

        assert client.delete(f"/api/restaurants/{rid}").status_code == 204
        assert client.get(f"/api/restaurants/{rid}").status_code == 404

    def test_duplicate_slug_conflicts(self, client: TestClient) -> None:
        body = {"slug": "resta", "name": "Copycat"}
        assert client.post("/api/restaurants", json=body).status_code == 409

    def test_bad_timezone_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/restaurants", json={"slug": "tzless", "name": "x", "timezone": "Mars/Olympus"}
        )
        assert response.status_code == 422

    def test_list_pagination(self, client: TestClient) -> None:
        listed = client.get("/api/restaurants", params={"limit": 1, "offset": 0}).json()
        assert listed["total"] == 2
        assert len(listed["items"]) == 1
        second = client.get("/api/restaurants", params={"limit": 1, "offset": 1}).json()
        assert second["items"][0]["id"] != listed["items"][0]["id"]
        assert client.get("/api/restaurants", params={"limit": 0}).status_code == 422


class TestCameras:
    def test_crud_roundtrip(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        created = client.post(
            f"/api/restaurants/{rid}/cameras",
            json={
                "name": "New cam",
                "source_type": "rtsp",
                "source_url": "rtsp://cam.local/stream",
                "exit_group_id": api_env.ids["group_a"],
            },
        )
        assert created.status_code == 201
        camera_id = created.json()["id"]

        updated = client.put(f"/api/restaurants/{rid}/cameras/{camera_id}", json={"target_fps": 10})
        assert updated.status_code == 200
        assert updated.json()["target_fps"] == 10

        assert client.delete(f"/api/restaurants/{rid}/cameras/{camera_id}").status_code == 204
        assert client.get(f"/api/restaurants/{rid}/cameras/{camera_id}").status_code == 404

    def test_usb_source_must_be_numeric(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/cameras",
            json={"name": "usb", "source_type": "usb", "source_url": "/dev/video0"},
        )
        assert response.status_code == 422

    def test_tenant_isolation(self, client: TestClient, api_env: ApiEnv) -> None:
        rid_b, cam_a = api_env.ids["rest_b"], api_env.ids["cam_a1"]
        # B cannot see, modify or delete A's camera.
        assert client.get(f"/api/restaurants/{rid_b}/cameras/{cam_a}").status_code == 404
        assert (
            client.put(
                f"/api/restaurants/{rid_b}/cameras/{cam_a}", json={"name": "hijack"}
            ).status_code
            == 404
        )
        assert client.delete(f"/api/restaurants/{rid_b}/cameras/{cam_a}").status_code == 404
        listed = client.get(f"/api/restaurants/{rid_b}/cameras").json()
        assert {row["id"] for row in listed["items"]} == {api_env.ids["cam_b1"]}

    def test_cross_tenant_exit_group_reference_rejected(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        rid_b = api_env.ids["rest_b"]
        response = client.post(
            f"/api/restaurants/{rid_b}/cameras",
            json={
                "name": "bad ref",
                "source_type": "file",
                "source_url": "./x.mp4",
                "exit_group_id": api_env.ids["group_a"],  # belongs to A
            },
        )
        assert response.status_code == 404

    def test_status_for_never_started_camera(self, client: TestClient, api_env: ApiEnv) -> None:
        response = client.get(f"/api/cameras/{api_env.ids['cam_a1']}/status")
        assert response.status_code == 200
        assert response.json()["running"] is False
        assert client.get("/api/cameras/999999/status").status_code == 404


class TestExitGroups:
    def test_crud_roundtrip(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        created = client.post(
            f"/api/restaurants/{rid}/exit-groups",
            json={"name": "Pass 2", "dedup_window_ms": 1500, "dedup_strategy": "first_wins"},
        )
        assert created.status_code == 201
        group_id = created.json()["id"]
        assert created.json()["dedup_strategy"] == "first_wins"

        updated = client.put(
            f"/api/restaurants/{rid}/exit-groups/{group_id}", json={"dedup_window_ms": 900}
        )
        assert updated.json()["dedup_window_ms"] == 900

        assert client.delete(f"/api/restaurants/{rid}/exit-groups/{group_id}").status_code == 204

    def test_bad_strategy_rejected(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/exit-groups",
            json={"name": "x", "dedup_strategy": "coin_flip"},
        )
        assert response.status_code == 422

    def test_tenant_isolation(self, client: TestClient, api_env: ApiEnv) -> None:
        rid_b, group_a = api_env.ids["rest_b"], api_env.ids["group_a"]
        assert client.get(f"/api/restaurants/{rid_b}/exit-groups/{group_a}").status_code == 404


class TestLines:
    def test_crud_roundtrip(self, client: TestClient, api_env: ApiEnv) -> None:
        cam = api_env.ids["cam_a1"]
        created = client.post(
            f"/api/cameras/{cam}/lines",
            json={"name": "Diag", "x1": 0.1, "y1": 0.2, "x2": 0.9, "y2": 0.8},
        )
        assert created.status_code == 201
        line_id = created.json()["id"]
        assert created.json()["count_directions"] == "out,in"

        updated = client.put(f"/api/cameras/{cam}/lines/{line_id}", json={"x1": 0.25})
        assert updated.status_code == 200
        assert updated.json()["x1"] == 0.25
        assert updated.json()["y1"] == 0.2

        assert client.delete(f"/api/cameras/{cam}/lines/{line_id}").status_code == 204
        assert client.get(f"/api/cameras/{cam}/lines/{line_id}").status_code == 404

    def test_coordinates_validated(self, client: TestClient, api_env: ApiEnv) -> None:
        cam = api_env.ids["cam_a1"]
        bad = {"x1": 1.5, "y1": 0.0, "x2": 0.5, "y2": 1.0}
        assert client.post(f"/api/cameras/{cam}/lines", json=bad).status_code == 422
        bad_dirs = {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0, "count_directions": "sideways"}
        assert client.post(f"/api/cameras/{cam}/lines", json=bad_dirs).status_code == 422

    def test_tenant_isolation_via_camera(self, client: TestClient, api_env: ApiEnv) -> None:
        # A's line is not reachable through B's camera.
        cam_b, line_a = api_env.ids["cam_b1"], api_env.ids["line_a1"]
        assert client.get(f"/api/cameras/{cam_b}/lines/{line_a}").status_code == 404
        assert (
            client.put(f"/api/cameras/{cam_b}/lines/{line_a}", json={"x1": 0.0}).status_code == 404
        )


class TestCategoriesAndMenu:
    def test_category_crud_and_conflict(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        created = client.post(
            f"/api/restaurants/{rid}/categories",
            json={"key": "soup", "name_de": "Suppe", "name_en": "Soup"},
        )
        assert created.status_code == 201
        # Duplicate key within the tenant conflicts ...
        dup = client.post(
            f"/api/restaurants/{rid}/categories",
            json={"key": "soup", "name_de": "S", "name_en": "S"},
        )
        assert dup.status_code == 409
        # ... but the same key in another tenant is fine.
        other = client.post(
            f"/api/restaurants/{api_env.ids['rest_b']}/categories",
            json={"key": "soup", "name_de": "S", "name_en": "S"},
        )
        assert other.status_code == 201

    def test_menu_item_cross_tenant_category_rejected(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        rid_b = api_env.ids["rest_b"]
        response = client.post(
            f"/api/restaurants/{rid_b}/menu-items",
            json={"category_id": api_env.ids["cat_a_drink"], "name": "Stolen"},
        )
        assert response.status_code == 404

    def test_menu_item_crud(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        created = client.post(
            f"/api/restaurants/{rid}/menu-items",
            json={"category_id": api_env.ids["cat_a_main"], "name": "Lasagne", "price": 12.5},
        )
        assert created.status_code == 201
        item_id = created.json()["id"]
        dup = client.post(
            f"/api/restaurants/{rid}/menu-items",
            json={"category_id": api_env.ids["cat_a_main"], "name": "Lasagne"},
        )
        assert dup.status_code == 409
        updated = client.put(f"/api/restaurants/{rid}/menu-items/{item_id}", json={"price": 13.0})
        assert updated.json()["price"] == 13.0
        assert client.delete(f"/api/restaurants/{rid}/menu-items/{item_id}").status_code == 204


class TestMappings:
    def test_crud_and_conflict(self, client: TestClient, api_env: ApiEnv) -> None:
        rid, model = api_env.ids["rest_a"], api_env.ids["model_shared"]
        created = client.post(
            f"/api/restaurants/{rid}/mappings",
            json={
                "model_id": model,
                "model_class_id": 1,
                "model_class_name": "bowl",
                "category_id": api_env.ids["cat_a_main"],
            },
        )
        assert created.status_code == 201
        mapping_id = created.json()["id"]
        # Duplicate (model, class) for the tenant conflicts.
        dup = client.post(
            f"/api/restaurants/{rid}/mappings",
            json={
                "model_id": model,
                "model_class_id": 1,
                "model_class_name": "bowl",
                "category_id": api_env.ids["cat_a_main"],
            },
        )
        assert dup.status_code == 409
        updated = client.put(
            f"/api/restaurants/{rid}/mappings/{mapping_id}", json={"min_confidence": 0.6}
        )
        assert updated.json()["min_confidence"] == 0.6
        assert client.delete(f"/api/restaurants/{rid}/mappings/{mapping_id}").status_code == 204

    def test_mapping_requires_target(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/mappings",
            json={
                "model_id": api_env.ids["model_shared"],
                "model_class_id": 7,
                "model_class_name": "x",
            },
        )
        assert response.status_code == 422

    def test_bulk_replace_set(self, client: TestClient, api_env: ApiEnv) -> None:
        rid, model = api_env.ids["rest_a"], api_env.ids["model_shared"]
        body = [
            {
                "model_class_id": 0,
                "model_class_name": "cup",
                "category_id": api_env.ids["cat_a_drink"],
                "min_confidence": 0.5,
            },
            {
                "model_class_id": 1,
                "model_class_name": "bowl",
                "category_id": api_env.ids["cat_a_main"],
            },
        ]
        response = client.put(f"/api/restaurants/{rid}/mappings/model/{model}", json=body)
        assert response.status_code == 200
        assert response.json()["total"] == 2
        listed = client.get(f"/api/restaurants/{rid}/mappings", params={"model_id": model}).json()
        assert listed["total"] == 2  # old single mapping replaced by the new set
        assert {row["model_class_name"] for row in listed["items"]} == {"cup", "bowl"}

    def test_cross_tenant_category_in_mapping_rejected(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        rid_b = api_env.ids["rest_b"]
        response = client.post(
            f"/api/restaurants/{rid_b}/mappings",
            json={
                "model_id": api_env.ids["model_shared"],
                "model_class_id": 0,
                "model_class_name": "cup",
                "category_id": api_env.ids["cat_a_drink"],  # A's category
            },
        )
        assert response.status_code == 404


class TestModelsRegistry:
    def test_list_includes_shared_and_own(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        listed = client.get(f"/api/restaurants/{rid}/models").json()
        assert {row["id"] for row in listed["items"]} == {
            api_env.ids["model_shared"],
            api_env.ids["model_a"],
        }
        # B sees only the shared model, never A's finetuned one.
        listed_b = client.get(f"/api/restaurants/{api_env.ids['rest_b']}/models").json()
        assert {row["id"] for row in listed_b["items"]} == {api_env.ids["model_shared"]}

    def test_activate_deactivates_others(self, client: TestClient, api_env: ApiEnv) -> None:
        rid, own = api_env.ids["rest_a"], api_env.ids["model_a"]
        response = client.post(f"/api/restaurants/{rid}/models/{own}/activate")
        assert response.status_code == 200
        assert response.json()["is_active"] is True
        by_id = {
            row["id"]: row["is_active"]
            for row in client.get(f"/api/restaurants/{rid}/models").json()["items"]
        }
        assert by_id == {api_env.ids["model_shared"]: False, own: True}

    def test_b_cannot_activate_foreign_model(self, client: TestClient, api_env: ApiEnv) -> None:
        rid_b, own_a = api_env.ids["rest_b"], api_env.ids["model_a"]
        assert client.post(f"/api/restaurants/{rid_b}/models/{own_a}/activate").status_code == 404


class TestSessions:
    def test_crud_and_end(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        created = client.post(f"/api/restaurants/{rid}/sessions", json={"name": "Lunch"})
        assert created.status_code == 201
        session_id = created.json()["id"]
        assert created.json()["started_ts"] > 0
        assert created.json()["ended_ts"] is None

        ended = client.post(f"/api/restaurants/{rid}/sessions/{session_id}/end")
        assert ended.status_code == 200
        assert ended.json()["ended_ts"] is not None
        # Ending twice conflicts.
        assert client.post(f"/api/restaurants/{rid}/sessions/{session_id}/end").status_code == 409
        assert client.delete(f"/api/restaurants/{rid}/sessions/{session_id}").status_code == 204

    def test_tenant_isolation(self, client: TestClient, api_env: ApiEnv) -> None:
        rid_a, rid_b = api_env.ids["rest_a"], api_env.ids["rest_b"]
        session_id = client.post(
            f"/api/restaurants/{rid_a}/sessions", json={"name": "Dinner"}
        ).json()["id"]
        assert client.get(f"/api/restaurants/{rid_b}/sessions/{session_id}").status_code == 404
        assert client.post(f"/api/restaurants/{rid_b}/sessions/{session_id}/end").status_code == 404
