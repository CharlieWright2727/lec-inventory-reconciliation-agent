from agent.models import DecisionOutcome, EvidenceType, ResolutionOutcome
from tests.test_agent_v3 import run_v3
from tests.v3_support import V3Harness, WAREHOUSE_IDS


def test_mixed_conflicts_choose_independent_paths_and_converge() -> None:
    harness = V3Harness("mixed-conflicts")
    state = run_v3(harness)

    assert list(state.conflicts) == ["SKU-001", "SKU-004", "SKU-007"]
    assert {
        sku: history[0].outcome
        for sku, history in state.decision_history.items()
    } == {
        "SKU-001": DecisionOutcome.RECONCILE,
        "SKU-004": DecisionOutcome.INVESTIGATE,
        "SKU-007": DecisionOutcome.INVESTIGATE,
    }
    assert state.decisions["SKU-004"].outcome == DecisionOutcome.RECONCILE
    assert state.decisions["SKU-007"].outcome == DecisionOutcome.RECONCILE
    assert state.resolutions == {
        sku: ResolutionOutcome.RESOLVED
        for sku in ("SKU-001", "SKU-004", "SKU-007")
    }

    event_calls = [(call[0], call[2]) for call in harness.calls(suffix="/events")]
    assert event_calls == [
        ("warehouse-c", "/inventory/SKU-004/events"),
        ("warehouse-a", "/inventory/SKU-007/events"),
        ("warehouse-b", "/inventory/SKU-007/events"),
        ("warehouse-c", "/inventory/SKU-007/events"),
    ]
    put_calls = [(call[0], call[2]) for call in harness.calls(method="PUT")]
    assert put_calls == [
        ("warehouse-b", "/inventory/SKU-001"),
        ("warehouse-a", "/inventory/SKU-004"),
        ("warehouse-b", "/inventory/SKU-004"),
        ("warehouse-a", "/inventory/SKU-007"),
        ("warehouse-b", "/inventory/SKU-007"),
        ("warehouse-c", "/inventory/SKU-007"),
    ]
    assert all(
        state.verification_results[sku].verified
        for sku in ("SKU-001", "SKU-004", "SKU-007")
    )
    assert state.reconciliation_plans["SKU-007"].repair_revision is True
    assert state.reconciliation_plans["SKU-007"].target_version == 61
    assert state.metrics.catalogue_queries == 3
    assert state.metrics.event_investigation_queries == 4
    assert state.metrics.reconciliation_writes == 6
    assert state.metrics.verification_reads == 9
    assert state.metrics.total_api_calls == 22

    harness.requests.clear()
    second = run_v3(harness)
    assert second.conflicts == {}
    assert len(second.consistent_skus) == 10
    assert second.metrics.total_api_calls == 3
    assert second.metrics.event_investigation_queries == 0
    assert second.metrics.reconciliation_writes == 0
    assert second.metrics.verification_reads == 0


def test_incomplete_history_investigates_then_escalates_without_writes() -> None:
    harness = V3Harness("incomplete-event-history")
    state = run_v3(harness)

    assert list(state.conflicts) == ["SKU-001"]
    assert state.decision_history["SKU-001"][0].outcome == (
        DecisionOutcome.INVESTIGATE
    )
    assert state.decisions["SKU-001"].outcome == DecisionOutcome.ESCALATE
    assert state.resolutions["SKU-001"] == ResolutionOutcome.ESCALATED
    assert [
        (call[0], call[2]) for call in harness.calls(suffix="/events")
    ] == [("warehouse-c", "/inventory/SKU-001/events")]
    assert state.evidence["SKU-001"].findings_of_type(
        EvidenceType.INSUFFICIENT_EVIDENCE
    )
    assert harness.calls(method="PUT") == []
    assert state.reconciliation_plans == {}
    assert state.metrics.catalogue_queries == 3
    assert state.metrics.event_investigation_queries == 1
    assert state.metrics.reconciliation_writes == 0
    assert state.metrics.verification_reads == 0
    assert state.metrics.total_api_calls == 4
