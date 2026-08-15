import asyncio

import httpx
import pytest

from agent.models import DecisionOutcome, ExecutionStatus, ResolutionOutcome
from agent.runner import run_agent_v3
from tests.v3_support import V3Harness, WAREHOUSE_IDS, endpoints
from warehouse.models import InventoryUpdateRequest, UpdateSource


def run_v3(harness: V3Harness):
    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(harness.handler)
        ) as client:
            return await run_agent_v3(endpoints(), http_client=client)

    return asyncio.run(exercise())


@pytest.mark.parametrize(
    (
        "scenario",
        "initial_decision",
        "event_warehouses",
        "put_targets",
        "target_version",
        "on_hand",
        "total_calls",
    ),
    [
        (
            "one-stale-warehouse",
            DecisionOutcome.RECONCILE,
            [],
            ["warehouse-b"],
            42,
            120,
            7,
        ),
        (
            "newer-singleton",
            DecisionOutcome.INVESTIGATE,
            ["warehouse-c"],
            ["warehouse-a", "warehouse-b"],
            43,
            105,
            9,
        ),
        (
            "same-version-divergence",
            DecisionOutcome.INVESTIGATE,
            list(WAREHOUSE_IDS),
            list(WAREHOUSE_IDS),
            43,
            120,
            12,
        ),
    ],
)
def test_v3_scenarios_execute_and_verify_real_store_convergence(
    scenario,
    initial_decision,
    event_warehouses,
    put_targets,
    target_version,
    on_hand,
    total_calls,
) -> None:
    harness = V3Harness(scenario)
    state = run_v3(harness)
    verification = state.verification_results["SKU-001"]

    assert state.decision_history["SKU-001"][0].outcome == initial_decision
    assert state.decisions["SKU-001"].outcome == DecisionOutcome.RECONCILE
    assert state.resolutions["SKU-001"] == ResolutionOutcome.RESOLVED
    assert verification.verified is True
    assert verification.remaining_conflict_types == []
    assert [call[0] for call in harness.calls(suffix="/events")] == event_warehouses
    assert [call[0] for call in harness.calls(method="PUT")] == put_targets
    assert state.metrics.total_api_calls == total_calls
    assert state.metrics.put_calls == len(put_targets)
    assert state.metrics.verification_reads == 3
    assert state.metrics.total_request_bytes > 0
    assert state.metrics.total_bytes_transferred > 0
    assert {
        (record.state.version, record.inventory.on_hand, record.sync.event_cursor)
        for record in verification.warehouse_records.values()
    } == {(target_version, on_hand, 1042 if target_version == 42 else 1043)}


def test_optimistic_concurrency_conflict_stops_and_escalates_without_retry() -> None:
    harness = V3Harness("one-stale-warehouse")

    def mutate_before_put(warehouse_id, put_count, store):
        if warehouse_id != "warehouse-b" or put_count != 1:
            return
        store.update(
            "SKU-001",
            InventoryUpdateRequest(
                expected_current_version=41,
                target_version=42,
                inventory={"on_hand": 120, "reserved": 8, "available": 112},
                source=UpdateSource(
                    system_id="external-system",
                    snapshot_id="external-snapshot",
                    event_id="external-event",
                ),
                reason="independent warehouse update",
            ),
        )

    harness.before_put = mutate_before_put
    state = run_v3(harness)

    assert state.execution_results["SKU-001"][0].status == ExecutionStatus.REJECTED
    assert state.verification_results["SKU-001"].verified is True
    assert state.resolutions["SKU-001"] == ResolutionOutcome.ESCALATED
    assert state.metrics.reconciliation_writes == 1
    assert state.metrics.failed_calls == 1
    assert state.metrics.verification_reads == 3


def test_non_writable_target_is_rejected_before_any_put() -> None:
    harness = V3Harness(
        "one-stale-warehouse", non_writable={"warehouse-b"}
    )
    state = run_v3(harness)

    assert state.safety_results["SKU-001"].safe is False
    assert state.resolutions["SKU-001"] == ResolutionOutcome.ESCALATED
    assert state.execution_results == {}
    assert state.metrics.put_calls == 0


def test_successful_put_with_mismatching_verification_escalates() -> None:
    harness = V3Harness("one-stale-warehouse")
    stale_b = harness.stores["warehouse-b"].inventory("SKU-001")
    harness.verification_overrides["warehouse-b"] = stale_b
    state = run_v3(harness)

    assert state.execution_results["SKU-001"][0].status == ExecutionStatus.SUCCESS
    assert state.verification_results["SKU-001"].verified is False
    assert state.resolutions["SKU-001"] == ResolutionOutcome.ESCALATED


def test_partial_multi_target_failure_is_audited_verified_and_escalated() -> None:
    harness = V3Harness("newer-singleton")
    harness.forced_put_status["warehouse-b"] = 500
    state = run_v3(harness)
    results = state.execution_results["SKU-001"]
    verification = state.verification_results["SKU-001"]

    assert [(item.warehouse_id, item.status) for item in results] == [
        ("warehouse-a", ExecutionStatus.SUCCESS),
        ("warehouse-b", ExecutionStatus.REJECTED),
    ]
    assert harness.stores["warehouse-a"].inventory("SKU-001").state.version == 43
    assert harness.stores["warehouse-b"].inventory("SKU-001").state.version == 42
    assert verification.verified is False
    assert state.resolutions["SKU-001"] == ResolutionOutcome.ESCALATED
    assert state.metrics.put_calls == 2
    assert state.metrics.verification_reads == 3


def test_second_run_is_idempotent_and_performs_only_catalogue_reads() -> None:
    harness = V3Harness("same-version-divergence")
    first = run_v3(harness)
    harness.requests.clear()
    second = run_v3(harness)

    assert first.resolutions["SKU-001"] == ResolutionOutcome.RESOLVED
    assert second.conflicts == {}
    assert second.metrics.catalogue_queries == 3
    assert second.metrics.event_investigation_queries == 0
    assert second.metrics.reconciliation_writes == 0
    assert second.metrics.verification_reads == 0
    assert second.metrics.total_api_calls == 3
