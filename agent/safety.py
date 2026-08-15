"""Whole-plan safety validation performed before any reconciliation write."""

from agent.models import (
    DecisionOutcome,
    EvidenceSet,
    ReconciliationDecision,
    ReconciliationPlan,
    SafetyCheck,
    SafetyValidationResult,
    WarehouseObservation,
)
from pydantic import ValidationError
from warehouse.models import Inventory


def validate_reconciliation_plan(
    plan: ReconciliationPlan,
    decision: ReconciliationDecision,
    evidence: EvidenceSet,
    observations: dict[str, WarehouseObservation],
) -> SafetyValidationResult:
    checks: list[SafetyCheck] = []

    def check(
        name: str,
        passed: bool,
        detail: str,
        action_id: str | None = None,
    ) -> None:
        checks.append(
            SafetyCheck(
                name=name,
                passed=passed,
                detail=detail,
                action_id=action_id,
            )
        )

    canonical = decision.canonical_state
    check(
        "decision_reconcile",
        decision.outcome == DecisionOutcome.RECONCILE,
        "Current policy decision must be RECONCILE.",
    )
    check(
        "canonical_state_present",
        canonical is not None and decision.canonical_source is not None,
        "An evidence-backed canonical source and state must exist.",
    )
    check(
        "plan_belongs_to_decision",
        plan.sku == decision.sku
        and plan.canonical_source == decision.canonical_source,
        "Plan SKU and canonical source must match the current decision.",
    )

    canonical_record = evidence.observed_records.get(plan.canonical_source)
    coherent_source = (
        canonical is not None
        and canonical_record is not None
        and canonical_record.product == canonical.product
        and canonical_record.state.snapshot_id == canonical.state.snapshot_id
        and canonical_record.last_event.event_id == canonical.last_event.event_id
    )
    check(
        "canonical_source_coherent",
        coherent_source,
        "Write source metadata must match the observed canonical record.",
    )

    action_warehouses = {action.warehouse_id for action in plan.actions}
    expected_action_warehouses = (
        set(plan.participating_warehouses)
        if plan.repair_revision
        else set(decision.target_warehouses)
    )
    check(
        "decision_targets_match",
        action_warehouses == expected_action_warehouses,
        "Write actions must exactly match the decision or shared repair scope.",
    )

    if plan.repair_revision:
        versions = {action.target_version for action in plan.actions}
        expected_repair_version = (
            max(record.state.version for record in evidence.observed_records.values())
            + 1
        )
        check(
            "repair_scope_complete",
            action_warehouses == set(evidence.observed_records),
            "A same-version repair must update every participating warehouse.",
        )
        check(
            "repair_version_consistent",
            versions == {expected_repair_version}
            and plan.target_version == expected_repair_version,
            "Every repair action must use the same next logical revision.",
        )

    for action in plan.actions:
        observation = observations.get(action.warehouse_id)
        observed_record = evidence.observed_records.get(action.warehouse_id)
        check(
            "target_configured",
            action.warehouse_id in observations,
            "Target warehouse must be configured and observed.",
            action.action_id,
        )
        check(
            "target_writable",
            observation is not None and observation.writable,
            "Target warehouse must report writable capability.",
            action.action_id,
        )
        check(
            "sku_observed",
            observation is not None and action.sku in observation.items,
            "Target SKU must exist in the warehouse observation.",
            action.action_id,
        )
        check(
            "product_identity_matches",
            canonical is not None
            and observed_record is not None
            and observed_record.product == canonical.product,
            "Target and canonical product identities must agree.",
            action.action_id,
        )
        check(
            "inventory_matches_plan",
            action.inventory == plan.target_inventory,
            "Action inventory must be the validated plan inventory.",
            action.action_id,
        )
        try:
            Inventory.model_validate(action.inventory.model_dump())
            inventory_valid = True
        except ValidationError:
            inventory_valid = False
        check(
            "inventory_valid",
            inventory_valid,
            "Action inventory must satisfy the warehouse Inventory model.",
            action.action_id,
        )
        current_version = (
            observed_record.state.version if observed_record is not None else None
        )
        check(
            "expected_version_matches_observation",
            current_version is not None
            and action.expected_current_version == current_version,
            "Optimistic concurrency version must match the observed version.",
            action.action_id,
        )
        check(
            "target_version_not_backward",
            current_version is not None and action.target_version >= current_version,
            "Target logical revision must not move backwards.",
            action.action_id,
        )
        check(
            "same_version_inventory_safe",
            observed_record is not None
            and not (
                action.target_version == observed_record.state.version
                and action.inventory != observed_record.inventory
            ),
            "Different inventory must never be written at the current version.",
            action.action_id,
        )
        check(
            "source_metadata_matches",
            coherent_source
            and action.source.system_id == plan.canonical_source
            and action.source.snapshot_id == canonical_record.state.snapshot_id
            and action.source.event_id == canonical_record.last_event.event_id,
            "Action source metadata must identify the canonical observation.",
            action.action_id,
        )

    failed = [item for item in checks if not item.passed]
    return SafetyValidationResult(
        sku=plan.sku,
        safe=not failed,
        checks=checks,
        rejection_reason=(
            None
            if not failed
            else "; ".join(f"{item.name}: {item.detail}" for item in failed)
        ),
    )
