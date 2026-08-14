from fastapi.testclient import TestClient

from warehouse.app import create_app


def make_client(warehouse_id: str = "warehouse-test") -> TestClient:
    return TestClient(create_app(warehouse_id=warehouse_id))


def update_payload(
    *,
    expected_version: int = 41,
    target_version: int = 42,
    on_hand: int = 120,
    reserved: int = 8,
    available: int = 112,
) -> dict:
    return {
        "expected_current_version": expected_version,
        "target_version": target_version,
        "inventory": {
            "on_hand": on_hand,
            "reserved": reserved,
            "available": available,
        },
        "source": {
            "system_id": "warehouse-source",
            "snapshot_id": "snap-source-SKU001-42",
            "event_id": "evt-source-42",
        },
        "reason": "stale_inventory_reconciliation",
    }


def sku_state(response_body: dict) -> dict:
    return {
        key: response_body[key]
        for key in (
            "product",
            "inventory",
            "state",
            "last_event",
            "sync",
            "data_quality",
        )
    }


def test_health_identifies_warehouse() -> None:
    response = make_client("warehouse-a").get("/health")

    assert response.status_code == 200
    assert response.json() == {"system_id": "warehouse-a", "status": "healthy"}


def test_catalogue_returns_multiple_products() -> None:
    response = make_client().get("/inventory")

    assert response.status_code == 200
    body = response.json()
    assert body["system"]["id"] == "warehouse-test"
    assert len(body["items"]) == 10
    assert body["items"][0]["product"]["sku"] == "SKU-001"
    assert body["capabilities"]["writable"] is True
    assert "system" not in body["items"][0]
    assert "capabilities" not in body["items"][0]


def test_get_known_sku_returns_current_record() -> None:
    response = make_client().get("/inventory/SKU-001")

    assert response.status_code == 200
    body = response.json()
    assert body["product"] == {
        "sku": "SKU-001",
        "name": "Wireless Keyboard",
        "barcode": "5012345678901",
    }
    assert body["state"]["version"] == 41
    assert body["inventory"]["available"] == (
        body["inventory"]["on_hand"] - body["inventory"]["reserved"]
    )


def test_get_unknown_sku_returns_404() -> None:
    response = make_client().get("/inventory/DOES-NOT-EXIST")

    assert response.status_code == 404


def test_event_history_is_sku_scoped_and_respects_limit() -> None:
    client = make_client()
    assert client.put("/inventory/SKU-001", json=update_payload()).status_code == 200

    response = client.get("/inventory/SKU-001/events?limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["sku"] == "SKU-001"
    assert len(body["events"]) == 1
    assert body["events"][0]["type"] == "stock_adjustment"


def test_event_history_for_unknown_sku_returns_404() -> None:
    response = make_client().get("/inventory/DOES-NOT-EXIST/events?limit=5")

    assert response.status_code == 404


def test_valid_write_adopts_target_version() -> None:
    client = make_client()

    response = client.put("/inventory/SKU-001", json=update_payload())

    assert response.status_code == 200
    assert response.json()["previous_version"] == 41
    assert response.json()["new_version"] == 42
    updated = client.get("/inventory/SKU-001").json()
    assert updated["state"]["version"] == 42
    assert updated["inventory"] == {
        "on_hand": 120,
        "reserved": 8,
        "available": 112,
    }
    assert updated["last_event"]["type"] == "stock_adjustment"


def test_same_version_with_different_inventory_returns_409_without_changes() -> None:
    client = make_client()
    before = sku_state(client.get("/inventory/SKU-001").json())
    events_before = client.get("/inventory/SKU-001/events").json()

    response = client.put(
        "/inventory/SKU-001",
        json=update_payload(target_version=41),
    )

    assert response.status_code == 409
    assert "same logical version" in response.json()["message"]
    assert sku_state(client.get("/inventory/SKU-001").json()) == before
    assert client.get("/inventory/SKU-001/events").json() == events_before


def test_same_version_with_identical_inventory_is_idempotent() -> None:
    client = make_client()
    before = sku_state(client.get("/inventory/SKU-001").json())
    events_before = client.get("/inventory/SKU-001/events").json()
    inventory = before["inventory"]

    response = client.put(
        "/inventory/SKU-001",
        json=update_payload(
            target_version=before["state"]["version"],
            on_hand=inventory["on_hand"],
            reserved=inventory["reserved"],
            available=inventory["available"],
        ),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unchanged"
    assert response.json()["previous_version"] == 41
    assert response.json()["new_version"] == 41
    assert sku_state(client.get("/inventory/SKU-001").json()) == before
    assert client.get("/inventory/SKU-001/events").json() == events_before


def test_stale_expected_version_returns_409_without_changing_state() -> None:
    client = make_client()
    assert client.put("/inventory/SKU-001", json=update_payload()).status_code == 200
    before_conflict = sku_state(client.get("/inventory/SKU-001").json())

    response = client.put(
        "/inventory/SKU-001",
        json=update_payload(on_hand=130, reserved=8, available=122),
    )

    assert response.status_code == 409
    assert response.json()["current_version"] == 42
    assert sku_state(client.get("/inventory/SKU-001").json()) == before_conflict


def test_target_version_cannot_move_backwards() -> None:
    client = make_client()

    response = client.put(
        "/inventory/SKU-001",
        json=update_payload(expected_version=41, target_version=40),
    )

    assert response.status_code == 409
    assert client.get("/inventory/SKU-001").json()["state"]["version"] == 41


def test_reserved_cannot_exceed_on_hand() -> None:
    client = make_client()
    before = sku_state(client.get("/inventory/SKU-001").json())

    response = client.put(
        "/inventory/SKU-001",
        json=update_payload(on_hand=10, reserved=12, available=0),
    )

    assert response.status_code == 422
    assert sku_state(client.get("/inventory/SKU-001").json()) == before


def test_available_must_equal_on_hand_minus_reserved() -> None:
    client = make_client()
    before = sku_state(client.get("/inventory/SKU-001").json())

    response = client.put(
        "/inventory/SKU-001",
        json=update_payload(on_hand=10, reserved=2, available=9),
    )

    assert response.status_code == 422
    assert sku_state(client.get("/inventory/SKU-001").json()) == before


def test_updating_one_sku_does_not_change_another() -> None:
    client = make_client()
    sku_002_before = sku_state(client.get("/inventory/SKU-002").json())

    assert client.put("/inventory/SKU-001", json=update_payload()).status_code == 200

    assert sku_state(client.get("/inventory/SKU-002").json()) == sku_002_before


def test_app_instances_keep_independent_runtime_state() -> None:
    warehouse_a = make_client("warehouse-a")
    warehouse_b = make_client("warehouse-b")

    assert warehouse_a.put("/inventory/SKU-001", json=update_payload()).status_code == 200

    assert warehouse_a.get("/inventory/SKU-001").json()["state"]["version"] == 42
    assert warehouse_b.get("/inventory/SKU-001").json()["state"]["version"] == 41
