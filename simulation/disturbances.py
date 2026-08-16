"""External warehouse disturbances; none contain reconciliation decisions."""

import asyncio
from datetime import datetime, timezone

from agent.models import WarehouseObservation
from simulation.client import SimulationWarehouseClient
from simulation.models import DisturbanceMetadata, DisturbanceType
from warehouse.models import InventoryEvent, InventoryRecord, SimulationCorruptionRequest, SimulationEventRequest


DISTURBANCE_SKUS = {
    DisturbanceType.STALE_REPLICA: "SKU-001",
    DisturbanceType.NEWER_LEGITIMATE_STATE: "SKU-002",
    DisturbanceType.MATERIALISED_CORRUPTION: "SKU-003",
    DisturbanceType.INCOMPLETE_HISTORY: "SKU-004",
    DisturbanceType.COMPETING_CAUSAL_BRANCHES: "SKU-005",
}


async def inject_disturbance(
    disturbance_type: DisturbanceType,
    client: SimulationWarehouseClient,
    observations: dict[str, WarehouseObservation],
) -> DisturbanceMetadata:
    """Inject exactly one disturbance into an already verified clean view."""
    sku = DISTURBANCE_SKUS[disturbance_type]
    baseline = _shared_baseline(sku, observations)

    if disturbance_type == DisturbanceType.STALE_REPLICA:
        request = _event_request(
            baseline,
            event_id="evt-live-stale-SKU-001",
            quantity_delta=20,
            reference="external-delivery-live-stale",
            minute=1,
        )
        await asyncio.gather(
            client.apply_event("warehouse-a", sku, request),
            client.apply_event("warehouse-c", sku, request),
        )
        return DisturbanceMetadata(
            disturbance_type=disturbance_type,
            sku=sku,
            affected_warehouses=["warehouse-a", "warehouse-c"],
            detail="A and C processed the same +20 external stock event; B missed it.",
        )

    if disturbance_type == DisturbanceType.NEWER_LEGITIMATE_STATE:
        request = _event_request(
            baseline,
            event_id="evt-live-newer-SKU-002",
            quantity_delta=15,
            reference="external-business-event-live-newer",
            minute=2,
        )
        await client.apply_event("warehouse-c", sku, request)
        return DisturbanceMetadata(
            disturbance_type=disturbance_type,
            sku=sku,
            affected_warehouses=["warehouse-c"],
            detail="C processed one valid +15 event extending the shared event tip.",
        )

    if disturbance_type == DisturbanceType.MATERIALISED_CORRUPTION:
        await client.corrupt(
            "warehouse-c",
            sku,
            SimulationCorruptionRequest(
                expected_current_version=baseline.state.version,
                on_hand=baseline.inventory.on_hand - 5,
                actor="cycle-count",
            ),
        )
        return DisturbanceMetadata(
            disturbance_type=disturbance_type,
            sku=sku,
            affected_warehouses=["warehouse-c"],
            detail=(
                "C's materialised on_hand was reduced by 5 without changing its "
                "version, cursor, or history."
            ),
        )

    if disturbance_type == DisturbanceType.INCOMPLETE_HISTORY:
        request = _event_request(
            baseline,
            event_id="evt-live-incomplete-SKU-004",
            quantity_delta=10,
            reference="warehouse-operation-live-incomplete",
            minute=4,
        )
        await client.apply_event("warehouse-c", sku, request)
        await client.replace_history(
            "warehouse-c",
            sku,
            expected_last_event_id=request.event.event_id,
            events=[request.event],
        )
        return DisturbanceMetadata(
            disturbance_type=disturbance_type,
            sku=sku,
            affected_warehouses=["warehouse-c"],
            detail="C advanced by +10 but exposes history without the shared anchor.",
        )

    if disturbance_type == DisturbanceType.COMPETING_CAUSAL_BRANCHES:
        branch_b = _event_request(
            baseline,
            event_id="evt-live-branch-b-SKU-005",
            quantity_delta=-10,
            reference="warehouse-operation-live-branch-b",
            minute=5,
        )
        branch_c = _event_request(
            baseline,
            event_id="evt-live-branch-c-SKU-005",
            quantity_delta=10,
            reference="warehouse-operation-live-branch-c",
            minute=5,
        )
        await asyncio.gather(
            client.apply_event("warehouse-b", sku, branch_b),
            client.apply_event("warehouse-c", sku, branch_c),
        )
        return DisturbanceMetadata(
            disturbance_type=disturbance_type,
            sku=sku,
            affected_warehouses=["warehouse-b", "warehouse-c"],
            detail="B and C independently extended the same event tip by -10 and +10.",
        )

    raise ValueError(f"unsupported disturbance: {disturbance_type}")


def _shared_baseline(
    sku: str,
    observations: dict[str, WarehouseObservation],
) -> InventoryRecord:
    records = [observation.items[sku] for observation in observations.values()]
    first = records[0]
    logical_keys = {
        (
            record.inventory.on_hand,
            record.inventory.reserved,
            record.inventory.available,
            record.state.version,
            record.sync.event_cursor,
            record.last_event.event_id,
        )
        for record in records
    }
    if len(logical_keys) != 1:
        raise ValueError(f"{sku} is not clean before disturbance injection")
    return first


def _event_request(
    baseline: InventoryRecord,
    *,
    event_id: str,
    quantity_delta: int,
    reference: str,
    minute: int,
) -> SimulationEventRequest:
    occurred_at = datetime(2026, 8, 15, 12, minute, tzinfo=timezone.utc)
    return SimulationEventRequest(
        expected_current_version=baseline.state.version,
        target_version=baseline.state.version + 1,
        actor="inventory-event-processor",
        event=InventoryEvent(
            event_id=event_id,
            type="stock_adjustment",
            quantity_delta=quantity_delta,
            occurred_at=occurred_at,
            processed_at=occurred_at,
            reference=reference,
        ),
    )
