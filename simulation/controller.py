"""Strictly gated orchestration around the existing V3 reconciliation agent."""

import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from agent.models import ResolutionOutcome, RunState, RunStatus, WarehouseEndpoint
from agent.runner import load_warehouse_endpoints, run_agent_v3
from simulation.client import SimulationControlError, SimulationWarehouseClient
from simulation.disturbances import inject_disturbance
from simulation.evaluator import evaluate_round
from simulation.models import (
    AgentCost,
    DisturbanceType,
    EnvironmentObservation,
    ExpectedAgentOutcome,
    RoundResult,
    RoundStatus,
    SimulationResult,
    SimulationRound,
    SimulationStatus,
)

if TYPE_CHECKING:
    from simulation.reporter import SimulationReporter


def required_rounds() -> list[SimulationRound]:
    return [
        SimulationRound(
            round_id=1,
            disturbance_type=DisturbanceType.STALE_REPLICA,
            display_name="Stale replica",
            expected_outcome=ExpectedAgentOutcome.RESOLVED,
        ),
        SimulationRound(
            round_id=2,
            disturbance_type=DisturbanceType.NEWER_LEGITIMATE_STATE,
            display_name="Newer legitimate state",
            expected_outcome=ExpectedAgentOutcome.RESOLVED,
        ),
        SimulationRound(
            round_id=3,
            disturbance_type=DisturbanceType.MATERIALISED_CORRUPTION,
            display_name="Materialised corruption",
            expected_outcome=ExpectedAgentOutcome.RESOLVED,
        ),
        SimulationRound(
            round_id=4,
            disturbance_type=DisturbanceType.INCOMPLETE_HISTORY,
            display_name="Incomplete history",
            expected_outcome=ExpectedAgentOutcome.ESCALATED,
            expected_zero_writes=True,
        ),
        SimulationRound(
            round_id=5,
            disturbance_type=DisturbanceType.COMPETING_CAUSAL_BRANCHES,
            display_name="Competing causal branches",
            expected_outcome=ExpectedAgentOutcome.ESCALATED,
            expected_zero_writes=True,
        ),
    ]


def shuffled_rounds(seed: int) -> list[SimulationRound]:
    rounds = [item.model_copy(deep=True) for item in required_rounds()]
    random.Random(seed).shuffle(rounds)
    return rounds


