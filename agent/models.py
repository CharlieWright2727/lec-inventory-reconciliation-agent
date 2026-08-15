"""Structured state models for all reconciliation agent versions."""

from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from agent.metrics import RunMetrics
from warehouse.models import (
    EventHistoryResponse,
    Inventory,
    InventoryRecord,
    InventoryUpdateResponse,
    UpdateSource,
)


class WarehouseEndpoint(BaseModel):
    warehouse_id: str = Field(min_length=1)
    base_url: str = Field(min_length=1)

    @field_validator("warehouse_id", "base_url")
    @classmethod
    def strip_non_empty_values(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        if stripped.startswith(("http://", "https://")):
            return stripped.rstrip("/")
        return stripped

    @field_validator("base_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must use http:// or https://")
        return value


class WarehouseObservation(BaseModel):
    warehouse_id: str = Field(min_length=1)
    observed_at: datetime
    health_status: str = Field(min_length=1)
    writable: bool
    items: dict[str, InventoryRecord]

    @model_validator(mode="after")
    def validate_item_keys(self) -> "WarehouseObservation":
        for sku, record in self.items.items():
            if sku != record.product.sku:
                raise ValueError("item key must match the record SKU")
        return self


class ProductObservation(BaseModel):
    sku: str = Field(min_length=1)
    records: dict[str, InventoryRecord]

    @model_validator(mode="after")
    def validate_record_skus(self) -> "ProductObservation":
        if any(record.product.sku != self.sku for record in self.records.values()):
            raise ValueError("all records must belong to the observed SKU")
        return self


class ConflictType(str, Enum):
    INVENTORY_MISMATCH = "inventory_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    EVENT_PROGRESS_MISMATCH = "event_progress_mismatch"
    PRODUCT_IDENTITY_MISMATCH = "product_identity_mismatch"
    MISSING_SKU = "missing_sku"


class ProductConflict(BaseModel):
    conflict_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    conflict_types: list[ConflictType] = Field(min_length=1)
    records: dict[str, InventoryRecord]
    detected_at: datetime


class EvidenceType(str, Enum):
    WAREHOUSES_AGREE = "warehouses_agree"
    WAREHOUSE_BEHIND = "warehouse_behind"
    WAREHOUSE_AHEAD = "warehouse_ahead"
    SAME_VERSION_DIVERGENCE = "same_version_divergence"
    EVENT_PROGRESS_BEHIND = "event_progress_behind"
    EVENT_PROGRESS_AHEAD = "event_progress_ahead"
    EVENT_HISTORY_SUPPORTS_STATE = "event_history_supports_state"
    EVENT_HISTORY_CONTRADICTS_STATE = "event_history_contradicts_state"
    EVENT_HISTORY_EXTENDS_KNOWN_STATE = "event_history_extends_known_state"
    EVENT_HISTORIES_AGREE = "event_histories_agree"
    EVENT_HISTORIES_CONTRADICT = "event_histories_contradict"
    INCOMPATIBLE_PRODUCT_IDENTITY = "incompatible_product_identity"
    MISSING_WAREHOUSE_STATE = "missing_warehouse_state"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class LogicalState(BaseModel):
    inventory: Inventory
    version: int = Field(ge=0)
    event_cursor: int = Field(ge=0)
    last_event_id: str = Field(min_length=1)


class AgreementGroup(BaseModel):
    warehouse_ids: list[str] = Field(min_length=1)
    state: LogicalState


class EvidenceFinding(BaseModel):
    evidence_id: str = Field(min_length=1)
    type: EvidenceType
    warehouse_ids: list[str] = Field(default_factory=list)
    related_warehouse_ids: list[str] = Field(default_factory=list)
    detail: str = Field(min_length=1)
    derived_on_hand: int | None = Field(default=None, ge=0)
    anchor_event_id: str | None = None
    subsequent_event_ids: list[str] = Field(default_factory=list)


class EvidenceSet(BaseModel):
    sku: str = Field(min_length=1)
    findings: list[EvidenceFinding] = Field(default_factory=list)
    agreement_groups: list[AgreementGroup] = Field(default_factory=list)
    observed_records: dict[str, InventoryRecord]
    investigated_warehouses: list[str] = Field(default_factory=list)

    def findings_of_type(self, evidence_type: EvidenceType) -> list[EvidenceFinding]:
        return [item for item in self.findings if item.type == evidence_type]


class DecisionOutcome(str, Enum):
    NO_ACTION = "NO_ACTION"
    INVESTIGATE = "INVESTIGATE"
    RECONCILE = "RECONCILE"
    ESCALATE = "ESCALATE"


class ReconciliationDecision(BaseModel):
    sku: str = Field(min_length=1)
    outcome: DecisionOutcome
    target_warehouses: list[str] = Field(default_factory=list)
    canonical_source: str | None = None
    canonical_state: InventoryRecord | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    requires_investigation: bool = False
    investigation_warehouses: list[str] = Field(default_factory=list)


class ActionType(str, Enum):
    QUERY_EVENTS = "query_events"


class PlannedAction(BaseModel):
    action_id: str = Field(min_length=1)
    type: ActionType
    warehouse_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class InvestigationPlan(BaseModel):
    sku: str = Field(min_length=1)
    actions: list[PlannedAction] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class UpdateInventoryAction(BaseModel):
    action_id: str = Field(min_length=1)
    warehouse_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    expected_current_version: int = Field(ge=0)
    target_version: int = Field(ge=0)
    inventory: Inventory
    source: UpdateSource
    reason: str = Field(min_length=1)


class ReconciliationPlan(BaseModel):
    sku: str = Field(min_length=1)
    canonical_source: str = Field(min_length=1)
    target_inventory: Inventory
    target_version: int = Field(ge=0)
    participating_warehouses: list[str] = Field(min_length=1)
    repair_revision: bool = False
    reason: str = Field(min_length=1)
    actions: list[UpdateInventoryAction] = Field(min_length=1)


class SafetyCheck(BaseModel):
    name: str = Field(min_length=1)
    passed: bool
    detail: str = Field(min_length=1)
    action_id: str | None = None


class SafetyValidationResult(BaseModel):
    sku: str = Field(min_length=1)
    safe: bool
    checks: list[SafetyCheck] = Field(default_factory=list)
    rejection_reason: str | None = None


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    UNCHANGED = "unchanged"
    REJECTED = "rejected"
    FAILED = "failed"


class ExecutionResult(BaseModel):
    action_id: str = Field(min_length=1)
    warehouse_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    status: ExecutionStatus
    expected_version: int = Field(ge=0)
    target_version: int = Field(ge=0)
    response: InventoryUpdateResponse | None = None
    error: str | None = None


class VerificationResult(BaseModel):
    sku: str = Field(min_length=1)
    verified: bool
    warehouse_records: dict[str, InventoryRecord] = Field(default_factory=dict)
    expected_inventory: Inventory
    expected_version: int = Field(ge=0)
    missing_warehouses: list[str] = Field(default_factory=list)
    remaining_conflict_types: list[ConflictType] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class ResolutionOutcome(str, Enum):
    RESOLVED = "RESOLVED"
    NO_ACTION = "NO_ACTION"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class RunStatus(str, Enum):
    STARTING = "starting"
    OBSERVING = "observing"
    ANALYSING = "analysing"
    INVESTIGATING = "investigating"
    REASSESSING = "reassessing"
    PLANNING = "planning"
    VALIDATING = "validating"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


class RunState(BaseModel):
    run_id: str = Field(min_length=1)
    agent_version: int = Field(default=1, ge=1, le=3)
    started_at: datetime
    completed_at: datetime | None = None
    status: RunStatus
    warehouses: dict[str, WarehouseEndpoint]
    observations: dict[str, WarehouseObservation] = Field(default_factory=dict)
    products: dict[str, ProductObservation] = Field(default_factory=dict)
    consistent_skus: list[str] = Field(default_factory=list)
    conflicts: dict[str, ProductConflict] = Field(default_factory=dict)
    event_histories: dict[str, dict[str, EventHistoryResponse]] = Field(
        default_factory=dict
    )
    evidence: dict[str, EvidenceSet] = Field(default_factory=dict)
    decisions: dict[str, ReconciliationDecision] = Field(default_factory=dict)
    decision_history: dict[str, list[ReconciliationDecision]] = Field(
        default_factory=dict
    )
    plans: dict[str, InvestigationPlan] = Field(default_factory=dict)
    investigation_errors: dict[str, str] = Field(default_factory=dict)
    reconciliation_plans: dict[str, ReconciliationPlan] = Field(
        default_factory=dict
    )
    safety_results: dict[str, SafetyValidationResult] = Field(
        default_factory=dict
    )
    execution_results: dict[str, list[ExecutionResult]] = Field(
        default_factory=dict
    )
    verification_results: dict[str, VerificationResult] = Field(
        default_factory=dict
    )
    resolutions: dict[str, ResolutionOutcome] = Field(default_factory=dict)
    observation_errors: dict[str, str] = Field(default_factory=dict)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
