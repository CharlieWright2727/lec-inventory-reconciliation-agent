import asyncio

import httpx

import simulation.controller as controller
from simulation.controller import run_simulation
from simulation.models import (
    DisturbanceType,
    ExpectedAgentOutcome,
    RoundStatus,
    SimulationStatus,
)
from tests.simulation_support import MultiWarehouseTransport, simulation_endpoints


def test_complete_fixed_seed_live_simulation_passes() -> None:
    async def exercise():
        transport = MultiWarehouseTransport()
        async with httpx.AsyncClient(transport=transport) as http_client:
            return await run_simulation(
                81724,
                simulation_endpoints(),
                http_client=http_client,
            )

    result = asyncio.run(exercise())

    assert result.required_rounds == 5
    assert result.executed_rounds == 5
    assert result.passed_rounds == 5
    assert result.failed_rounds == 0
    assert result.resolved_rounds == 3
    assert result.escalated_rounds == 2
    assert result.unexpected_writes == 0
    assert result.verification_failures == 0
    assert result.reset_failures == 0
    assert result.total_agent_runs == 5
    assert result.overall_result == SimulationStatus.PASS
    assert all(item.status == RoundStatus.PASS for item in result.rounds)
    assert all(item.clean_check_passed for item in result.rounds)
    assert all(
        item.reconciliation_writes == 0
        for item in result.rounds
        if item.expected_outcome == ExpectedAgentOutcome.ESCALATED
    )
    assert set(result.round_order) == set(DisturbanceType)
    assert result.cumulative_cost.api_calls == sum(
        item.api_calls for item in result.rounds
    )
    assert result.simulation_control_cost.api_calls > 0


def test_simulation_stops_before_injecting_a_second_round_on_failure(
    monkeypatch,
) -> None:
    calls = 0

    async def fail_first_injection(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("forced disturbance failure")

    monkeypatch.setattr(controller, "inject_disturbance", fail_first_injection)

    async def exercise():
        transport = MultiWarehouseTransport()
        async with httpx.AsyncClient(transport=transport) as http_client:
            return await run_simulation(
                81724,
                simulation_endpoints(),
                http_client=http_client,
            )

    result = asyncio.run(exercise())

    assert calls == 1
    assert result.executed_rounds == 1
    assert result.failed_rounds == 1
    assert result.passed_rounds == 0
    assert result.overall_result == SimulationStatus.FAIL
    assert result.rounds[0].failure_reason == (
        "Round execution failed: forced disturbance failure"
    )
