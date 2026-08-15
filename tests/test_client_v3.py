import asyncio

import httpx
import pytest

from agent.client import WarehouseClient, WarehouseClientError
from agent.metrics import MetricRecorder
from tests.v3_support import V3Harness, endpoints
from warehouse.models import InventoryUpdateRequest, UpdateSource


def update_request() -> InventoryUpdateRequest:
    return InventoryUpdateRequest(
        expected_current_version=41,
        target_version=42,
        inventory={"on_hand": 120, "reserved": 8, "available": 112},
        source=UpdateSource(
            system_id="warehouse-a",
            snapshot_id="source-snapshot",
            event_id="source-event",
        ),
        reason="test reconciliation",
    )


def test_targeted_read_and_write_validate_responses_and_measure_bytes() -> None:
    harness = V3Harness("one-stale-warehouse")

    async def exercise():
        recorder = MetricRecorder()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(harness.handler)
        ) as http_client:
            client = WarehouseClient(http_client, recorder)
            before = await client.get_inventory(
                endpoints()["warehouse-b"], "SKU-001", run_id="run-test"
            )
            result = await client.update_inventory(
                endpoints()["warehouse-b"],
                "SKU-001",
                update_request(),
                run_id="run-test",
            )
        return before, result, recorder.metrics

    before, result, metrics = asyncio.run(exercise())

    assert before.state.version == 41
    assert result.new_version == 42
    assert metrics.verification_reads == 1
    assert metrics.reconciliation_writes == 1
    assert metrics.api_calls[1].request_bytes == harness.calls(method="PUT")[0][3]
    assert metrics.api_calls[1].request_bytes > 0
    assert metrics.total_response_bytes > 0


def test_write_409_is_recorded_as_a_failed_reconciliation_call() -> None:
    harness = V3Harness("one-stale-warehouse")
    harness.forced_put_status["warehouse-b"] = 409

    async def exercise():
        recorder = MetricRecorder()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(harness.handler)
        ) as http_client:
            client = WarehouseClient(http_client, recorder)
            with pytest.raises(WarehouseClientError):
                await client.update_inventory(
                    endpoints()["warehouse-b"],
                    "SKU-001",
                    update_request(),
                    run_id="run-test",
                )
        return recorder.metrics

    metrics = asyncio.run(exercise())
    assert metrics.failed_calls == 1
    assert metrics.api_calls[0].status_code == 409
    assert metrics.api_calls[0].error_type == "http_error"
    assert metrics.api_calls[0].request_bytes > 0


@pytest.mark.parametrize("failure", ["timeout", "validation"])
def test_targeted_read_records_transport_and_validation_failures(failure) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"not": "an inventory response"})

    async def exercise():
        recorder = MetricRecorder()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = WarehouseClient(http_client, recorder)
            with pytest.raises(WarehouseClientError):
                await client.get_inventory(
                    endpoints()["warehouse-a"], "SKU-001", run_id="run-test"
                )
        return recorder.metrics

    metrics = asyncio.run(exercise())
    assert metrics.verification_reads == 1
    assert metrics.failed_calls == 1
    assert metrics.api_calls[0].error_type == (
        "validation_error" if failure == "validation" else "timeout"
    )


@pytest.mark.parametrize("failure", ["timeout", "validation"])
def test_write_records_transport_and_response_validation_failures(failure) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(
            200,
            json={
                "status": "updated",
                "system_id": "wrong-warehouse",
                "sku": "SKU-001",
                "previous_version": 41,
                "new_version": 42,
            },
        )

    async def exercise():
        recorder = MetricRecorder()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = WarehouseClient(http_client, recorder)
            with pytest.raises(WarehouseClientError):
                await client.update_inventory(
                    endpoints()["warehouse-b"],
                    "SKU-001",
                    update_request(),
                    run_id="run-test",
                )
        return recorder.metrics

    metrics = asyncio.run(exercise())
    assert metrics.reconciliation_writes == 1
    assert metrics.failed_calls == 1
    assert metrics.api_calls[0].request_bytes > 0
    assert metrics.api_calls[0].error_type == (
        "validation_error" if failure == "validation" else "timeout"
    )
