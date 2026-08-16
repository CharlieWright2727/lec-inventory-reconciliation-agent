"""Independent PASS/FAIL judgement for one terminal V3 outcome."""

from dataclasses import dataclass

from agent.models import ResolutionOutcome, RunStatus
from simulation.models import ExpectedAgentOutcome, RoundStatus, SimulationRound


@dataclass(frozen=True)
class RoundEvaluation:
    status: RoundStatus
    failure_reason: str | None = None


def evaluate_round(
    round_spec: SimulationRound,
    *,
    agent_status: RunStatus,
    actual_outcome: ResolutionOutcome | None,
    reconciliation_writes: int,
    verification_succeeded: bool,
    clean_check_passed: bool,
    reset_completed: bool,
) -> RoundEvaluation:
    """Judge V3 without changing or duplicating its reconciliation policy."""
    if agent_status != RunStatus.COMPLETED:
        return RoundEvaluation(RoundStatus.FAIL, "V3 did not complete successfully.")
    if actual_outcome is None:
        return RoundEvaluation(
            RoundStatus.FAIL,
            "V3 did not return one terminal outcome for the injected conflict.",
        )

    expected = round_spec.expected_outcome.value
    if actual_outcome.value != expected:
        return RoundEvaluation(
            RoundStatus.FAIL,
            f"Expected {expected}, but V3 returned {actual_outcome.value}.",
        )

    if round_spec.expected_outcome == ExpectedAgentOutcome.RESOLVED:
        if not verification_succeeded:
            return RoundEvaluation(
                RoundStatus.FAIL, "V3 did not verify the resolved state."
            )
        if not clean_check_passed:
            return RoundEvaluation(
                RoundStatus.FAIL,
                "The full post-resolution environment was not clean.",
            )
        return RoundEvaluation(RoundStatus.PASS)

    if reconciliation_writes != 0:
        return RoundEvaluation(
            RoundStatus.FAIL,
            "An expected escalation performed reconciliation writes.",
        )
    if not reset_completed:
        return RoundEvaluation(
            RoundStatus.FAIL, "The escalated environment was not reset."
        )
    if not clean_check_passed:
        return RoundEvaluation(
            RoundStatus.FAIL, "The reset environment was not clean."
        )
    return RoundEvaluation(RoundStatus.PASS)
