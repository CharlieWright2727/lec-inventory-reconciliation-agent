import json
from pathlib import Path

from fastapi.testclient import TestClient

from warehouse.app import create_app
from warehouse.loader import STARTING_VERSION, load_inventory_records


ROOT = Path(__file__).parents[1]
PRODUCTS_PATH = ROOT / "warehouse" / "data" / "products.json"
SCENARIOS_ROOT = ROOT / "scenarios"
EXPECTATIONS_ROOT = ROOT / "tests" / "expectations"
DEFAULT_SCENARIO = "one-stale-warehouse"
SCENARIO_DIR = SCENARIOS_ROOT / DEFAULT_SCENARIO
EXPECTATION_PATH = EXPECTATIONS_ROOT / f"{DEFAULT_SCENARIO}.json"
WAREHOUSE_IDS = ("warehouse-a", "warehouse-b", "warehouse-c")


def expected_catalogue_skus() -> set[str]:
    return {
        product["sku"]
        for product in json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))[
            "products"
        ]
    }


def load_scenario(
    scenario_name: str = DEFAULT_SCENARIO,
) -> dict[str, tuple[dict, dict]]:
    scenario_dir = SCENARIOS_ROOT / scenario_name
    return {
        warehouse_id: load_inventory_records(
            warehouse_id,
            PRODUCTS_PATH,
            scenario_dir / f"{warehouse_id}.json",
        )
        for warehouse_id in WAREHOUSE_IDS
    }


def find_conflicting_skus(
    warehouse_states: dict[str, tuple[dict, dict]],
) -> list[str]:
    return [
        sku
        for sku in warehouse_states["warehouse-a"][0]
        if len(
            {
                json.dumps(logical_state(records[sku]), sort_keys=True)
                for records, _ in warehouse_states.values()
            }
        )
        > 1
    ]


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
    expected_skus = expected_catalogue_skus()

    for records, event_history in load_scenario().values():
        assert set(records) == expected_skus
        assert set(event_history) == expected_skus
        assert len(records) == 10


def test_scenario_contains_only_the_intended_logical_conflict() -> None:
    warehouse_states = load_scenario()

    assert find_conflicting_skus(warehouse_states) == ["SKU-001"]


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
    conflicting_skus = find_conflicting_skus(warehouse_states)

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


def test_newer_singleton_loads_complete_warehouse_states() -> None:
    expected_skus = expected_catalogue_skus()

    for records, event_history in load_scenario("newer-singleton").values():
        assert set(records) == expected_skus
        assert set(event_history) == expected_skus
        assert len(records) == 10


def test_newer_singleton_contains_only_sku_001_as_a_conflict() -> None:
    warehouse_states = load_scenario("newer-singleton")

    assert find_conflicting_skus(warehouse_states) == ["SKU-001"]


def test_newer_singleton_states_and_event_evidence_are_coherent() -> None:
    warehouse_states = load_scenario("newer-singleton")
    expected_majority = {
        "version": 42,
        "on_hand": 120,
        "reserved": 8,
        "available": 112,
        "event_cursor": 1042,
    }
    expected_singleton = {
        "version": 43,
        "on_hand": 105,
        "reserved": 8,
        "available": 97,
        "event_cursor": 1043,
    }

    for warehouse_id in ("warehouse-a", "warehouse-b"):
        records, histories = warehouse_states[warehouse_id]
        record = records["SKU-001"]
        history = histories["SKU-001"]
        assert sku_summary(record) == expected_majority
        assert record.last_event.event_id == "evt-1042"
        assert record.last_event.type == "stock_received"
        assert record.last_event.quantity_delta == 20
        assert record.last_event.reference == "delivery-8841"
        assert [event.event_id for event in history] == ["evt-1041", "evt-1042"]
        assert "evt-1043" not in {event.event_id for event in history}

    records, histories = warehouse_states["warehouse-c"]
    record = records["SKU-001"]
    history = histories["SKU-001"]
    assert sku_summary(record) == expected_singleton
    assert record.last_event.event_id == "evt-1043"
    assert [event.event_id for event in history] == [
        "evt-1041",
        "evt-1042",
        "evt-1043",
    ]
    assert history[-1].quantity_delta == -15
    assert history[-1].type == "stock_adjustment"
    assert history[-1].reference == "cycle-count-5501"

    for warehouse_records, warehouse_histories in warehouse_states.values():
        sku_record = warehouse_records["SKU-001"]
        sku_history = warehouse_histories["SKU-001"]
        assert sku_record.inventory.available == (
            sku_record.inventory.on_hand - sku_record.inventory.reserved
        )
        assert sku_record.state.version == sku_record.sync.last_synced_version
        assert sku_record.sync.event_cursor == int(
            sku_history[-1].event_id.removeprefix("evt-")
        )
        assert sku_record.last_event == sku_history[-1]
        assert sku_record.data_quality.status == "valid"
        assert sku_record.data_quality.warnings == []
        assert all(
            event.occurred_at <= event.processed_at for event in sku_history
        )
        assert all(
            earlier.processed_at <= later.processed_at
            for earlier, later in zip(sku_history, sku_history[1:])
        )
        assert sku_record.last_event.processed_at <= sku_record.state.updated_at
        assert (
            sku_record.state.updated_at
            <= sku_record.data_quality.last_validated_at
            <= sku_record.sync.last_successful_sync_at
        )


