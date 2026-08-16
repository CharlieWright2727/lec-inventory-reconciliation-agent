"""Independent in-memory inventory state for one warehouse process."""

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from warehouse.loader import inventory_checksum, load_inventory_records
from warehouse.models import (
    Capabilities,
    CatalogueResponse,
    DataQuality,
    EventHistoryResponse,
    InventoryEvent,
    InventoryRecord,
    InventoryResponse,
    InventoryState,
    InventoryUpdateRequest,
    InventoryUpdateResponse,
    SimulationCorruptionRequest,
    SimulationEventRequest,
    SimulationHistoryRequest,
    SimulationMutationResponse,
    SyncMetadata,
    SystemHealth,
    SystemInfo,
)


class UnknownSkuError(KeyError):
    pass


class WarehouseNotWritableError(RuntimeError):
    pass


class VersionConflictError(RuntimeError):
    def __init__(self, expected: int, current: int) -> None:
        self.expected = expected
        self.current = current
        super().__init__(f"expected version {expected}, current version {current}")


class TargetVersionError(RuntimeError):
    def __init__(self, target: int, current: int) -> None:
        self.target = target
        self.current = current
        super().__init__(f"target version {target} is older than current version {current}")


class SameVersionInventoryConflictError(RuntimeError):
    def __init__(self, version: int) -> None:
        self.version = version
        super().__init__(
            f"logical version {version} already represents different inventory"
        )


