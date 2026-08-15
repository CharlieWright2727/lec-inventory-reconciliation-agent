import asyncio

import httpx

from agent.client import WarehouseClient
from agent.executor import execute_reconciliation_plan
from agent.metrics import MetricRecorder
from agent.models import ExecutionStatus
from agent.planner import plan_reconciliation
from tests.test_reconciliation_planner import v2_state
from tests.v3_support import V3Harness, endpoints


def test_executor_preserves_order_and_stops_after_failure() -> None:
    state = v2_state("same-version-divergence")
    plan = plan_reconciliation(
        state.decisions["SKU-001"],
        state.evidence["SKU-001"],
        state.observations,
    )
    original = plan.model_copy(deep=True)
    harness = V3Harness("same-version-divergence")
    harness.forced_put_status["warehouse-b"] = 409

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(harness.handler)
        ) as http_client:
            client = WarehouseClient(http_client, MetricRecorder())
            return await execute_reconciliation_plan(
                plan, endpoints(), client, run_id="run-test"
            )

    results = asyncio.run(exercise())

    assert [result.warehouse_id for result in results] == [
        "warehouse-a",
        "warehouse-b",
    ]
    assert [result.status for result in results] == [
        ExecutionStatus.SUCCESS,
        ExecutionStatus.REJECTED,
    ]
    assert [call[:3] for call in harness.calls(method="PUT")] == [
        ("warehouse-a", "PUT", "/inventory/SKU-001"),
        ("warehouse-b", "PUT", "/inventory/SKU-001"),
    ]
    assert plan == original


def test_executor_runs_a_valid_plan_to_completion() -> None:
    state = v2_state("newer-singleton")
    plan = plan_reconciliation(
        state.decisions["SKU-001"], state.evidence["SKU-001"], state.observations
    )
    harness = V3Harness("newer-singleton")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(harness.handler)
        ) as http_client:
            return await execute_reconciliation_plan(
                plan,
                endpoints(),
                WarehouseClient(http_client, MetricRecorder()),
                run_id="run-test",
            )

    results = asyncio.run(exercise())
    assert [result.status for result in results] == [
        ExecutionStatus.SUCCESS,
        ExecutionStatus.SUCCESS,
    ]
