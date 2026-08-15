import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from agent.client import WarehouseClient
from agent.detector import build_product_observations, detect_conflicts
from agent.metrics import ApiCallMetric, MetricRecorder, RunMetrics
from agent.models import (
    ConflictType,
    ProductConflict,
    ProductObservation,
    RunState,
    RunStatus,
    WarehouseEndpoint,
    WarehouseObservation,
)
from agent.observer import ObservationError, observe_warehouses
from agent.runner import run_agent
from warehouse.models import Inventory
from warehouse.store import WarehouseStore


ROOT = Path(__file__).parents[1]
PRODUCTS_PATH = ROOT / "warehouse" / "data" / "products.json"
SCENARIOS_ROOT = ROOT / "scenarios"
NOW = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)


def make_record(warehouse_id: str, sku: str = "SKU-001"):
    store = WarehouseStore(warehouse_id, PRODUCTS_PATH)
    return store.inventory(sku).model_dump(
        exclude={"system", "capabilities"}
    )


def inventory_record(warehouse_id: str, sku: str = "SKU-001"):
    from warehouse.models import InventoryRecord

    return InventoryRecord.model_validate(make_record(warehouse_id, sku))


def observation(warehouse_id: str, record) -> WarehouseObservation:
    return WarehouseObservation(
        warehouse_id=warehouse_id,
        observed_at=NOW,
        health_status="healthy",
        writable=True,
        items={record.product.sku: record},
    )


def detect_for_records(records: dict[str, object]):
    observations = {
        warehouse_id: observation(warehouse_id, record)
        for warehouse_id, record in records.items()
    }
    products = build_product_observations(observations)
    return products, detect_conflicts(
        products,
        ("warehouse-a", "warehouse-b", "warehouse-c"),
    )


def catalogue_payload(
    warehouse_id: str,
    *,
    scenario_name: str | None = None,
) -> dict:
    scenario_path = (
        SCENARIOS_ROOT / scenario_name / f"{warehouse_id}.json"
        if scenario_name
        else None
    )
    store = WarehouseStore(
        warehouse_id,
        PRODUCTS_PATH,
        scenario_data_path=scenario_path,
    )
    return store.catalogue().model_dump(mode="json")


def endpoints() -> dict[str, WarehouseEndpoint]:
    return {
        warehouse_id: WarehouseEndpoint(
            warehouse_id=warehouse_id,
            base_url=f"https://{warehouse_id}.test",
        )
        for warehouse_id in ("warehouse-a", "warehouse-b", "warehouse-c")
    }


def test_v1_models_represent_agent_state() -> None:
    record = inventory_record("warehouse-a")
    endpoint = WarehouseEndpoint(
        warehouse_id="warehouse-a",
        base_url="http://localhost:8001/",
    )
    warehouse_observation = observation("warehouse-a", record)
    product = ProductObservation(
        sku="SKU-001",
        records={"warehouse-a": record},
    )
    conflict = ProductConflict(
        conflict_id="conflict-test",
        sku="SKU-001",
        conflict_types=[
            ConflictType.INVENTORY_MISMATCH,
            ConflictType.VERSION_MISMATCH,
        ],
        records=product.records,
        detected_at=NOW,
    )
    state = RunState(
        run_id="run-test",
        started_at=NOW,
        completed_at=NOW,
        status=RunStatus.COMPLETED,
        warehouses={"warehouse-a": endpoint},
        observations={"warehouse-a": warehouse_observation},
        products={"SKU-001": product},
        conflicts={"SKU-001": conflict},
    )

    assert endpoint.base_url == "http://localhost:8001"
    assert warehouse_observation.items["SKU-001"] == record
    assert product.records["warehouse-a"] == record
    assert len(conflict.conflict_types) == 2
    assert state.status == RunStatus.COMPLETED