class WarehouseStore:
    def __init__(
        self,
        warehouse_id: str,
        product_data_path: Path,
        *,
        scenario_data_path: Path | None = None,
        writable: bool = True,
    ) -> None:
        self.warehouse_id = warehouse_id
        self.capabilities = Capabilities(
            writable=writable,
            supports_version_check=True,
        )
        self._product_data_path = product_data_path
        self._records, self._event_history = load_inventory_records(
            warehouse_id, product_data_path, scenario_data_path
        )
        self._lock = RLock()

    def _system(self) -> SystemInfo:
        return SystemInfo(
            id=self.warehouse_id,
            health=SystemHealth(
                status="healthy",
                last_heartbeat_at=datetime.now(timezone.utc),
                error_rate_5m=0.0,
            ),
        )

    def catalogue(self) -> CatalogueResponse:
        with self._lock:
            items = [
                self._records[sku].model_copy(deep=True)
                for sku in sorted(self._records)
            ]
        return CatalogueResponse(
            system=self._system(),
            items=items,
            capabilities=self.capabilities.model_copy(deep=True),
        )

    def inventory(self, sku: str) -> InventoryResponse:
        with self._lock:
            record = self._record(sku).model_copy(deep=True)
        return InventoryResponse(
            system=self._system(),
            capabilities=self.capabilities.model_copy(deep=True),
            **record.model_dump(),
        )

    def events(self, sku: str, limit: int) -> EventHistoryResponse:
        with self._lock:
            self._record(sku)
            events = [
                event.model_copy(deep=True)
                for event in self._event_history[sku][-limit:]
            ]
        return EventHistoryResponse(
            system_id=self.warehouse_id,
            sku=sku,
            events=list(reversed(events)),
        )

    def update(
        self, sku: str, request: InventoryUpdateRequest
    ) -> InventoryUpdateResponse:
        with self._lock:
            current = self._record(sku)
            if not self.capabilities.writable:
                raise WarehouseNotWritableError
            if request.expected_current_version != current.state.version:
                raise VersionConflictError(
                    request.expected_current_version, current.state.version
                )
            if request.target_version < current.state.version:
                raise TargetVersionError(request.target_version, current.state.version)
            if request.target_version == current.state.version:
                if request.inventory != current.inventory:
                    raise SameVersionInventoryConflictError(current.state.version)
                return InventoryUpdateResponse(
                    status="unchanged",
                    system_id=self.warehouse_id,
                    sku=sku,
                    previous_version=current.state.version,
                    new_version=current.state.version,
                )

            now = datetime.now(timezone.utc)
            previous_version = current.state.version
            event_cursor = current.sync.event_cursor + 1
            event = InventoryEvent(
                event_id=f"evt-reconcile-{sku}-{event_cursor}",
                type="stock_adjustment",
                quantity_delta=(
                    request.inventory.on_hand - current.inventory.on_hand
                ),
                occurred_at=now,
                processed_at=now,
                reference=request.reason,
            )
            updated = InventoryRecord(
                product=current.product.model_copy(deep=True),
                inventory=request.inventory.model_copy(deep=True),
                state=InventoryState(
                    version=request.target_version,
                    snapshot_id=(
                        f"snap-{self.warehouse_id}-{sku.replace('-', '')}-"
                        f"{request.target_version}-{event_cursor}"
                    ),
                    updated_at=now,
                    updated_by="reconciliation-agent",
                    checksum=inventory_checksum(request.inventory),
                ),
                last_event=event,
                sync=SyncMetadata(
                    status="up_to_date",
                    last_successful_sync_at=now,
                    last_synced_version=request.target_version,
                    event_cursor=event_cursor,
                    sync_lag_seconds=0,
                ),
                data_quality=DataQuality(
                    status="valid",
                    warnings=[],
                    last_validated_at=now,
                ),
            )

            self._records[sku] = updated
            self._event_history[sku].append(event)

        return InventoryUpdateResponse(
            status="updated",
            system_id=self.warehouse_id,
            sku=sku,
            previous_version=previous_version,
            new_version=request.target_version,
        )

    def simulation_reset(self) -> SimulationMutationResponse:
        """Restore the one canonical clean catalogue used by live simulation."""
        records, history = load_inventory_records(
            self.warehouse_id,
            self._product_data_path,
        )
        with self._lock:
            self._records = records
            self._event_history = history
        return SimulationMutationResponse(
            status="reset",
            system_id=self.warehouse_id,
        )

    def simulation_apply_event(
        self, sku: str, request: SimulationEventRequest
    ) -> SimulationMutationResponse:
        """Apply activity from an external inventory event processor."""
        with self._lock:
            current = self._record(sku)
            if request.expected_current_version != current.state.version:
                raise VersionConflictError(
                    request.expected_current_version, current.state.version
                )
            if request.target_version != current.state.version + 1:
                raise ValueError("external event must advance exactly one revision")
            if any(
                event.event_id == request.event.event_id
                for event in self._event_history[sku]
            ):
                raise ValueError("event_id already exists for SKU")
            if request.event.processed_at < current.last_event.processed_at:
                raise ValueError("external event predates current event progress")

            on_hand = current.inventory.on_hand + request.event.quantity_delta
            inventory = current.inventory.model_copy(
                update={
                    "on_hand": on_hand,
                    "available": on_hand - current.inventory.reserved,
                }
            )
            # Re-validate model_copy updates because Pydantic does not do so.
            inventory = type(current.inventory).model_validate(inventory.model_dump())
            event_cursor = current.sync.event_cursor + 1
            now = request.event.processed_at
            self._records[sku] = InventoryRecord(
                product=current.product.model_copy(deep=True),
                inventory=inventory,
                state=InventoryState(
                    version=request.target_version,
                    snapshot_id=(
                        f"snap-{self.warehouse_id}-{sku.replace('-', '')}-"
                        f"{request.target_version}-{event_cursor}"
                    ),
                    updated_at=now,
                    updated_by=request.actor,
                    checksum=inventory_checksum(inventory),
                ),
                last_event=request.event.model_copy(deep=True),
                sync=SyncMetadata(
                    status="up_to_date",
                    last_successful_sync_at=now,
                    last_synced_version=request.target_version,
                    event_cursor=event_cursor,
                    sync_lag_seconds=0,
                ),
                data_quality=DataQuality(
                    status="valid",
                    warnings=[],
                    last_validated_at=now,
                ),
            )
            self._event_history[sku].append(request.event.model_copy(deep=True))
        return SimulationMutationResponse(
            status="event_applied",
            system_id=self.warehouse_id,
            sku=sku,
        )

    def simulation_corrupt(
        self, sku: str, request: SimulationCorruptionRequest
    ) -> SimulationMutationResponse:
        """Corrupt only materialised inventory, retaining causal progress."""
        with self._lock:
            current = self._record(sku)
            if request.expected_current_version != current.state.version:
                raise VersionConflictError(
                    request.expected_current_version, current.state.version
                )
            if request.on_hand == current.inventory.on_hand:
                raise ValueError("corruption must change on_hand")
            inventory = type(current.inventory).model_validate(
                {
                    "on_hand": request.on_hand,
                    "reserved": current.inventory.reserved,
                    "available": request.on_hand - current.inventory.reserved,
                }
            )
            self._records[sku] = current.model_copy(
                deep=True,
                update={
                    "inventory": inventory,
                    "state": current.state.model_copy(
                        update={
                            "updated_by": request.actor,
                            "checksum": inventory_checksum(inventory),
                        }
                    ),
                },
            )
        return SimulationMutationResponse(
            status="corrupted",
            system_id=self.warehouse_id,
            sku=sku,
        )

    def simulation_replace_history(
        self, sku: str, request: SimulationHistoryRequest
    ) -> SimulationMutationResponse:
        """Replace only the history exposed for one observed inventory record."""
        with self._lock:
            current = self._record(sku)
            if request.expected_last_event_id != current.last_event.event_id:
                raise ValueError("expected_last_event_id does not match current state")
            if request.events[-1] != current.last_event:
                raise ValueError("replacement history must end at current last_event")
            self._event_history[sku] = [
                event.model_copy(deep=True) for event in request.events
            ]
        return SimulationMutationResponse(
            status="history_replaced",
            system_id=self.warehouse_id,
            sku=sku,
        )

    def _record(self, sku: str) -> InventoryRecord:
        try:
            return self._records[sku]
        except KeyError as exc:
            raise UnknownSkuError(sku) from exc
