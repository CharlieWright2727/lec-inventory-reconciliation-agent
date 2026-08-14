import json
from pathlib import Path

from fastapi.testclient import TestClient

from warehouse.app import create_app
from warehouse.loader import STARTING_VERSION, load_inventory_records


ROOT = Path(__file__).parents[1]
PRODUCTS_PATH = ROOT / "warehouse" / "data" / "products.json"
SCENARIO_DIR = ROOT / "scenarios" / "one-stale-warehouse"
EXPECTATION_PATH = ROOT / "tests" / "expectations" / "one-stale-warehouse.json"
WAREHOUSE_IDS = ("warehouse-a", "warehouse-b", "warehouse-c")


def load_scenario() -> dict[str, tuple[dict, dict]]:
    return {
        warehouse_id: load_inventory_records(
            warehouse_id,
            PRODUCTS_PATH,
            SCENARIO_DIR / f"{warehouse_id}.json",
        )
        for warehouse_id in WAREHOUSE_IDS
    }


def logical_state(record: object) -> dict:
    return {
        "product": record.product.model_dump(mode="json"),
        "inventory": record.inventory.model_dump(mode="json"),
        "version": record.state.version,
        "updated_at": record.state.updated_at.isoformat(),
        "updated_by": record.state.updated_by,
        "last_event": record.last_event.model_dump(mode="json"),
        "sync": record.sync.model_dump(mode="json"),
        "data_quality": record.data_quality.model_dump(mode="json"),
    }


def sku_summary(record: object) -> dict:
    return {
        "version": record.state.version,
        **record.inventory.model_dump(mode="json"),
        "event_cursor": record.sync.event_cursor,
    }


def test_scenario_files_load_as_valid_complete_warehouse_states() -> None:
    expected_skus = {
        product["sku"]
        for product in json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))[
            "products"
        ]
    }

    for records, event_history in load_scenario().values():
        assert set(records) == expected_skus
        assert set(event_history) == expected_skus
        assert len(records) == 10


def test_scenario_contains_only_the_intended_logical_conflict() -> None:
    warehouse_states = load_scenario()
    conflicting_skus = []

    for sku in warehouse_states["warehouse-a"][0]:
        states = {
            json.dumps(logical_state(records[sku]), sort_keys=True)
            for records, _ in warehouse_states.values()
        }
        if len(states) > 1:
            conflicting_skus.append(sku)

    assert conflicting_skus == ["SKU-001"]


def test_sku_001_state_and_event_evidence_are_coherent() -> None:
    warehouse_states = load_scenario()
    expected_newer = {
        "version": 42,
        "on_hand": 120,
        "reserved": 8,
        "available": 112,
        "event_cursor": 1042,
    }
    expected_older = {
        "version": 41,
        "on_hand": 100,
        "reserved": 8,
        "available": 92,
        "event_cursor": 1041,
    }

    for warehouse_id in ("warehouse-a", "warehouse-c"):
        records, histories = warehouse_states[warehouse_id]
        assert sku_summary(records["SKU-001"]) == expected_newer
        assert records["SKU-001"].last_event.event_id == "evt-1042"
        assert [event.event_id for event in histories["SKU-001"]] == [
            "evt-1041",
            "evt-1042",
        ]
        assert histories["SKU-001"][-1].quantity_delta == 20

    records, histories = warehouse_states["warehouse-b"]
    assert sku_summary(records["SKU-001"]) == expected_older
    assert records["SKU-001"].last_event.event_id == "evt-1041"
    assert [event.event_id for event in histories["SKU-001"]] == ["evt-1041"]
    assert "evt-1042" not in {
        event.event_id for event in histories["SKU-001"]
    }


def test_expectation_matches_observable_scenario_facts() -> None:
    expectation = json.loads(EXPECTATION_PATH.read_text(encoding="utf-8"))
    warehouse_states = load_scenario()
    records_by_warehouse = {
        warehouse_id: records
        for warehouse_id, (records, _) in warehouse_states.items()
    }
    conflicting_skus = [
        sku
        for sku in records_by_warehouse["warehouse-a"]
        if len(
            {
                json.dumps(logical_state(records[sku]), sort_keys=True)
                for records in records_by_warehouse.values()
            }
        )
        > 1
    ]

    assert expectation["scenario"] == "one-stale-warehouse"
    assert expectation["product_count"] == len(
        records_by_warehouse["warehouse-a"]
    )
    assert expectation["consistent_sku_count"] == 9
    assert expectation["conflicting_skus"] == conflicting_skus
    assert expectation["sku_001"]["agreed_warehouses"] == [
        "warehouse-a",
        "warehouse-c",
    ]
    assert expectation["sku_001"]["stale_warehouse"] == "warehouse-b"
    assert expectation["sku_001"]["preferred_state"] == sku_summary(
        records_by_warehouse["warehouse-a"]["SKU-001"]
    )
    assert expectation["sku_001"]["stale_state"] == sku_summary(
        records_by_warehouse["warehouse-b"]["SKU-001"]
    )
    assert expectation["sku_001"]["intended_reconciliation"] == {
        "warehouse": "warehouse-b",
        "from_version": 41,
        "to_version": 42,
    }
    assert expectation["final_intended_result"] == {
        "all_warehouses_logically_consistent": True
    }


def test_default_loader_remains_available_without_a_scenario() -> None:
    records, event_history = load_inventory_records("warehouse-default", PRODUCTS_PATH)

    assert len(records) == 10
    assert set(records) == set(event_history)
    assert all(record.state.version == STARTING_VERSION for record in records.values())
    assert records["SKU-001"].inventory.model_dump() == {
        "on_hand": 100,
        "reserved": 1,
        "available": 99,
    }


def test_warehouse_api_can_start_from_a_selected_scenario_file() -> None:
    client = TestClient(
        create_app(
            warehouse_id="warehouse-b",
            scenario_data_path=SCENARIO_DIR / "warehouse-b.json",
        )
    )

    catalogue = client.get("/inventory").json()
    sku_001 = client.get("/inventory/SKU-001").json()
    events = client.get("/inventory/SKU-001/events").json()["events"]

    assert len(catalogue["items"]) == 10
    assert sku_001["state"]["version"] == 41
    assert sku_001["inventory"] == {
        "on_hand": 100,
        "reserved": 8,
        "available": 92,
    }
    assert [event["event_id"] for event in events] == ["evt-1041"]