def test_warehouse_endpoint_rejects_blank_or_non_http_values() -> None:
    with pytest.raises(ValidationError):
        WarehouseEndpoint(warehouse_id="", base_url="http://localhost:8001")
    with pytest.raises(ValidationError):
        WarehouseEndpoint(warehouse_id="warehouse-a", base_url="localhost:8001")


def test_equivalent_records_are_consistent_despite_different_snapshot_ids() -> None:
    records = {
        warehouse_id: inventory_record(warehouse_id)
        for warehouse_id in ("warehouse-a", "warehouse-b", "warehouse-c")
    }
    products, (consistent, conflicts) = detect_for_records(records)

    assert set(products["SKU-001"].records) == set(records)
    assert consistent == ["SKU-001"]
    assert conflicts == {}
    assert len({record.state.snapshot_id for record in records.values()}) == 3


@pytest.mark.parametrize(
    ("mutation", "expected_type"),
    [
        ("inventory", ConflictType.INVENTORY_MISMATCH),
        ("version", ConflictType.VERSION_MISMATCH),
        ("event_cursor", ConflictType.EVENT_PROGRESS_MISMATCH),
        ("identity", ConflictType.PRODUCT_IDENTITY_MISMATCH),
    ],
)
def test_detector_classifies_logical_mismatches(
    mutation: str,
    expected_type: ConflictType,
) -> None:
    records = {
        warehouse_id: inventory_record(warehouse_id)
        for warehouse_id in ("warehouse-a", "warehouse-b", "warehouse-c")
    }
    changed = records["warehouse-b"]
    if mutation == "inventory":
        changed.inventory = Inventory(on_hand=120, reserved=1, available=119)
    elif mutation == "version":
        changed.state.version = 42
    elif mutation == "event_cursor":
        changed.sync.event_cursor = 1042
    else:
        changed.product.name = "Different product name"

    _, (consistent, conflicts) = detect_for_records(records)

    assert consistent == []
    assert conflicts["SKU-001"].conflict_types == [expected_type]


def test_detector_reports_a_sku_missing_from_one_warehouse() -> None:
    records = {
        warehouse_id: inventory_record(warehouse_id)
        for warehouse_id in ("warehouse-a", "warehouse-b")
    }
    _, (_, conflicts) = detect_for_records(records)

    assert conflicts["SKU-001"].conflict_types == [ConflictType.MISSING_SKU]


def test_run_metrics_derive_counts_bytes_and_latency_from_calls() -> None:
    metrics = RunMetrics(
        api_calls=[
            ApiCallMetric(
                request_id=f"request-{index}",
                run_id="run-test",
                warehouse_id=f"warehouse-{index}",
                method="GET",
                endpoint="/inventory",
                purpose="catalogue_observation",
                started_at=NOW,
                latency_ms=float(index),
                status_code=200,
                success=True,
                request_bytes=0,
                response_bytes=100 * index,
            )
            for index in range(1, 4)
        ]
    )

    assert metrics.total_api_calls == 3
    assert metrics.successful_calls == 3
    assert metrics.failed_calls == 0
    assert metrics.get_calls == 3
    assert metrics.put_calls == 0
    assert metrics.catalogue_queries == 3
    assert metrics.total_response_bytes == 600
    assert metrics.total_bytes_transferred == (
        metrics.total_request_bytes + metrics.total_response_bytes
    )
    assert metrics.total_api_latency_ms == 6


def test_observer_collects_all_catalogues_and_metrics_with_mock_http() -> None:
    warehouse_endpoints = endpoints()

    def handler(request: httpx.Request) -> httpx.Response:
        warehouse_id = request.url.host.removesuffix(".test")
        return httpx.Response(200, json=catalogue_payload(warehouse_id))

    async def exercise():
        recorder = MetricRecorder()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = WarehouseClient(http_client, recorder)
            observed = await observe_warehouses(
                warehouse_endpoints,
                client,
                run_id="run-test",
            )
        return observed, recorder.metrics

    observed, metrics = asyncio.run(exercise())

    assert set(observed) == set(warehouse_endpoints)
    assert all(len(item.items) == 10 for item in observed.values())
    assert metrics.total_api_calls == 3
    assert metrics.successful_calls == 3
    assert all(metric.response_bytes > 0 for metric in metrics.api_calls)
    assert all(metric.latency_ms >= 0 for metric in metrics.api_calls)