def test_newer_singleton_expectation_matches_observable_facts() -> None:
    expectation = json.loads(
        (EXPECTATIONS_ROOT / "newer-singleton.json").read_text(encoding="utf-8")
    )
    warehouse_states = load_scenario("newer-singleton")
    records_by_warehouse = {
        warehouse_id: records
        for warehouse_id, (records, _) in warehouse_states.items()
    }

    assert expectation["scenario"] == "newer-singleton"
    assert expectation["product_count"] == len(
        records_by_warehouse["warehouse-a"]
    )
    assert expectation["consistent_sku_count"] == (
        expectation["product_count"] - len(find_conflicting_skus(warehouse_states))
    )
    assert expectation["conflicting_skus"] == find_conflicting_skus(
        warehouse_states
    )
    assert expectation["sku_001"]["agreed_warehouses"] == [
        "warehouse-a",
        "warehouse-b",
    ]
    assert logical_state(records_by_warehouse["warehouse-a"]["SKU-001"]) == (
        logical_state(records_by_warehouse["warehouse-b"]["SKU-001"])
    )
    assert expectation["sku_001"]["newer_singleton"] == "warehouse-c"
    assert expectation["sku_001"]["majority_state"] == sku_summary(
        records_by_warehouse["warehouse-a"]["SKU-001"]
    )
    assert expectation["sku_001"]["newer_singleton_state"] == sku_summary(
        records_by_warehouse["warehouse-c"]["SKU-001"]
    )
    assert expectation["sku_001"]["intended_v2_disposition"] == "INVESTIGATE"
    assert expectation["sku_001"]["safe_canonical_state_established"] is False
    assert expectation["sku_001"]["warehouse_writes_expected"] == 0
    assert (
        expectation["sku_001"]["must_not_reconcile_newer_singleton_backward"]
        is True
    )


def event_derived_on_hand(event_history: list[object]) -> int:
    return sum(event.quantity_delta for event in event_history)


def test_same_version_divergence_loads_and_isolates_sku_001() -> None:
    warehouse_states = load_scenario("same-version-divergence")

    for records, event_history in warehouse_states.values():
        assert set(records) == expected_catalogue_skus()
        assert set(event_history) == expected_catalogue_skus()
        assert len(records) == 10
    assert find_conflicting_skus(warehouse_states) == ["SKU-001"]


def test_same_version_divergence_has_shared_progress_but_different_inventory() -> None:
    warehouse_states = load_scenario("same-version-divergence")
    expected_supported = {
        "version": 42,
        "on_hand": 120,
        "reserved": 8,
        "available": 112,
        "event_cursor": 1042,
    }
    expected_divergent = {
        "version": 42,
        "on_hand": 115,
        "reserved": 8,
        "available": 107,
        "event_cursor": 1042,
    }

    for warehouse_id in WAREHOUSE_IDS:
        record = warehouse_states[warehouse_id][0]["SKU-001"]
        assert record.state.version == 42
        assert record.sync.last_synced_version == 42
        assert record.sync.event_cursor == 1042
        assert record.last_event.event_id == "evt-1042"
        assert record.sync.status == "up_to_date"
        assert record.data_quality.status == "valid"

    assert sku_summary(warehouse_states["warehouse-a"][0]["SKU-001"]) == (
        expected_supported
    )
    assert sku_summary(warehouse_states["warehouse-b"][0]["SKU-001"]) == (
        expected_supported
    )
    assert sku_summary(warehouse_states["warehouse-c"][0]["SKU-001"]) == (
        expected_divergent
    )
    c_inventory = warehouse_states["warehouse-c"][0]["SKU-001"].inventory
    assert c_inventory.available == c_inventory.on_hand - c_inventory.reserved


