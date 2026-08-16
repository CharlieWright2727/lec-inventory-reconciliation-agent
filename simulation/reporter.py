"""Readable terminal reporting for live simulation runs."""

from agent.models import RunState
from agent.runner import print_summary
from simulation.models import (
    DisturbanceMetadata,
    EnvironmentObservation,
    RoundResult,
    SimulationResult,
    SimulationRound,
    SimulationStatus,
)


RULE = "=" * 52


class SimulationReporter:
    def start(self, seed: int, rounds: list[SimulationRound]) -> None:
        print(RULE)
        print("LIVE WAREHOUSE RECONCILIATION SIMULATION")
        print(RULE)
        print()
        print(f"Seed: {seed}")
        print()
        print(f"Required rounds: {len(rounds)}")
        print()
        print("Random order:")
        for index, round_spec in enumerate(rounds, start=1):
            print(f"{index}. {round_spec.disturbance_type.value}")

    def initial_baseline(self, baseline: EnvironmentObservation) -> None:
        print()
        print("[INITIAL BASELINE]")
        self._environment(baseline)

    def round_start(
        self,
        position: int,
        total: int,
        round_spec: SimulationRound,
        baseline: EnvironmentObservation,
    ) -> None:
        print()
        print(RULE)
        print(f"ROUND {position}/{total} — {round_spec.display_name.upper()}")
        print(RULE)
        print()
        print("[BASELINE]")
        self._environment(baseline)

    def disturbance(self, disturbance: DisturbanceMetadata) -> None:
        print()
        print("[DISTURBANCE]")
        print(disturbance.detail)
        print(f"SKU: {disturbance.sku}")
        print(f"Warehouses: {', '.join(disturbance.affected_warehouses)}")

    def agent(self, state: RunState) -> None:
        print()
        print("[AGENT]")
        print_summary(state)

    def round_evaluation(self, result: RoundResult) -> None:
        print()
        print("[ROUND EVALUATION]")
        print()
        print(f"Expected: {result.expected_outcome.value}")
        print(
            "Actual:   "
            + (result.actual_outcome.value if result.actual_outcome else "NO OUTCOME")
        )
        print()
        print(f"Reconciliation writes: {result.reconciliation_writes}")
        print(f"Verification failures: {result.verification_failures}")
        print(f"Clean-state check: {'PASS' if result.clean_check_passed else 'FAIL'}")
        if result.failure_reason:
            print(f"Reason: {result.failure_reason}")
        print()
        print(f"ROUND RESULT: {result.status.value}")

    def reset(self, baseline: EnvironmentObservation) -> None:
        print()
        print("[RESET]")
        print()
        print("Warehouse baseline restored.")
        self._environment(baseline)

    def finish(self, result: SimulationResult) -> None:
        print()
        print(RULE)
        print(
            "LIVE SIMULATION COMPLETE"
            if result.overall_result == SimulationStatus.PASS
            else "SIMULATION ABORTED"
        )
        print(RULE)
        print()
        print(f"Seed: {result.seed}")
        print()
        print(f"Required rounds: {result.required_rounds}")
        print(f"Executed rounds: {result.executed_rounds}")
        print(f"Passed rounds: {result.passed_rounds}")
        print(f"Failed rounds: {result.failed_rounds}")
        print()
        print(f"Automatically resolved: {result.resolved_rounds}")
        print(f"Safely escalated: {result.escalated_rounds}")
        print()
        print(f"Unexpected writes: {result.unexpected_writes}")
        print(f"Verification failures: {result.verification_failures}")
        print(f"Reset failures: {result.reset_failures}")
        print()
        print("-" * 40)
        print("CUMULATIVE AGENT COST")
        print("-" * 40)
        print()
        cost = result.cumulative_cost
        print(f"Agent runs: {cost.agent_runs}")
        print(f"API calls: {cost.api_calls}")
        print(f"Catalogue observations: {cost.catalogue_observations}")
        print(f"Event investigations: {cost.event_investigations}")
        print(f"Reconciliation writes: {cost.reconciliation_writes}")
        print(f"Verification reads: {cost.verification_reads}")
        print()
        print(f"Request bytes: {cost.request_bytes}")
        print(f"Response bytes: {cost.response_bytes}")
        print(f"Total transferred: {cost.total_bytes}")
        print()
        print(f"API latency: {cost.api_latency_ms:.2f} ms")
        print(f"Wall-clock time: {cost.wall_clock_ms:.2f} ms")
        print()
        print("-" * 40)
        print("SIMULATION CONTROL COST")
        print("-" * 40)
        control = result.simulation_control_cost
        print(f"Control API calls: {control.api_calls}")
        print(f"Reset calls: {control.reset_calls}")
        print(f"Mutation calls: {control.mutation_calls}")
        print(f"Read-only observation calls: {control.observation_calls}")
        print()
        if result.failure_reason:
            print(f"Failure reason: {result.failure_reason}")
            print()
        print("-" * 40)
        print("FINAL RESULT")
        print("-" * 40)
        print()
        print(f"SIMULATION: {result.overall_result.value}")

    @staticmethod
    def _environment(observation: EnvironmentObservation) -> None:
        print(f"Products: {observation.products}")
        print(f"Consistent: {observation.consistent}")
        print(f"Conflicts: {observation.conflicts}")