def test_failed_http_attempt_is_measured_and_aborts_observation() -> None:
    warehouse_endpoints = endpoints()

    def handler(request: httpx.Request) -> httpx.Response:
        warehouse_id = request.url.host.removesuffix(".test")
        if warehouse_id == "warehouse-c":
            raise httpx.ConnectError("warehouse unavailable", request=request)
        return httpx.Response(200, json=catalogue_payload(warehouse_id))

    async def exercise():
        recorder = MetricRecorder()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = WarehouseClient(http_client, recorder)
            with pytest.raises(ObservationError) as caught:
                await observe_warehouses(
                    warehouse_endpoints,
                    client,
                    run_id="run-test",
                )
        return caught.value, recorder.metrics

    error, metrics = asyncio.run(exercise())

    assert set(error.failures) == {"warehouse-c"}
    assert metrics.total_api_calls == 3
    assert metrics.successful_calls == 2
    assert metrics.failed_calls == 1
    failed_metric = next(metric for metric in metrics.api_calls if not metric.success)
    assert failed_metric.warehouse_id == "warehouse-c"
    assert failed_metric.status_code is None
    assert failed_metric.error_type == "connection_error"


def test_runner_marks_an_incomplete_warehouse_view_as_failed() -> None:
    warehouse_endpoints = endpoints()

    def handler(request: httpx.Request) -> httpx.Response:
        warehouse_id = request.url.host.removesuffix(".test")
        if warehouse_id == "warehouse-c":
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, json=catalogue_payload(warehouse_id))

    async def exercise() -> RunState:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            return await run_agent(warehouse_endpoints, http_client=http_client)

    state = asyncio.run(exercise())

    assert state.status == RunStatus.FAILED
    assert set(state.observation_errors) == {"warehouse-c"}
    assert state.observations == {}
    assert state.products == {}
    assert state.conflicts == {}
    assert state.metrics.total_api_calls == 3
    assert state.metrics.successful_calls == 2
    assert state.metrics.failed_calls == 1


@pytest.mark.parametrize(
    ("scenario_name", "expected_conflict_types"),
    [
        (
            "one-stale-warehouse",
            [
                ConflictType.INVENTORY_MISMATCH,
                ConflictType.VERSION_MISMATCH,
                ConflictType.EVENT_PROGRESS_MISMATCH,
            ],
        ),
        (
            "newer-singleton",
            [
                ConflictType.INVENTORY_MISMATCH,
                ConflictType.VERSION_MISMATCH,
                ConflictType.EVENT_PROGRESS_MISMATCH,
            ],
        ),
        (
            "same-version-divergence",
            [ConflictType.INVENTORY_MISMATCH],
        ),
    ],
)
def test_runner_detects_scenarios_read_only_through_catalogue_apis(
    scenario_name: str,
    expected_conflict_types: list[ConflictType],
) -> None:
    warehouse_endpoints = endpoints()
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        warehouse_id = request.url.host.removesuffix(".test")
        requests.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json=catalogue_payload(warehouse_id, scenario_name=scenario_name),
        )

    async def exercise() -> RunState:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            return await run_agent(warehouse_endpoints, http_client=http_client)

    state = asyncio.run(exercise())

    assert state.status == RunStatus.COMPLETED
    assert len(state.observations) == 3
    assert len(state.products) == 10
    assert len(state.consistent_skus) == 9
    assert list(state.conflicts) == ["SKU-001"]
    assert state.conflicts["SKU-001"].conflict_types == expected_conflict_types
    assert requests == [("GET", "/inventory")] * 3
    assert state.metrics.total_api_calls == 3
    assert state.metrics.put_calls == 0
