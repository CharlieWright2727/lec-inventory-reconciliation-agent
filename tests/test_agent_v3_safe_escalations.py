from agent.models import (
    ConflictType,
    DecisionOutcome,
    EvidenceType,
    ResolutionOutcome,
    RunStatus,
)
from tests.test_agent_v3 import run_v3
from tests.v3_support import V3Harness


def test_competing_valid_extensions_are_investigated_then_escalated() -> None:
    harness = V3Harness("competing-newer-states")
    state = run_v3(harness)

    assert state.status == RunStatus.COMPLETED
    assert list(state.conflicts) == ["SKU-001"]
    assert state.decision_history["SKU-001"][0].outcome == (
        DecisionOutcome.INVESTIGATE
    )
    assert state.decisions["SKU-001"].outcome == DecisionOutcome.ESCALATE
    assert state.resolutions["SKU-001"] == ResolutionOutcome.ESCALATED
    assert [
        (call[0], call[2]) for call in harness.calls(suffix="/events")
    ] == [
        ("warehouse-b", "/inventory/SKU-001/events"),
        ("warehouse-c", "/inventory/SKU-001/events"),
    ]

    extensions = state.evidence["SKU-001"].findings_of_type(
        EvidenceType.EVENT_HISTORY_EXTENDS_KNOWN_STATE
    )
    assert {
        (finding.warehouse_ids[0], finding.anchor_event_id, finding.derived_on_hand)
        for finding in extensions
    } == {
        ("warehouse-b", "evt-1042", 110),
        ("warehouse-c", "evt-1042", 130),
    }
    contradiction = state.evidence["SKU-001"].findings_of_type(
        EvidenceType.EVENT_HISTORIES_CONTRADICT
    )
    assert len(contradiction) == 1
    assert contradiction[0].warehouse_ids == ["warehouse-b", "warehouse-c"]
    assert harness.calls(method="PUT") == []
    assert state.metrics.catalogue_queries == 3
    assert state.metrics.event_investigation_queries == 2
    assert state.metrics.reconciliation_writes == 0
    assert state.metrics.verification_reads == 0
    assert state.metrics.total_api_calls == 5


def test_missing_sku_escalates_without_investigation_or_mutation() -> None:
    harness = V3Harness("missing-sku")
    state = run_v3(harness)

    assert state.status == RunStatus.COMPLETED
    assert list(state.conflicts) == ["SKU-005"]
    assert state.conflicts["SKU-005"].conflict_types == [ConflictType.MISSING_SKU]
    assert state.decisions["SKU-005"].outcome == DecisionOutcome.ESCALATE
    assert state.resolutions["SKU-005"] == ResolutionOutcome.ESCALATED
    assert state.evidence["SKU-005"].findings_of_type(
        EvidenceType.MISSING_WAREHOUSE_STATE
    )
    assert harness.calls(suffix="/events") == []
    assert harness.calls(method="PUT") == []
    assert state.metrics.catalogue_queries == 3
    assert state.metrics.event_investigation_queries == 0
    assert state.metrics.reconciliation_writes == 0
    assert state.metrics.verification_reads == 0
    assert state.metrics.total_api_calls == 3

    harness.requests.clear()
    second = run_v3(harness)
    assert second.resolutions["SKU-005"] == ResolutionOutcome.ESCALATED
    assert second.metrics.total_api_calls == 3
    assert second.metrics.put_calls == 0


def test_one_supported_branch_cannot_win_if_another_branch_read_fails() -> None:
    harness = V3Harness("competing-newer-states")
    harness.forced_event_status["warehouse-c"] = 503
    state = run_v3(harness)

    assert state.decisions["SKU-001"].outcome == DecisionOutcome.ESCALATE
    assert state.resolutions["SKU-001"] == ResolutionOutcome.ESCALATED
    assert state.metrics.event_investigation_queries == 2
    assert state.metrics.failed_calls == 1
    assert state.metrics.put_calls == 0
