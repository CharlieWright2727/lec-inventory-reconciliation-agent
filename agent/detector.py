"""Factual cross-warehouse product comparison for V1."""

from collections.abc import Collection
from datetime import datetime, timezone
from uuid import uuid4

from agent.models import (
    ConflictType,
    ProductConflict,
    ProductObservation,
    WarehouseObservation,
)


def build_product_observations(
    observations: dict[str, WarehouseObservation],
) -> dict[str, ProductObservation]:
    all_skus = sorted(
        {sku for observation in observations.values() for sku in observation.items}
    )
    return {
        sku: ProductObservation(
            sku=sku,
            records={
                warehouse_id: observation.items[sku]
                for warehouse_id, observation in observations.items()
                if sku in observation.items
            },
        )
        for sku in all_skus
    }


def detect_conflicts(
    products: dict[str, ProductObservation],
    warehouse_ids: Collection[str],
) -> tuple[list[str], dict[str, ProductConflict]]:
    expected_warehouses = set(warehouse_ids)
    consistent_skus: list[str] = []
    conflicts: dict[str, ProductConflict] = {}

    for sku in sorted(products):
        product = products[sku]
        conflict_types = _conflict_types(product, expected_warehouses)
        if not conflict_types:
            consistent_skus.append(sku)
            continue
        conflicts[sku] = ProductConflict(
            conflict_id=f"conflict-{uuid4()}",
            sku=sku,
            conflict_types=conflict_types,
            records=product.records,
            detected_at=datetime.now(timezone.utc),
        )

    return consistent_skus, conflicts


def _conflict_types(
    product: ProductObservation,
    expected_warehouses: set[str],
) -> list[ConflictType]:
    records = list(product.records.values())
    conflict_types: list[ConflictType] = []

    if len(
        {
            (
                record.inventory.on_hand,
                record.inventory.reserved,
                record.inventory.available,
            )
            for record in records
        }
    ) > 1:
        conflict_types.append(ConflictType.INVENTORY_MISMATCH)
    if len({record.state.version for record in records}) > 1:
        conflict_types.append(ConflictType.VERSION_MISMATCH)
    if len({record.sync.event_cursor for record in records}) > 1:
        conflict_types.append(ConflictType.EVENT_PROGRESS_MISMATCH)
    if len(
        {
            (record.product.sku, record.product.name, record.product.barcode)
            for record in records
        }
    ) > 1:
        conflict_types.append(ConflictType.PRODUCT_IDENTITY_MISMATCH)
    if set(product.records) != expected_warehouses:
        conflict_types.append(ConflictType.MISSING_SKU)

    return conflict_types
