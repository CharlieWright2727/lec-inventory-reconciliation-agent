"""Command-line orchestration for the read-only V1 and V2 agents."""

import asyncio
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from agent.client import WarehouseClient
from agent.detector import build_product_observations, detect_conflicts
from agent.evidence import extract_evidence
from agent.metrics import MetricRecorder, RunMetrics
from agent.models import (
    DecisionOutcome,
    PlannedAction,
    RunState,
    RunStatus,
    WarehouseEndpoint,
)
from agent.observer import ObservationError, observe_warehouses
from agent.planner import plan_investigation
from agent.policy import decide, exhausted_investigation_decision


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


async def run_agent_v2(
    endpoints: dict[str, WarehouseEndpoint] | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> RunState:
    """Run the bounded V2 evidence-gathering loop without warehouse writes."""
    started_at = datetime.now(timezone.utc)
    timer_started = time.perf_counter()
    run_id = f"run-{uuid4()}"
    endpoints = endpoints or load_warehouse_endpoints()
    metrics = RunMetrics()
    recorder = MetricRecorder(metrics)
    state = RunState(
        run_id=run_id,
        agent_version=2,
        started_at=started_at,
        status=RunStatus.STARTING,
        warehouses=endpoints,
        metrics=metrics,
    )

    async def execute(client: httpx.AsyncClient) -> None:
        warehouse_client = WarehouseClient(client, recorder)
        state.status = RunStatus.OBSERVING
        state.observations = await observe_warehouses(
            endpoints,
            warehouse_client,
            run_id=run_id,
        )
        state.status = RunStatus.ANALYSING
        state.products = build_product_observations(state.observations)
        state.consistent_skus, state.conflicts = detect_conflicts(
            state.products,
            state.warehouses,
        )

        planned_actions: list[PlannedAction] = []
        for sku, conflict in state.conflicts.items():
            evidence = extract_evidence(conflict)
            decision = decide(evidence)
            plan = plan_investigation(decision, evidence)
            state.evidence[sku] = evidence
            state.decisions[sku] = decision
            state.decision_history[sku] = [decision]
            state.plans[sku] = plan
            planned_actions.extend(plan.actions)

        if planned_actions:
            state.status = RunStatus.INVESTIGATING
            await _gather_investigation(
                planned_actions,
                state,
                warehouse_client,
            )

        state.status = RunStatus.REASSESSING
        for sku, conflict in state.conflicts.items():
            initial = state.decisions[sku]
            if initial.outcome != DecisionOutcome.INVESTIGATE:
                continue
            histories = state.event_histories.get(sku, {})
            evidence = extract_evidence(conflict, histories)
            reassessed = decide(evidence)
            if reassessed.outcome == DecisionOutcome.INVESTIGATE:
                remaining = plan_investigation(reassessed, evidence)
                if not remaining.actions or state.plans[sku].actions:
                    reassessed = exhausted_investigation_decision(reassessed)
            state.evidence[sku] = evidence
            state.decisions[sku] = reassessed
            state.decision_history[sku].append(reassessed)

        state.status = RunStatus.COMPLETED

    try:
        if http_client is None:
            async with httpx.AsyncClient(timeout=10.0) as managed_client:
                await execute(managed_client)
        else:
            await execute(http_client)
    except ObservationError as exc:
        state.status = RunStatus.FAILED
        state.observation_errors = exc.failures
    finally:
        state.completed_at = datetime.now(timezone.utc)
        state.metrics.wall_clock_time_ms = (
            time.perf_counter() - timer_started
        ) * 1000

    return state


async def _gather_investigation(
    actions: list[PlannedAction],
    state: RunState,
    client: WarehouseClient,
) -> None:
    async def query(action: PlannedAction):
        try:
            history = await client.get_events(
                state.warehouses[action.warehouse_id],
                action.sku,
                run_id=state.run_id,
                limit=action.limit,
            )
        except Exception as exc:  # recorded and converted to safe escalation
            return action, exc
        return action, history

    results = await asyncio.gather(*(query(action) for action in actions))
    for action, result in results:
        if isinstance(result, BaseException):
            state.investigation_errors[action.action_id] = str(result)
        else:
            state.event_histories.setdefault(action.sku, {})[
                action.warehouse_id
            ] = result


def print_summary(state: RunState) -> None:
    is_v2 = state.agent_version == 2
    print(f"INVENTORY RECONCILIATION AGENT — {'V2' if is_v2 else 'V1'}")
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

        if is_v2 and state.conflicts:
            print()
            print("[EVIDENCE]")
            for sku in state.conflicts:
                initial_evidence_ids = set(
                    state.decision_history[sku][0].evidence_ids
                )
                for finding in state.evidence[sku].findings:
                    if finding.evidence_id in initial_evidence_ids:
                        print(f"- {sku}: {finding.detail}")

            print()
            print("[DECISION]")
            for sku, history in state.decision_history.items():
                initial = history[0]
                print(f"{sku}: {initial.outcome.value}")
                print(f"  Reason: {initial.reason}")

            plans_with_actions = [
                plan for plan in state.plans.values() if plan.actions
            ]
            if plans_with_actions:
                print()
                print("[PLAN]")
                action_number = 1
                for plan in plans_with_actions:
                    for action in plan.actions:
                        print(
                            f"{action_number}. GET {action.warehouse_id} "
                            f"/inventory/{action.sku}/events"
                        )
                        action_number += 1

                print()
                print("[INVESTIGATE]")
                for sku, histories in sorted(state.event_histories.items()):
                    for warehouse_id in sorted(histories):
                        print(f"{warehouse_id} {sku} event history retrieved")
                for action_id, error in state.investigation_errors.items():
                    print(f"{action_id} failed: {error}")
                for sku, history in state.decision_history.items():
                    if len(history) < 2:
                        continue
                    initial_ids = set(history[0].evidence_ids)
                    final_ids = set(history[-1].evidence_ids)
                    new_findings = [
                        finding
                        for finding in state.evidence[sku].findings
                        if finding.evidence_id in final_ids - initial_ids
                    ]
                    if new_findings:
                        print(f"New evidence for {sku}:")
                        for finding in new_findings:
                            print(f"  - {finding.detail}")

                print()
                print("[REASSESS]")
                for sku, history in state.decision_history.items():
                    final = history[-1]
                    print(f"{sku}: {final.outcome.value}")
                    print(f"  Reason: {final.reason}")
                    if final.canonical_source:
                        print(f"  Canonical source: {final.canonical_source}")
                    if final.target_warehouses:
                        print(f"  Targets: {', '.join(final.target_warehouses)}")
            else:
                print()
                print("[FINAL DECISION]")
                for sku, decision in state.decisions.items():
                    print(f"{sku}: {decision.outcome.value}")
                    if decision.canonical_source:
                        print(f"  Canonical source: {decision.canonical_source}")
                    if decision.target_warehouses:
                        print(f"  Targets: {', '.join(decision.target_warehouses)}")

            print()
            print("V2 is read-only. No updates executed.")

    print()
    print("[COST]")
    print()
    print(f"API calls: {state.metrics.total_api_calls}")
    print(f"Successful: {state.metrics.successful_calls}")
    print(f"Failed: {state.metrics.failed_calls}")
    print(f"GET: {state.metrics.get_calls}")
    print(f"PUT: {state.metrics.put_calls}")
    print(f"Catalogue observation calls: {state.metrics.catalogue_queries}")
    print(
        "Event investigation calls: "
        f"{state.metrics.event_investigation_queries}"
    )
    print(f"Response bytes: {state.metrics.total_response_bytes}")
    print(f"Total API latency: {state.metrics.total_api_latency_ms:.2f} ms")
    print(f"Wall-clock time: {state.metrics.wall_clock_time_ms:.2f} ms")


async def main() -> int:
    state = await run_agent_v2()
    print_summary(state)
    return 0 if state.status == RunStatus.COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
