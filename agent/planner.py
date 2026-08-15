"""Build selective, read-only investigation plans from current decisions."""

from agent.models import (
    ActionType,
    DecisionOutcome,
    EvidenceSet,
    InvestigationPlan,
    PlannedAction,
    ReconciliationPlan,
    ReconciliationDecision,
    UpdateInventoryAction,
    WarehouseObservation,
)
from warehouse.models import UpdateSource


def plan_investigation(
    decision: ReconciliationDecision,
    evidence: EvidenceSet,
    *,
    limit: int = 10,
) -> InvestigationPlan:
    completed = set(evidence.investigated_warehouses)
    warehouses = (
        decision.investigation_warehouses
        if decision.outcome == DecisionOutcome.INVESTIGATE
        else []
    )
    actions = [
        PlannedAction(
            action_id=f"query-events:{decision.sku}:{warehouse_id}",
            type=ActionType.QUERY_EVENTS,
            warehouse_id=warehouse_id,
            sku=decision.sku,
            reason=decision.reason,
            limit=limit,
        )
        for warehouse_id in sorted(warehouses)
        if warehouse_id not in completed
    ]
    return InvestigationPlan(
        sku=decision.sku,
        actions=actions,
        reason=(
            decision.reason
            if actions
            else "No uncompleted evidence-gathering actions remain."
        ),
    )


def plan_reconciliation(
    decision: ReconciliationDecision,
    evidence: EvidenceSet,
    observations: dict[str, WarehouseObservation],
) -> ReconciliationPlan | None:
    """Turn an evidence-backed decision into explicit warehouse updates."""
    if (
        decision.outcome != DecisionOutcome.RECONCILE
        or decision.canonical_source is None
        or decision.canonical_state is None
    ):
        return None

    canonical = decision.canonical_state
    participating = sorted(evidence.observed_records)
    same_version_repair = any(
        evidence.observed_records[target].state.version
        == canonical.state.version
        and evidence.observed_records[target].inventory != canonical.inventory
        for target in decision.target_warehouses
        if target in evidence.observed_records
    )
    if same_version_repair:
        target_version = (
            max(
                record.state.version
                for record in evidence.observed_records.values()
            )
            + 1
        )
        mutation_targets = participating
        reason = (
            "Forward repair of an evidence-supported materialised state at a "
            "new shared logical revision."
        )
    else:
        target_version = canonical.state.version
        mutation_targets = sorted(decision.target_warehouses)
        reason = "Forward propagation of the evidence-supported canonical state."

    source = UpdateSource(
        system_id=decision.canonical_source,
        snapshot_id=canonical.state.snapshot_id,
        event_id=canonical.last_event.event_id,
    )
    actions = [
        UpdateInventoryAction(
            action_id=f"update-inventory:{decision.sku}:{warehouse_id}",
            warehouse_id=warehouse_id,
            sku=decision.sku,
            expected_current_version=(
                evidence.observed_records[warehouse_id].state.version
            ),
            target_version=target_version,
            inventory=canonical.inventory.model_copy(deep=True),
            source=source.model_copy(deep=True),
            reason=reason,
        )
        for warehouse_id in mutation_targets
        if warehouse_id in evidence.observed_records
    ]
    if not actions:
        return None
    return ReconciliationPlan(
        sku=decision.sku,
        canonical_source=decision.canonical_source,
        target_inventory=canonical.inventory.model_copy(deep=True),
        target_version=target_version,
        participating_warehouses=participating,
        repair_revision=same_version_repair,
        reason=reason,
        actions=actions,
    )