async def run_simulation(
    seed: int,
    endpoints: dict[str, WarehouseEndpoint] | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
    reporter: "SimulationReporter | None" = None,
) -> SimulationResult:
    """Run all five disturbances once, stopping at the first failed gate."""
    endpoints = endpoints or load_warehouse_endpoints()
    rounds = shuffled_rounds(seed)

    async def execute(client: httpx.AsyncClient) -> SimulationResult:
        control = SimulationWarehouseClient(endpoints, client)
        if reporter:
            reporter.start(seed, rounds)

        try:
            await control.reset_all()
            baseline, observations = await control.observe_environment()
            if not baseline.clean:
                raise SimulationControlError(
                    "initial reset did not produce 10 consistent products"
                )
        except Exception as exc:
            result = _build_result(
                seed,
                rounds,
                [],
                AgentCost(),
                control,
                failure_reason=f"Initial baseline failed: {exc}",
                reset_failures=1,
            )
            if reporter:
                reporter.finish(result)
            return result

        if reporter:
            reporter.initial_baseline(baseline)

        results: list[RoundResult] = []
        cumulative = AgentCost()
        reset_failures = 0

        for position, round_spec in enumerate(rounds, start=1):
            round_spec.status = RoundStatus.RUNNING
            round_spec.started_at = datetime.now(timezone.utc)
            if reporter:
                reporter.round_start(position, len(rounds), round_spec, baseline)

            try:
                disturbance = await inject_disturbance(
                    round_spec.disturbance_type, control, observations
                )
                disturbed_view, _ = await control.observe_environment()
                if disturbed_view.conflicting_skus != [disturbance.sku]:
                    raise SimulationControlError(
                        "disturbance did not create exactly its intended conflict"
                    )
                if reporter:
                    reporter.disturbance(disturbance)

                agent_state = await run_agent_v3(endpoints, http_client=client)
                if reporter:
                    reporter.agent(agent_state)
            except Exception as exc:
                failed = RoundResult(
                    round_id=round_spec.round_id,
                    disturbance_type=round_spec.disturbance_type,
                    expected_outcome=round_spec.expected_outcome,
                    status=RoundStatus.FAIL,
                    failure_reason=f"Round execution failed: {exc}",
                )
                results.append(failed)
                if reporter:
                    reporter.round_evaluation(failed)
                break

            cost = AgentCost.from_metrics(agent_state.metrics)
            cumulative = cumulative.add(cost)
            actual = _terminal_outcome(agent_state, disturbance.sku)
            verification_succeeded = bool(
                agent_state.verification_results.get(disturbance.sku)
                and agent_state.verification_results[disturbance.sku].verified
            )
            verification_failures = sum(
                not verification.verified
                for verification in agent_state.verification_results.values()
            )
            reset_required = actual == ResolutionOutcome.ESCALATED
            reset_completed = False
            clean_check_passed = False
            clean_failure: str | None = None

            outcome_matches = (
                actual is not None
                and actual.value == round_spec.expected_outcome.value
            )
            writes_safe = not (
                round_spec.expected_zero_writes
                and agent_state.metrics.reconciliation_writes > 0
            )
            if outcome_matches and writes_safe:
                try:
                    if actual == ResolutionOutcome.ESCALATED:
                        await control.reset_all()
                        reset_completed = True
                    baseline, observations = await control.observe_environment()
                    clean_check_passed = baseline.clean
                    if not clean_check_passed:
                        clean_failure = "clean-state observation found remaining conflicts"
                        if actual == ResolutionOutcome.ESCALATED:
                            reset_failures += 1
                except Exception as exc:
                    clean_failure = str(exc)
                    if actual == ResolutionOutcome.ESCALATED:
                        reset_failures += 1

            evaluation = evaluate_round(
                round_spec,
                agent_status=agent_state.status,
                actual_outcome=actual,
                reconciliation_writes=agent_state.metrics.reconciliation_writes,
                verification_succeeded=verification_succeeded,
                clean_check_passed=clean_check_passed,
                reset_completed=reset_completed,
            )
            failure_reason = evaluation.failure_reason
            if clean_failure and failure_reason is None:
                failure_reason = clean_failure

            round_result = RoundResult(
                round_id=round_spec.round_id,
                disturbance_type=round_spec.disturbance_type,
                expected_outcome=round_spec.expected_outcome,
                actual_outcome=actual,
                status=evaluation.status,
                conflicting_skus=disturbed_view.conflicting_skus,
                agent_runs=1,
                investigation_calls=agent_state.metrics.event_investigation_queries,
                reconciliation_writes=agent_state.metrics.reconciliation_writes,
                verification_reads=agent_state.metrics.verification_reads,
                verification_failures=verification_failures,
                api_calls=agent_state.metrics.total_api_calls,
                request_bytes=agent_state.metrics.total_request_bytes,
                response_bytes=agent_state.metrics.total_response_bytes,
                total_bytes=agent_state.metrics.total_bytes_transferred,
                api_latency_ms=agent_state.metrics.total_api_latency_ms,
                wall_clock_ms=agent_state.metrics.wall_clock_time_ms,
                reset_required=reset_required,
                reset_completed=reset_completed,
                clean_check_passed=clean_check_passed,
                failure_reason=failure_reason,
            )
            results.append(round_result)
            round_spec.status = evaluation.status
            round_spec.completed_at = datetime.now(timezone.utc)
            round_spec.failure_reason = failure_reason
            if reporter:
                reporter.round_evaluation(round_result)
                if reset_completed:
                    reporter.reset(baseline)
            if evaluation.status == RoundStatus.FAIL:
                break

        result = _build_result(
            seed,
            rounds,
            results,
            cumulative,
            control,
            reset_failures=reset_failures,
        )
        if reporter:
            reporter.finish(result)
        return result

    if http_client is not None:
        return await execute(http_client)
    async with httpx.AsyncClient(timeout=10.0) as managed_client:
        return await execute(managed_client)


def _terminal_outcome(
    state: RunState, injected_sku: str
) -> ResolutionOutcome | None:
    if state.status != RunStatus.COMPLETED:
        return None
    if set(state.resolutions) != {injected_sku}:
        return None
    outcome = state.resolutions[injected_sku]
    if outcome not in {ResolutionOutcome.RESOLVED, ResolutionOutcome.ESCALATED}:
        return None
    return outcome


def _build_result(
    seed: int,
    rounds: list[SimulationRound],
    results: list[RoundResult],
    cumulative: AgentCost,
    control: SimulationWarehouseClient,
    *,
    failure_reason: str | None = None,
    reset_failures: int = 0,
) -> SimulationResult:
    passed = sum(item.status == RoundStatus.PASS for item in results)
    failed = sum(item.status == RoundStatus.FAIL for item in results)
    complete = len(results) == len(rounds) and failed == 0
    first_failure = next(
        (item.failure_reason for item in results if item.status == RoundStatus.FAIL),
        None,
    )
    return SimulationResult(
        seed=seed,
        round_order=[item.disturbance_type for item in rounds],
        rounds=results,
        required_rounds=len(rounds),
        executed_rounds=len(results),
        passed_rounds=passed,
        failed_rounds=failed,
        resolved_rounds=sum(
            item.status == RoundStatus.PASS
            and item.actual_outcome == ResolutionOutcome.RESOLVED
            for item in results
        ),
        escalated_rounds=sum(
            item.status == RoundStatus.PASS
            and item.actual_outcome == ResolutionOutcome.ESCALATED
            for item in results
        ),
        unexpected_writes=sum(
            item.reconciliation_writes
            for item in results
            if item.expected_outcome == ExpectedAgentOutcome.ESCALATED
        ),
        verification_failures=sum(item.verification_failures for item in results),
        reset_failures=reset_failures,
        total_agent_runs=cumulative.agent_runs,
        cumulative_cost=cumulative,
        simulation_control_cost=control.cost.model_copy(deep=True),
        overall_result=(SimulationStatus.PASS if complete else SimulationStatus.FAIL),
        failure_reason=failure_reason or first_failure,
    )
