"""Load deterministic inventory from the catalogue and optional scenario data."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class ScenarioState(BaseModel):
    """State fields supplied by a scenario; derived fields are added on load."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    updated_at: datetime
    updated_by: str = Field(min_length=1)


class ScenarioRecord(BaseModel):
    """A complete scenario replacement for one catalogue SKU."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    inventory: Inventory
    state: ScenarioState
    last_event: InventoryEvent
    sync: SyncMetadata
    data_quality: DataQuality
    events: list[InventoryEvent] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_event_and_sync_evidence(self) -> "ScenarioRecord":
        if self.events[-1] != self.last_event:
            raise ValueError("last_event must be the final event in events")
        if self.sync.last_synced_version != self.state.version:
            raise ValueError("last_synced_version must equal state.version")
        if any(
            earlier.processed_at > later.processed_at
            for earlier, later in zip(self.events, self.events[1:])
        ):
            raise ValueError("events must be ordered by processed_at")
        return self


class ScenarioData(BaseModel):
    """Validated scenario overlay loaded on top of the default catalogue."""

    model_config = ConfigDict(extra="forbid")

    records: list[ScenarioRecord] = Field(default_factory=list)
    missing_skus: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_overlay_operations(self) -> "ScenarioData":
        record_skus = [record.sku for record in self.records]
        if not record_skus and not self.missing_skus:
            raise ValueError("scenario must replace or remove at least one SKU")
        if len(record_skus) != len(set(record_skus)):
            raise ValueError("scenario records must not contain duplicate SKUs")
        if len(self.missing_skus) != len(set(self.missing_skus)):
            raise ValueError("missing_skus must not contain duplicates")
        if set(record_skus) & set(self.missing_skus):
            raise ValueError("a scenario cannot replace and remove the same SKU")
        return self


def inventory_checksum(inventory: Inventory) -> str:
    payload = inventory.model_dump_json(exclude_none=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def load_inventory_records(
    warehouse_id: str,
    product_data_path: Path,
    scenario_data_path: Path | None = None,
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

    if scenario_data_path is not None:
        _apply_scenario(
            warehouse_id,
            scenario_data_path,
            records,
            event_history,
        )

    return records, event_history


def _apply_scenario(
    warehouse_id: str,
    scenario_data_path: Path,
    records: dict[str, InventoryRecord],
    event_history: dict[str, list[InventoryEvent]],
) -> None:
    raw_data = json.loads(scenario_data_path.read_text(encoding="utf-8"))
    scenario = ScenarioData.model_validate(raw_data)
    seen_skus: set[str] = set()

    for scenario_record in scenario.records:
        sku = scenario_record.sku
        if sku in seen_skus:
            raise ValueError(f"duplicate SKU in scenario data: {sku}")
        if sku not in records:
            raise ValueError(f"scenario SKU is not in product catalogue: {sku}")
        seen_skus.add(sku)

        inventory = scenario_record.inventory.model_copy(deep=True)
        records[sku] = InventoryRecord(
            product=records[sku].product.model_copy(deep=True),
            inventory=inventory,
            state=InventoryState(
                version=scenario_record.state.version,
                snapshot_id=(
                    f"snap-{warehouse_id}-{sku.replace('-', '')}-"
                    f"{scenario_record.state.version}"
                ),
                updated_at=scenario_record.state.updated_at,
                updated_by=scenario_record.state.updated_by,
                checksum=inventory_checksum(inventory),
            ),
            last_event=scenario_record.last_event.model_copy(deep=True),
            sync=scenario_record.sync.model_copy(deep=True),
            data_quality=scenario_record.data_quality.model_copy(deep=True),
        )
        event_history[sku] = [
            event.model_copy(deep=True) for event in scenario_record.events
        ]

    for sku in scenario.missing_skus:
        if sku not in records:
            raise ValueError(f"missing scenario SKU is not in product catalogue: {sku}")
        del records[sku]
        del event_history[sku]
