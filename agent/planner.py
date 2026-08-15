"""Build selective, read-only investigation plans from current decisions."""

from agent.models import (
    ActionType,
    DecisionOutcome,
    EvidenceSet,
    InvestigationPlan,
    PlannedAction,
    ReconciliationDecision,
)


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
