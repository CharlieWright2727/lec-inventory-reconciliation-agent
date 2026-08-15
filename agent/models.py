"""Structured state models for the read-only V1 agent."""

from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from agent.metrics import RunMetrics
from warehouse.models import InventoryRecord


class WarehouseEndpoint(BaseModel):
    warehouse_id: str = Field(min_length=1)
    base_url: str = Field(min_length=1)

    @field_validator("warehouse_id", "base_url")
    @classmethod
    def strip_non_empty_values(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        if stripped.startswith(("http://", "https://")):
            return stripped.rstrip("/")
        return stripped

    @field_validator("base_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must use http:// or https://")
        return value


class WarehouseObservation(BaseModel):
    warehouse_id: str = Field(min_length=1)
    observed_at: datetime
    health_status: str = Field(min_length=1)
    writable: bool
    items: dict[str, InventoryRecord]

    @model_validator(mode="after")
    def validate_item_keys(self) -> "WarehouseObservation":
        for sku, record in self.items.items():
            if sku != record.product.sku:
                raise ValueError("item key must match the record SKU")
        return self


class ProductObservation(BaseModel):
    sku: str = Field(min_length=1)
    records: dict[str, InventoryRecord]

    @model_validator(mode="after")
    def validate_record_skus(self) -> "ProductObservation":
        if any(record.product.sku != self.sku for record in self.records.values()):
            raise ValueError("all records must belong to the observed SKU")
        return self


class ConflictType(str, Enum):
    INVENTORY_MISMATCH = "inventory_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    EVENT_PROGRESS_MISMATCH = "event_progress_mismatch"
    PRODUCT_IDENTITY_MISMATCH = "product_identity_mismatch"
    MISSING_SKU = "missing_sku"


class ProductConflict(BaseModel):
    conflict_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    conflict_types: list[ConflictType] = Field(min_length=1)
    records: dict[str, InventoryRecord]
    detected_at: datetime


class RunStatus(str, Enum):
    STARTING = "starting"
    OBSERVING = "observing"
    ANALYSING = "analysing"
    COMPLETED = "completed"
    FAILED = "failed"


class RunState(BaseModel):
    run_id: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime | None = None
    status: RunStatus
    warehouses: dict[str, WarehouseEndpoint]
    observations: dict[str, WarehouseObservation] = Field(default_factory=dict)
    products: dict[str, ProductObservation] = Field(default_factory=dict)
    consistent_skus: list[str] = Field(default_factory=list)
    conflicts: dict[str, ProductConflict] = Field(default_factory=dict)
    observation_errors: dict[str, str] = Field(default_factory=dict)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
