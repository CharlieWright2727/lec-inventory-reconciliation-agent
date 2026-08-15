"""Independent post-write verification using the factual V1 detector."""

import asyncio
from datetime import datetime, timezone

from agent.client import WarehouseClient
from agent.detector import build_product_observations, detect_conflicts
from agent.models import (
    ConflictType,
    ReconciliationPlan,
    VerificationResult,
    WarehouseEndpoint,
    WarehouseObservation,
)
from warehouse.models import InventoryRecord


async def verify_reconciliation(
    plan: ReconciliationPlan,
    endpoints: dict[str, WarehouseEndpoint],
    client: WarehouseClient,
    *,
    run_id: str,
) -> VerificationResult:
    """Read every participant and prove the planned state has converged."""

    async def read(warehouse_id: str):
        try:
            response = await client.get_inventory(
                endpoints[warehouse_id], plan.sku, run_id=run_id
            )
        except Exception as exc:
            return warehouse_id, exc
        return warehouse_id, response

    results = await asyncio.gather(
        *(read(warehouse_id) for warehouse_id in plan.participating_warehouses)
    )
    records: dict[str, InventoryRecord] = {}
    missing: list[str] = []
    observations: dict[str, WarehouseObservation] = {}
    for warehouse_id, result in results:
        if isinstance(result, BaseException):
            missing.append(warehouse_id)
            continue
        record = InventoryRecord.model_validate(
            result.model_dump(exclude={"system", "capabilities"})
        )
        records[warehouse_id] = record
        observations[warehouse_id] = WarehouseObservation(
            warehouse_id=warehouse_id,
            observed_at=datetime.now(timezone.utc),
            health_status=result.system.health.status,
            writable=result.capabilities.writable,
            items={plan.sku: record},
        )

    remaining: list[ConflictType] = []
    if not missing:
        products = build_product_observations(observations)
        _, conflicts = detect_conflicts(
            products, plan.participating_warehouses
        )
        if plan.sku in conflicts:
            remaining = conflicts[plan.sku].conflict_types

    matches_plan = bool(records) and all(
        record.inventory == plan.target_inventory
        and record.state.version == plan.target_version
        for record in records.values()
    )
    verified = not missing and not remaining and matches_plan
    if missing:
        reason = f"Verification responses missing from: {', '.join(sorted(missing))}."
    elif remaining:
        reason = (
            "Distributed conflict remains: "
            + ", ".join(item.value for item in remaining)
            + "."
        )
    elif not matches_plan:
        reason = "Observed state is consistent but does not match the plan."
    else:
        reason = "All participating warehouses match the planned distributed state."

    return VerificationResult(
        sku=plan.sku,
        verified=verified,
        warehouse_records=records,
        expected_inventory=plan.target_inventory,
        expected_version=plan.target_version,
        missing_warehouses=sorted(missing),
        remaining_conflict_types=remaining,
        reason=reason,
    )
