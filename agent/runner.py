"""Command-line orchestration for the read-only V1 agent."""

import asyncio
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from agent.client import WarehouseClient
from agent.detector import build_product_observations, detect_conflicts
from agent.metrics import MetricRecorder, RunMetrics
from agent.models import RunState, RunStatus, WarehouseEndpoint
from agent.observer import ObservationError, observe_warehouses


DEFAULT_WAREHOUSE_URLS = {
    "warehouse-a": "http://localhost:8001",
    "warehouse-b": "http://localhost:8002",
    "warehouse-c": "http://localhost:8003",
}


def load_warehouse_endpoints() -> dict[str, WarehouseEndpoint]:
    return {
        warehouse_id: WarehouseEndpoint(
            warehouse_id=warehouse_id,
            base_url=os.getenv(
                f"WAREHOUSE_{warehouse_id[-1].upper()}_URL",
                default_url,
            ),
        )
        for warehouse_id, default_url in DEFAULT_WAREHOUSE_URLS.items()
    }


async def run_agent(
    endpoints: dict[str, WarehouseEndpoint] | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> RunState:
    started_at = datetime.now(timezone.utc)
    timer_started = time.perf_counter()
    run_id = f"run-{uuid4()}"
    endpoints = endpoints or load_warehouse_endpoints()
    metrics = RunMetrics()
    recorder = MetricRecorder(metrics)
    state = RunState(
        run_id=run_id,
        started_at=started_at,
        status=RunStatus.STARTING,
        warehouses=endpoints,
        metrics=metrics,
    )

    async def observe(client: httpx.AsyncClient) -> None:
        state.status = RunStatus.OBSERVING
        warehouse_client = WarehouseClient(client, recorder)
        state.observations = await observe_warehouses(
            endpoints,
            warehouse_client,
            run_id=run_id,
        )

    try:
        if http_client is None:
            async with httpx.AsyncClient(timeout=10.0) as managed_client:
                await observe(managed_client)
        else:
            await observe(http_client)
    except ObservationError as exc:
        state.status = RunStatus.FAILED
        state.observation_errors = exc.failures
    else:
        state.status = RunStatus.ANALYSING
        state.products = build_product_observations(state.observations)
        state.consistent_skus, state.conflicts = detect_conflicts(
            state.products,
            state.warehouses,
        )
        state.status = RunStatus.COMPLETED
    finally:
        state.completed_at = datetime.now(timezone.utc)
        state.metrics.wall_clock_time_ms = (
            time.perf_counter() - timer_started
        ) * 1000

    return state


def print_summary(state: RunState) -> None:
    print("INVENTORY RECONCILIATION AGENT — V1")
    print()

    if state.status == RunStatus.FAILED:
        print("Run failed.")
        print()
        for warehouse_id, error in state.observation_errors.items():
            print(f"Unable to observe {warehouse_id}: {error}")
        print()
        print(
            "No conflict analysis was performed because the warehouse view "
            "was incomplete."
        )
    else:
        print("[OBSERVE]")
        print()
        for warehouse_id in sorted(state.observations):
            observation = state.observations[warehouse_id]
            print(warehouse_id)
            print(f"  products: {len(observation.items)}")
            print(f"  status: {observation.health_status}")
        print()
        print("[ANALYSE]")
        print()
        print(f"Products discovered: {len(state.products)}")
        print(f"Consistent: {len(state.consistent_skus)}")
        print(f"Conflicts: {len(state.conflicts)}")

        for sku, conflict in state.conflicts.items():
            print()
            print(f"{sku} — CONFLICT")
            print("  Types:")
            for conflict_type in conflict.conflict_types:
                print(f"    {conflict_type.value}")
            for warehouse_id in sorted(conflict.records):
                record = conflict.records[warehouse_id]
                print()
                print(f"  {warehouse_id}:")
                print(f"    on_hand: {record.inventory.on_hand}")
                print(f"    reserved: {record.inventory.reserved}")
                print(f"    available: {record.inventory.available}")
                print(f"    version: {record.state.version}")
                print(f"    event_cursor: {record.sync.event_cursor}")

    print()
    print("[COST]")
    print()
    print(f"API calls: {state.metrics.total_api_calls}")
    print(f"Successful: {state.metrics.successful_calls}")
    print(f"Failed: {state.metrics.failed_calls}")
    print(f"GET: {state.metrics.get_calls}")
    print(f"PUT: {state.metrics.put_calls}")
    print(f"Response bytes: {state.metrics.total_response_bytes}")
    print(f"Total API latency: {state.metrics.total_api_latency_ms:.2f} ms")
    print(f"Wall-clock time: {state.metrics.wall_clock_time_ms:.2f} ms")


async def main() -> int:
    state = await run_agent()
    print_summary(state)
    return 0 if state.status == RunStatus.COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
