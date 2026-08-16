import pytest

from agent.models import ResolutionOutcome, RunStatus
from simulation.controller import required_rounds, shuffled_rounds
from simulation.evaluator import evaluate_round
from simulation.models import (
    DisturbanceType,
    ExpectedAgentOutcome,
    RoundStatus,
    SimulationRound,
)


def spec(expected: ExpectedAgentOutcome) -> SimulationRound:
    return SimulationRound(
        round_id=1,
        disturbance_type=DisturbanceType.STALE_REPLICA,
        display_name="test",
        expected_outcome=expected,
        expected_zero_writes=expected == ExpectedAgentOutcome.ESCALATED,
    )


@pytest.mark.parametrize(
    (
        "expected",
        "actual",
        "writes",
        "verified",
        "clean",
        "reset",
        "status",
    ),
    [
        (
            ExpectedAgentOutcome.RESOLVED,
            ResolutionOutcome.RESOLVED,
            1,
            True,
            True,
            False,
            RoundStatus.PASS,
        ),
        (
            ExpectedAgentOutcome.RESOLVED,
            ResolutionOutcome.ESCALATED,
            0,
            False,
            False,
            False,
            RoundStatus.FAIL,
        ),
        (
            ExpectedAgentOutcome.ESCALATED,
            ResolutionOutcome.ESCALATED,
            0,
            False,
            True,
            True,
            RoundStatus.PASS,
        ),
        (
            ExpectedAgentOutcome.ESCALATED,
            ResolutionOutcome.RESOLVED,
            0,
            True,
            True,
            False,
            RoundStatus.FAIL,
        ),
        (
            ExpectedAgentOutcome.ESCALATED,
            ResolutionOutcome.ESCALATED,
            1,
            False,
            True,
            True,
            RoundStatus.FAIL,
        ),
    ],
)
def test_round_evaluator(
    expected, actual, writes, verified, clean, reset, status
) -> None:
    result = evaluate_round(
        spec(expected),
        agent_status=RunStatus.COMPLETED,
        actual_outcome=actual,
        reconciliation_writes=writes,
        verification_succeeded=verified,
        clean_check_passed=clean,
        reset_completed=reset,
    )

    assert result.status == status


def test_seeded_order_contains_every_required_round_exactly_once() -> None:
    first = [item.disturbance_type for item in shuffled_rounds(81724)]
    repeated = [item.disturbance_type for item in shuffled_rounds(81724)]

    assert first == repeated
    assert len(first) == 5
    assert set(first) == set(DisturbanceType)
    assert len(first) == len(set(first))
    assert len(required_rounds()) == 5
    assert first != [item.disturbance_type for item in required_rounds()]
