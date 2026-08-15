import asyncio

import httpx

from agent.client import WarehouseClient
from agent.executor import execute_reconciliation_plan
from agent.metrics import MetricRecorder
from agent.planner import plan_reconciliation
from agent.verifier import verify_reconciliation
from tests.test_reconciliation_planner import v2_state
from tests.v3_support import V3Harness, endpoints


def plan_for(scenario):
    state = v2_state(scenario)
    return plan_reconciliation(
        state.decisions["SKU-001"], state.evidence["SKU-001"], state.observations
    )


def test_verifier_uses_fresh_reads_and_detector_to_prove_convergence() -> None:
    plan = plan_for("one-stale-warehouse")
    harness = V3Harness("one-stale-warehouse")

    async def exercise():
        recorder = MetricRecorder()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(harness.handler)
        ) as http_client:
            client = WarehouseClient(http_client, recorder)
            await execute_reconciliation_plan(
                plan, endpoints(), client, run_id="run-test"
            )
            result = await verify_reconciliation(
                plan, endpoints(), client, run_id="run-test"
            )
        return result, recorder.metrics

    result, metrics = asyncio.run(exercise())
    assert result.verified is True
    assert result.remaining_conflict_types == []
    assert set(result.warehouse_records) == set(endpoints())
    assert metrics.verification_reads == 3


def test_verifier_rejects_inventory_version_or_event_progress_conflict() -> None:
    plan = plan_for("one-stale-warehouse")
    harness = V3Harness("one-stale-warehouse")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(harness.handler)
        ) as http_client:
            return await verify_reconciliation(
                plan,
                endpoints(),
                WarehouseClient(http_client, MetricRecorder()),
                run_id="run-test",
            )

    result = asyncio.run(exercise())
    assert result.verified is False
    assert result.remaining_conflict_types


def test_verifier_rejects_a_missing_response() -> None:
    plan = plan_for("one-stale-warehouse")
    harness = V3Harness("one-stale-warehouse")

    def handler(request: httpx.Request):
        if request.url.host == "warehouse-c.test" and request.method == "GET":
            raise httpx.ConnectError("missing", request=request)
        return harness.handler(request)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            return await verify_reconciliation(
                plan,
                endpoints(),
                WarehouseClient(http_client, MetricRecorder()),
                run_id="run-test",
            )

    result = asyncio.run(exercise())
    assert result.verified is False
    assert result.missing_warehouses == ["warehouse-c"]
