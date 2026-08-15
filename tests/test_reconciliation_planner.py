import asyncio

import httpx

from agent.models import DecisionOutcome
from agent.planner import plan_reconciliation
from agent.runner import run_agent_v2
from tests.v3_support import V3Harness, endpoints


def v2_state(scenario: str):
    harness = V3Harness(scenario)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(harness.handler)
        ) as client:
            return await run_agent_v2(endpoints(), http_client=client)

    return asyncio.run(exercise())


def test_plans_normal_forward_propagation_for_older_target() -> None:
    state = v2_state("one-stale-warehouse")
    plan = plan_reconciliation(
        state.decisions["SKU-001"],
        state.evidence["SKU-001"],
        state.observations,
    )

    assert plan.repair_revision is False
    assert [(a.warehouse_id, a.expected_current_version, a.target_version) for a in plan.actions] == [
        ("warehouse-b", 41, 42)
    ]


def test_plans_two_forward_updates_toward_newer_canonical_state() -> None:
    state = v2_state("newer-singleton")
    plan = plan_reconciliation(
        state.decisions["SKU-001"],
        state.evidence["SKU-001"],
        state.observations,
    )

    assert plan.repair_revision is False
    assert [action.warehouse_id for action in plan.actions] == [
        "warehouse-a",
        "warehouse-b",
    ]
    assert {action.target_version for action in plan.actions} == {43}


def test_same_version_repair_advances_every_participant() -> None:
    state = v2_state("same-version-divergence")
    plan = plan_reconciliation(
        state.decisions["SKU-001"],
        state.evidence["SKU-001"],
        state.observations,
    )

    assert plan.repair_revision is True
    assert plan.target_version == 43
    assert [action.warehouse_id for action in plan.actions] == [
        "warehouse-a",
        "warehouse-b",
        "warehouse-c",
    ]
    assert {action.expected_current_version for action in plan.actions} == {42}


def test_non_reconcile_decision_has_no_write_plan() -> None:
    state = v2_state("one-stale-warehouse")
    decision = state.decisions["SKU-001"].model_copy(
        update={"outcome": DecisionOutcome.ESCALATE}
    )

    assert (
        plan_reconciliation(
            decision, state.evidence["SKU-001"], state.observations
        )
        is None
    )
