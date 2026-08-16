"""Typed state and result models for the live warehouse simulation."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from agent.metrics import RunMetrics
from agent.models import ResolutionOutcome


class DisturbanceType(str, Enum):
    STALE_REPLICA = "stale-replica"
    NEWER_LEGITIMATE_STATE = "newer-legitimate-state"
    MATERIALISED_CORRUPTION = "materialised-corruption"
    INCOMPLETE_HISTORY = "incomplete-history"
    COMPETING_CAUSAL_BRANCHES = "competing-causal-branches"


class ExpectedAgentOutcome(str, Enum):
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"


class RoundStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"


class SimulationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class SimulationRound(BaseModel):
    round_id: int = Field(ge=1)
    disturbance_type: DisturbanceType
    display_name: str = Field(min_length=1)
    expected_outcome: ExpectedAgentOutcome
    expected_zero_writes: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: RoundStatus = RoundStatus.PENDING
    failure_reason: str | None = None


class DisturbanceMetadata(BaseModel):
    disturbance_type: DisturbanceType
    sku: str = Field(min_length=1)
    affected_warehouses: list[str] = Field(min_length=1)
    detail: str = Field(min_length=1)


class EnvironmentObservation(BaseModel):
    products: int = Field(ge=0)
    consistent: int = Field(ge=0)
    conflicts: int = Field(ge=0)
    conflicting_skus: list[str] = Field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.products == 10 and self.consistent == 10 and self.conflicts == 0


class AgentCost(BaseModel):
    agent_runs: int = Field(default=0, ge=0)
    api_calls: int = Field(default=0, ge=0)
    catalogue_observations: int = Field(default=0, ge=0)
    event_investigations: int = Field(default=0, ge=0)
    reconciliation_writes: int = Field(default=0, ge=0)
    verification_reads: int = Field(default=0, ge=0)
    request_bytes: int = Field(default=0, ge=0)
    response_bytes: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    api_latency_ms: float = Field(default=0, ge=0)
    wall_clock_ms: float = Field(default=0, ge=0)

    @classmethod
    def from_metrics(cls, metrics: RunMetrics) -> "AgentCost":
        return cls(
            agent_runs=1,
            api_calls=metrics.total_api_calls,
            catalogue_observations=metrics.catalogue_queries,
            event_investigations=metrics.event_investigation_queries,
            reconciliation_writes=metrics.reconciliation_writes,
            verification_reads=metrics.verification_reads,
            request_bytes=metrics.total_request_bytes,
            response_bytes=metrics.total_response_bytes,
            total_bytes=metrics.total_bytes_transferred,
            api_latency_ms=metrics.total_api_latency_ms,
            wall_clock_ms=metrics.wall_clock_time_ms,
        )

    def add(self, other: "AgentCost") -> "AgentCost":
        return AgentCost(
            **{
                field: getattr(self, field) + getattr(other, field)
                for field in type(self).model_fields
            }
        )


class SimulationControlCost(BaseModel):
    api_calls: int = Field(default=0, ge=0)
    successful_calls: int = Field(default=0, ge=0)
    failed_calls: int = Field(default=0, ge=0)
    reset_calls: int = Field(default=0, ge=0)
    mutation_calls: int = Field(default=0, ge=0)
    observation_calls: int = Field(default=0, ge=0)
    request_bytes: int = Field(default=0, ge=0)
    response_bytes: int = Field(default=0, ge=0)
    api_latency_ms: float = Field(default=0, ge=0)


class RoundResult(BaseModel):
    round_id: int = Field(ge=1)
    disturbance_type: DisturbanceType
    expected_outcome: ExpectedAgentOutcome
    actual_outcome: ResolutionOutcome | None = None
    status: RoundStatus
    conflicting_skus: list[str] = Field(default_factory=list)
    agent_runs: int = Field(default=0, ge=0)
    investigation_calls: int = Field(default=0, ge=0)
    reconciliation_writes: int = Field(default=0, ge=0)
    verification_reads: int = Field(default=0, ge=0)
    verification_failures: int = Field(default=0, ge=0)
    api_calls: int = Field(default=0, ge=0)
    request_bytes: int = Field(default=0, ge=0)
    response_bytes: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    api_latency_ms: float = Field(default=0, ge=0)
    wall_clock_ms: float = Field(default=0, ge=0)
    reset_required: bool = False
    reset_completed: bool = False
    clean_check_passed: bool = False
    failure_reason: str | None = None


class SimulationResult(BaseModel):
    seed: int
    round_order: list[DisturbanceType]
    rounds: list[RoundResult] = Field(default_factory=list)
    required_rounds: int = Field(default=5, ge=0)
    executed_rounds: int = Field(default=0, ge=0)
    passed_rounds: int = Field(default=0, ge=0)
    failed_rounds: int = Field(default=0, ge=0)
    resolved_rounds: int = Field(default=0, ge=0)
    escalated_rounds: int = Field(default=0, ge=0)
    unexpected_writes: int = Field(default=0, ge=0)
    verification_failures: int = Field(default=0, ge=0)
    reset_failures: int = Field(default=0, ge=0)
    total_agent_runs: int = Field(default=0, ge=0)
    cumulative_cost: AgentCost = Field(default_factory=AgentCost)
    simulation_control_cost: SimulationControlCost = Field(
        default_factory=SimulationControlCost
    )
    overall_result: SimulationStatus
    failure_reason: str | None = None
