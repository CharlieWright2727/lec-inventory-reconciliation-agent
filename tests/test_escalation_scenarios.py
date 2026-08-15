import json

from agent.evidence import derive_on_hand_from_complete_history
from tests.test_scenario_loading import (
    EXPECTATIONS_ROOT,
    WAREHOUSE_IDS,
    expected_catalogue_skus,
    load_scenario,
    logical_state,
    sku_summary,
)


def conflicting_skus(states) -> list[str]:
    all_skus = sorted(
        {sku for records, _ in states.values() for sku in records}
    )
    return [
        sku
        for sku in all_skus
        if any(sku not in records for records, _ in states.values())
        or len(
            {
                json.dumps(logical_state(records[sku]), sort_keys=True)
                for records, _ in states.values()
            }
        )
        > 1
    ]


def test_competing_newer_states_are_two_valid_branches_from_one_base() -> None:
    states = load_scenario("competing-newer-states")

    assert all(len(records) == 10 for records, _ in states.values())
    assert all(set(records) == expected_catalogue_skus() for records, _ in states.values())
    assert conflicting_skus(states) == ["SKU-001"]
    assert sku_summary(states["warehouse-a"][0]["SKU-001"]) == {
        "version": 42,
        "on_hand": 120,
        "reserved": 8,
        "available": 112,
        "event_cursor": 1042,
    }
    assert sku_summary(states["warehouse-b"][0]["SKU-001"]) == {
        "version": 43,
        "on_hand": 110,
        "reserved": 8,
        "available": 102,
        "event_cursor": 1043,
    }
    assert sku_summary(states["warehouse-c"][0]["SKU-001"]) == {
        "version": 43,
        "on_hand": 130,
        "reserved": 8,
        "available": 122,
        "event_cursor": 1043,
    }

    histories = {
        warehouse_id: states[warehouse_id][1]["SKU-001"]
        for warehouse_id in WAREHOUSE_IDS
    }
    assert [event.event_id for event in histories["warehouse-a"]] == [
        "evt-1041",
        "evt-1042",
    ]
    assert [event.event_id for event in histories["warehouse-b"]] == [
        "evt-1041",
        "evt-1042",
        "evt-1043-b",
    ]
    assert [event.event_id for event in histories["warehouse-c"]] == [
        "evt-1041",
        "evt-1042",
        "evt-1043-c",
    ]
    assert [
        event.model_dump(mode="json") for event in histories["warehouse-b"][:2]
    ] == [
        event.model_dump(mode="json") for event in histories["warehouse-c"][:2]
    ]
    assert histories["warehouse-b"][-1].quantity_delta == -10
    assert histories["warehouse-c"][-1].quantity_delta == 10
    assert derive_on_hand_from_complete_history(histories["warehouse-b"]) == 110
    assert derive_on_hand_from_complete_history(histories["warehouse-c"]) == 130


def test_competing_newer_expectation_documents_safe_escalation() -> None:
    expectation = json.loads(
        (EXPECTATIONS_ROOT / "competing-newer-states.json").read_text(
            encoding="utf-8"
        )
    )
    states = load_scenario("competing-newer-states")

    assert expectation["scenario"] == "competing-newer-states"
    assert expectation["product_count"] == 10
    assert expectation["consistent_sku_count"] == 9
    assert expectation["conflicting_skus"] == conflicting_skus(states)
    assert expectation["newer_branches"] == ["warehouse-b", "warehouse-c"]
    assert expectation["intended_final_outcome"] == "ESCALATED"
    assert expectation["warehouse_writes_expected"] == 0


def test_missing_sku_overlay_removes_the_record_only_from_warehouse_c() -> None:
    states = load_scenario("missing-sku")
    expected_skus = expected_catalogue_skus()

    assert set(states["warehouse-a"][0]) == expected_skus
    assert set(states["warehouse-b"][0]) == expected_skus
    assert set(states["warehouse-c"][0]) == expected_skus - {"SKU-005"}
    assert "SKU-005" not in states["warehouse-c"][1]
    assert set().union(*(set(records) for records, _ in states.values())) == expected_skus
    assert conflicting_skus(states) == ["SKU-005"]
    assert logical_state(states["warehouse-a"][0]["SKU-005"]) == logical_state(
        states["warehouse-b"][0]["SKU-005"]
    )
    for sku in expected_skus - {"SKU-005"}:
        assert all(sku in records for records, _ in states.values())


def test_missing_sku_expectation_documents_coverage_gap() -> None:
    expectation = json.loads(
        (EXPECTATIONS_ROOT / "missing-sku.json").read_text(encoding="utf-8")
    )
    states = load_scenario("missing-sku")

    assert expectation["scenario"] == "missing-sku"
    assert expectation["product_count"] == 10
    assert expectation["consistent_sku_count"] == 9
    assert expectation["conflicting_skus"] == conflicting_skus(states)
    assert expectation["present_in"] == ["warehouse-a", "warehouse-b"]
    assert expectation["missing_from"] == ["warehouse-c"]
    assert expectation["intended_final_outcome"] == "ESCALATED"
    assert expectation["warehouse_writes_expected"] == 0
