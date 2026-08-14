"""Typed request and response models for the warehouse API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Product(BaseModel):
    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    barcode: str = Field(min_length=1)


class Inventory(BaseModel):
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)
    available: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_quantities(self) -> "Inventory":
        if self.reserved > self.on_hand:
            raise ValueError("reserved must not exceed on_hand")
        if self.available != self.on_hand - self.reserved:
            raise ValueError("available must equal on_hand - reserved")
        return self


class InventoryState(BaseModel):
    version: int = Field(ge=0)
    snapshot_id: str = Field(min_length=1)
    updated_at: datetime
    updated_by: str = Field(min_length=1)
    checksum: str = Field(min_length=1)


class InventoryEvent(BaseModel):
    event_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    quantity_delta: int
    occurred_at: datetime
    processed_at: datetime
    reference: str = Field(min_length=1)


class SyncMetadata(BaseModel):
    status: Literal["up_to_date", "behind", "degraded", "unknown"]
    last_successful_sync_at: datetime
    last_synced_version: int = Field(ge=0)
    event_cursor: int = Field(ge=0)
    sync_lag_seconds: int = Field(ge=0)


class DataQuality(BaseModel):
    status: Literal["valid", "warning", "invalid"]
    warnings: list[str] = Field(default_factory=list)
    last_validated_at: datetime


class InventoryRecord(BaseModel):
    product: Product
    inventory: Inventory
    state: InventoryState
    last_event: InventoryEvent
    sync: SyncMetadata
    data_quality: DataQuality


class SystemHealth(BaseModel):
    status: Literal["healthy", "degraded", "unavailable"]
    last_heartbeat_at: datetime
    error_rate_5m: float = Field(ge=0.0)


class SystemInfo(BaseModel):
    id: str = Field(min_length=1)
    health: SystemHealth


class Capabilities(BaseModel):
    writable: bool
    supports_version_check: bool


class CatalogueResponse(BaseModel):
    system: SystemInfo
    items: list[InventoryRecord]
    capabilities: Capabilities


class InventoryResponse(BaseModel):
    system: SystemInfo
    product: Product
    inventory: Inventory
    state: InventoryState
    last_event: InventoryEvent
    sync: SyncMetadata
    data_quality: DataQuality
    capabilities: Capabilities


class EventHistoryResponse(BaseModel):
    system_id: str
    sku: str
    events: list[InventoryEvent]


class UpdateSource(BaseModel):
    system_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)


class InventoryUpdateRequest(BaseModel):
    expected_current_version: int = Field(ge=0)
    target_version: int = Field(ge=0)
    inventory: Inventory
    source: UpdateSource
    reason: str = Field(min_length=1)


class InventoryUpdateResponse(BaseModel):
    status: Literal["updated", "unchanged"]
    system_id: str
    sku: str
    previous_version: int
    new_version: int


class HealthResponse(BaseModel):
    system_id: str
    status: Literal["healthy", "degraded", "unavailable"]