def test_same_version_divergence_histories_support_only_the_120_state() -> None:
    warehouse_states = load_scenario("same-version-divergence")
    histories = {
        warehouse_id: event_history["SKU-001"]
        for warehouse_id, (_, event_history) in warehouse_states.items()
    }
    serialised_histories = {
        warehouse_id: [
            event.model_dump(mode="json") for event in event_history
        ]
        for warehouse_id, event_history in histories.items()
    }

    assert serialised_histories["warehouse-a"] == serialised_histories["warehouse-b"]
    assert serialised_histories["warehouse-a"] == serialised_histories["warehouse-c"]
    for event_history in histories.values():
        assert [event.event_id for event in event_history] == [
            "evt-1041",
            "evt-1042",
        ]
        event_details = [
            (event.type, event.quantity_delta, event.reference)
            for event in event_history
        ]
        assert event_details == [
            ("stock_received", 100, "opening-stock-1041"),
            ("stock_received", 20, "delivery-8841"),
        ]
        assert event_derived_on_hand(event_history) == 120

    assert warehouse_states["warehouse-a"][0]["SKU-001"].inventory.on_hand == 120
    assert warehouse_states["warehouse-b"][0]["SKU-001"].inventory.on_hand == 120
    assert warehouse_states["warehouse-c"][0]["SKU-001"].inventory.on_hand != 120


def test_same_version_divergence_expectation_matches_observable_facts() -> None:
    expectation = json.loads(
        (EXPECTATIONS_ROOT / "same-version-divergence.json").read_text(
            encoding="utf-8"
        )
    )
    warehouse_states = load_scenario("same-version-divergence")
    records_by_warehouse = {
        warehouse_id: records
        for warehouse_id, (records, _) in warehouse_states.items()
    }
    derived_on_hand = event_derived_on_hand(
        warehouse_states["warehouse-c"][1]["SKU-001"]
    )

    assert expectation["scenario"] == "same-version-divergence"
    assert expectation["product_count"] == len(
        records_by_warehouse["warehouse-a"]
    )
    assert expectation["consistent_sku_count"] == 9
    assert expectation["conflicting_skus"] == find_conflicting_skus(
        warehouse_states
    )
    assert expectation["sku_001"]["shared_logical_progress"] == {
        "version": records_by_warehouse["warehouse-c"]["SKU-001"].state.version,
        "event_cursor": (
            records_by_warehouse["warehouse-c"]["SKU-001"].sync.event_cursor
        ),
        "last_event": (
            records_by_warehouse["warehouse-c"]["SKU-001"].last_event.event_id
        ),
    }
    assert expectation["sku_001"]["agreed_warehouses"] == [
        "warehouse-a",
        "warehouse-b",
    ]
    assert expectation["sku_001"]["divergent_warehouse"] == "warehouse-c"
    assert expectation["sku_001"]["supported_state"] == sku_summary(
        records_by_warehouse["warehouse-a"]["SKU-001"]
    )
    assert expectation["sku_001"]["divergent_state"] == sku_summary(
        records_by_warehouse["warehouse-c"]["SKU-001"]
    )
    assert expectation["sku_001"]["event_derived_on_hand"] == derived_on_hand
    assert (
        records_by_warehouse["warehouse-c"]["SKU-001"].inventory.on_hand
        != derived_on_hand
    )
    assert (
        expectation["sku_001"][
            "initial_resolution_possible_from_version_progress"
        ]
        is False
    )
    assert expectation["sku_001"]["event_history_investigation_required"] is True
    assert (
        expectation["sku_001"]["divergent_state_supported_by_event_history"]
        is False
    )
    assert expectation["sku_001"]["intended_v2_disposition"] == "RECONCILE"
    assert expectation["sku_001"]["target_warehouse"] == "warehouse-c"
    assert expectation["sku_001"]["decision_requires_event_history_evidence"] is True
