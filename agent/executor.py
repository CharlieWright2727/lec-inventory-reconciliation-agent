"""Sequential execution of already validated reconciliation plans."""

from agent.client import WarehouseClient, WarehouseClientError
from agent.models import (
    ExecutionResult,
    ExecutionStatus,
    ReconciliationPlan,
    WarehouseEndpoint,
)
from warehouse.models import InventoryUpdateRequest


async def execute_reconciliation_plan(
    plan: ReconciliationPlan,
    endpoints: dict[str, WarehouseEndpoint],
    client: WarehouseClient,
    *,
    run_id: str,
) -> list[ExecutionResult]:
    """Execute actions in plan order and stop after the first failure."""
    results: list[ExecutionResult] = []
    for action in plan.actions:
        request = InventoryUpdateRequest(
            expected_current_version=action.expected_current_version,
            target_version=action.target_version,
            inventory=action.inventory,
            source=action.source,
            reason=action.reason,
        )
        try:
            response = await client.update_inventory(
                endpoints[action.warehouse_id],
                action.sku,
                request,
                run_id=run_id,
            )
        except WarehouseClientError as exc:
            results.append(
                ExecutionResult(
                    action_id=action.action_id,
                    warehouse_id=action.warehouse_id,
                    sku=action.sku,
                    status=(
                        ExecutionStatus.REJECTED
                        if exc.error_type == "http_error"
                        else ExecutionStatus.FAILED
                    ),
                    expected_version=action.expected_current_version,
                    target_version=action.target_version,
                    error=str(exc),
                )
            )
            break
        results.append(
            ExecutionResult(
                action_id=action.action_id,
                warehouse_id=action.warehouse_id,
                sku=action.sku,
                status=(
                    ExecutionStatus.SUCCESS
                    if response.status == "updated"
                    else ExecutionStatus.UNCHANGED
                ),
                expected_version=action.expected_current_version,
                target_version=action.target_version,
                response=response,
            )
        )
    return results
