import json

from agent.evidence import derive_on_hand_from_complete_history
from tests.test_scenario_loading import (
    EXPECTATIONS_ROOT,
    WAREHOUSE_IDS,
    expected_catalogue_skus,
    find_conflicting_skus,
    load_scenario,
    sku_summary,
)


def test_mixed_conflicts_loads_three_isolated_coherent_conflicts() -> None:
    states = load_scenario("mixed-conflicts")

    for records, histories in states.values():
        assert set(records) == expected_catalogue_skus()
        assert set(histories) == expected_catalogue_skus()
        assert len(records) == 10
    assert find_conflicting_skus(states) == ["SKU-001", "SKU-004", "SKU-007"]

    assert sku_summary(states["warehouse-a"][0]["SKU-001"])["version"] == 42
    assert sku_summary(states["warehouse-b"][0]["SKU-001"])["version"] == 41
    assert sku_summary(states["warehouse-c"][0]["SKU-001"])["version"] == 42

    for warehouse_id in ("warehouse-a", "warehouse-b"):
        record = states[warehouse_id][0]["SKU-004"]
        history = states[warehouse_id][1]["SKU-004"]
        assert sku_summary(record) == {
            "version": 50,
            "on_hand": 60,
            "reserved": 5,
            "available": 55,
            "event_cursor": 2050,
        }
        assert [event.event_id for event in history] == ["evt-2049", "evt-2050"]
        assert derive_on_hand_from_complete_history(history) == 60

    c_record = states["warehouse-c"][0]["SKU-004"]
    c_history = states["warehouse-c"][1]["SKU-004"]
    assert sku_summary(c_record) == {
        "version": 51,
        "on_hand": 52,
        "reserved": 5,
        "available": 47,
        "event_cursor": 2051,
    }
    assert [event.event_id for event in c_history] == [
        "evt-2049",
        "evt-2050",
        "evt-2051",
    ]
    assert derive_on_hand_from_complete_history(c_history) == 52

    serialised_histories = {
        warehouse_id: [
            event.model_dump(mode="json")
            for event in states[warehouse_id][1]["SKU-007"]
        ]
        for warehouse_id in WAREHOUSE_IDS
    }
    assert len({json.dumps(value, sort_keys=True) for value in serialised_histories.values()}) == 1
    for warehouse_id in WAREHOUSE_IDS:
        record = states[warehouse_id][0]["SKU-007"]
        history = states[warehouse_id][1]["SKU-007"]
        assert record.state.version == 60
        assert record.sync.event_cursor == 3060
        assert record.last_event.event_id == "evt-3060"
        assert derive_on_hand_from_complete_history(history) == 40
    assert states["warehouse-a"][0]["SKU-007"].inventory.on_hand == 40
    assert states["warehouse-b"][0]["SKU-007"].inventory.on_hand == 40
    assert states["warehouse-c"][0]["SKU-007"].inventory.on_hand == 35


def test_mixed_conflicts_expectation_documents_observable_paths() -> None:
    expectation = json.loads(
        (EXPECTATIONS_ROOT / "mixed-conflicts.json").read_text(encoding="utf-8")
    )
    states = load_scenario("mixed-conflicts")

    assert expectation["scenario"] == "mixed-conflicts"
    assert expectation["product_count"] == 10
    assert expectation["consistent_sku_count"] == 7
    assert expectation["conflicting_skus"] == find_conflicting_skus(states)
    assert expectation["sku_001"]["initial_decision"] == "RECONCILE"
    assert expectation["sku_004"]["event_investigation_warehouses"] == [
        "warehouse-c"
    ]
    assert expectation["sku_007"]["repair_version"] == 61


def test_incomplete_history_is_valid_but_lacks_the_known_anchor() -> None:
    states = load_scenario("incomplete-event-history")

    for records, histories in states.values():
        assert set(records) == expected_catalogue_skus()
        assert set(histories) == expected_catalogue_skus()
        assert len(records) == 10
    assert find_conflicting_skus(states) == ["SKU-001"]

    for warehouse_id in ("warehouse-a", "warehouse-b"):
        history = states[warehouse_id][1]["SKU-001"]
        assert [event.event_id for event in history] == ["evt-1041", "evt-1042"]
        assert derive_on_hand_from_complete_history(history) == 120

    c_record = states["warehouse-c"][0]["SKU-001"]
    c_history = states["warehouse-c"][1]["SKU-001"]
    assert c_record.state.version == 43
    assert c_record.sync.event_cursor == 1043
    assert c_record.last_event.event_id == "evt-1043"
    assert [event.event_id for event in c_history] == ["evt-1043"]
    assert "evt-1042" not in {event.event_id for event in c_history}
    assert derive_on_hand_from_complete_history(c_history) is None


def test_incomplete_history_expectation_requires_safe_escalation() -> None:
    expectation = json.loads(
        (EXPECTATIONS_ROOT / "incomplete-event-history.json").read_text(
            encoding="utf-8"
        )
    )
    states = load_scenario("incomplete-event-history")

    assert expectation["scenario"] == "incomplete-event-history"
    assert expectation["product_count"] == 10
    assert expectation["consistent_sku_count"] == 9
    assert expectation["conflicting_skus"] == find_conflicting_skus(states)
    assert expectation["intended_final_outcome"] == "ESCALATED"
    assert expectation["warehouse_writes_expected"] == 0
    assert expectation["sku_001"]["intended_final_outcome"] == "ESCALATED"
    assert expectation["sku_001"]["warehouse_writes_expected"] == 0
