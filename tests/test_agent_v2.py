import asyncio
from pathlib import Path

import httpx
import pytest

from agent.client import WarehouseClient, WarehouseClientError
from agent.metrics import MetricRecorder
from agent.models import DecisionOutcome, RunStatus, WarehouseEndpoint
from agent.runner import run_agent_v2
from warehouse.store import WarehouseStore


ROOT = Path(__file__).parents[1]
WAREHOUSE_IDS = ("warehouse-a", "warehouse-b", "warehouse-c")


def endpoints():
    return {
        warehouse_id: WarehouseEndpoint(
            warehouse_id=warehouse_id,
            base_url=f"https://{warehouse_id}.test",
        )
        for warehouse_id in WAREHOUSE_IDS
    }


def stores(scenario):
    return {
        warehouse_id: WarehouseStore(
            warehouse_id,
            ROOT / "warehouse/data/products.json",
            scenario_data_path=(
                ROOT / "scenarios" / scenario / f"{warehouse_id}.json"
            ),
        )
        for warehouse_id in WAREHOUSE_IDS
    }


def execute_scenario(scenario):
    warehouse_stores = stores(scenario)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        warehouse_id = request.url.host.removesuffix(".test")
        requests.append((warehouse_id, request.method, request.url.path))
        store = warehouse_stores[warehouse_id]
        if request.url.path == "/inventory":
            body = store.catalogue()
        else:
            sku = request.url.path.split("/")[2]
            body = store.events(sku, int(request.url.params["limit"]))
        return httpx.Response(200, json=body.model_dump(mode="json"))

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            return await run_agent_v2(endpoints(), http_client=http_client)

    return asyncio.run(exercise()), requests


@pytest.mark.parametrize(
    (
        "scenario",
        "initial",
        "source",
        "targets",
        "total_calls",
        "event_warehouses",
    ),
    [
        (
            "one-stale-warehouse",
            DecisionOutcome.RECONCILE,
            "warehouse-a",
            ["warehouse-b"],
            3,
            [],
        ),
        (
            "newer-singleton",
            DecisionOutcome.INVESTIGATE,
            "warehouse-c",
            ["warehouse-a", "warehouse-b"],
            4,
            ["warehouse-c"],
        ),
        (
            "same-version-divergence",
            DecisionOutcome.INVESTIGATE,
            "warehouse-a",
            ["warehouse-c"],
            6,
            list(WAREHOUSE_IDS),
        ),
    ],
)
def test_v2_scenarios_are_selective_explainable_and_read_only(
    scenario,
    initial,
    source,
    targets,
    total_calls,
    event_warehouses,
) -> None:
    state, requests = execute_scenario(scenario)
    decision = state.decisions["SKU-001"]
    event_requests = [
        request for request in requests if request[2].endswith("/events")
    ]

    assert state.status == RunStatus.COMPLETED
    assert state.decision_history["SKU-001"][0].outcome == initial
    assert decision.outcome == DecisionOutcome.RECONCILE
    assert decision.canonical_source == source
    assert decision.target_warehouses == targets
    assert state.metrics.total_api_calls == total_calls
    assert state.metrics.event_investigation_queries == len(event_warehouses)
    assert state.metrics.put_calls == 0
    assert [request[0] for request in event_requests] == event_warehouses
    assert all(request[2] == "/inventory/SKU-001/events" for request in event_requests)


def test_event_client_validates_and_measures_success_and_failure() -> None:
    endpoint = endpoints()["warehouse-a"]
    store = stores("newer-singleton")["warehouse-a"]
    responses = [
        httpx.Response(200, json=store.events("SKU-001", 10).model_dump(mode="json")),
        httpx.Response(200, json={"system_id": "wrong", "sku": "SKU-001", "events": []}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    async def exercise():
        recorder = MetricRecorder()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = WarehouseClient(http_client, recorder)
            history = await client.get_events(
                endpoint, "SKU-001", run_id="run-test", limit=7
            )
            with pytest.raises(WarehouseClientError):
                await client.get_events(
                    endpoint, "SKU-001", run_id="run-test", limit=7
                )
        return history, recorder.metrics

    history, metrics = asyncio.run(exercise())

    assert history.sku == "SKU-001"
    assert metrics.event_investigation_queries == 2
    assert metrics.successful_calls == 1
    assert metrics.failed_calls == 1
    assert metrics.api_calls[0].endpoint == "/inventory/SKU-001/events"
    assert metrics.api_calls[0].response_bytes > 0
    assert metrics.api_calls[1].error_type == "validation_error"
