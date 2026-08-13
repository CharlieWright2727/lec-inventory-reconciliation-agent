"""Load deterministic starting inventory from the shared product catalogue."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from warehouse.models import (
    DataQuality,
    Inventory,
    InventoryEvent,
    InventoryRecord,
    InventoryState,
    Product,
    SyncMetadata,
)


STARTING_VERSION = 41
STARTED_AT = datetime(2026, 8, 13, 18, 0, tzinfo=timezone.utc)


def inventory_checksum(inventory: Inventory) -> str:
    payload = inventory.model_dump_json(exclude_none=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def load_inventory_records(
    warehouse_id: str, product_data_path: Path
) -> tuple[dict[str, InventoryRecord], dict[str, list[InventoryEvent]]]:
    raw_data = json.loads(product_data_path.read_text(encoding="utf-8"))
    raw_products = raw_data.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        raise ValueError("product data must contain a non-empty 'products' list")

    records: dict[str, InventoryRecord] = {}
    event_history: dict[str, list[InventoryEvent]] = {}

    for index, raw_product in enumerate(raw_products, start=1):
        product = Product.model_validate(raw_product)
        if product.sku in records:
            raise ValueError(f"duplicate SKU in product data: {product.sku}")

        on_hand = 90 + (index * 10)
        reserved = index
        inventory = Inventory(
            on_hand=on_hand,
            reserved=reserved,
            available=on_hand - reserved,
        )
        event = InventoryEvent(
            event_id=f"evt-initial-{product.sku}",
            type="stock_received",
            quantity_delta=on_hand,
            occurred_at=STARTED_AT,
            processed_at=STARTED_AT,
            reference="initial-catalogue-load",
        )
        cursor = index
        record = InventoryRecord(
            product=product,
            inventory=inventory,
            state=InventoryState(
                version=STARTING_VERSION,
                snapshot_id=(
                    f"snap-{warehouse_id}-{product.sku.replace('-', '')}-"
                    f"{STARTING_VERSION}"
                ),
                updated_at=STARTED_AT,
                updated_by="catalogue-loader",
                checksum=inventory_checksum(inventory),
            ),
            last_event=event,
            sync=SyncMetadata(
                status="up_to_date",
                last_successful_sync_at=STARTED_AT,
                last_synced_version=STARTING_VERSION,
                event_cursor=cursor,
                sync_lag_seconds=0,
            ),
            data_quality=DataQuality(
                status="valid",
                warnings=[],
                last_validated_at=STARTED_AT,
            ),
        )
        records[product.sku] = record
        event_history[product.sku] = [event]

    return records, event_history

