"""FastAPI application shared by all simulated warehouse services."""

import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from warehouse.models import (
    CatalogueResponse,
    EventHistoryResponse,
    HealthResponse,
    InventoryResponse,
    InventoryUpdateRequest,
    InventoryUpdateResponse,
    SimulationCorruptionRequest,
    SimulationEventRequest,
    SimulationHistoryRequest,
    SimulationMutationResponse,
)
from warehouse.store import (
    SameVersionInventoryConflictError,
    TargetVersionError,
    UnknownSkuError,
    VersionConflictError,
    WarehouseNotWritableError,
    WarehouseStore,
)


DEFAULT_PRODUCT_DATA_PATH = Path(__file__).parent / "data" / "products.json"


def create_app(
    *,
    warehouse_id: str | None = None,
    product_data_path: Path | None = None,
    scenario_data_path: Path | None = None,
    writable: bool = True,
    simulation_mode: bool | None = None,
) -> FastAPI:
    resolved_id = warehouse_id or os.getenv("WAREHOUSE_ID", "warehouse-local")
    configured_path = os.getenv("PRODUCT_DATA_PATH")
    resolved_path = product_data_path or (
        Path(configured_path) if configured_path else DEFAULT_PRODUCT_DATA_PATH
    )
    configured_scenario_path = os.getenv("SCENARIO_DATA_PATH")
    resolved_scenario_path = scenario_data_path or (
        Path(configured_scenario_path) if configured_scenario_path else None
    )
    resolved_simulation_mode = (
        simulation_mode
        if simulation_mode is not None
        else os.getenv("SIMULATION_MODE", "").strip().lower() == "true"
    )

    app = FastAPI(title=f"Inventory API: {resolved_id}")
    app.state.store = WarehouseStore(
        warehouse_id=resolved_id,
        product_data_path=resolved_path,
        scenario_data_path=resolved_scenario_path,
        writable=writable,
    )

    @app.get("/health", response_model=HealthResponse)
    def get_health() -> HealthResponse:
        return HealthResponse(system_id=resolved_id, status="healthy")

    @app.get("/inventory", response_model=CatalogueResponse)
    def get_catalogue() -> CatalogueResponse:
        return app.state.store.catalogue()

    @app.get("/inventory/{sku}", response_model=InventoryResponse)
    def get_inventory(sku: str) -> InventoryResponse | JSONResponse:
        try:
            return app.state.store.inventory(sku)
        except UnknownSkuError:
            return _not_found(sku)

    @app.get("/inventory/{sku}/events", response_model=EventHistoryResponse)
    def get_events(
        sku: str, limit: int = Query(default=10, ge=1, le=100)
    ) -> EventHistoryResponse | JSONResponse:
        try:
            return app.state.store.events(sku, limit)
        except UnknownSkuError:
            return _not_found(sku)

    @app.put("/inventory/{sku}", response_model=InventoryUpdateResponse)
    def update_inventory(
        sku: str, request: InventoryUpdateRequest
    ) -> InventoryUpdateResponse | JSONResponse:
        try:
            return app.state.store.update(sku, request)
        except UnknownSkuError:
            return _not_found(sku)
        except WarehouseNotWritableError:
            return JSONResponse(
                status_code=403,
                content={
                    "status": "rejected",
                    "message": "Warehouse is not writable.",
                },
            )
        except VersionConflictError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "conflict",
                    "message": (
                        "Inventory changed after it was read by the "
                        "reconciliation agent."
                    ),
                    "expected_current_version": exc.expected,
                    "current_version": exc.current,
                },
            )
        except TargetVersionError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "conflict",
                    "message": "Target version is older than current state.",
                    "target_version": exc.target,
                    "current_version": exc.current,
                },
            )
        except SameVersionInventoryConflictError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "conflict",
                    "message": (
                        "The same logical version cannot represent a different "
                        "inventory state."
                    ),
                    "target_version": exc.version,
                    "current_version": exc.version,
                },
            )

    if resolved_simulation_mode:

        @app.post("/simulation/reset", response_model=SimulationMutationResponse)
        def simulation_reset() -> SimulationMutationResponse:
            return app.state.store.simulation_reset()

        @app.post(
            "/simulation/inventory/{sku}/event",
            response_model=SimulationMutationResponse,
        )
        def simulation_event(
            sku: str, request: SimulationEventRequest
        ) -> SimulationMutationResponse | JSONResponse:
            try:
                return app.state.store.simulation_apply_event(sku, request)
            except UnknownSkuError:
                return _not_found(sku)
            except VersionConflictError as exc:
                return _simulation_conflict(str(exc))
            except (TargetVersionError, ValueError) as exc:
                return _simulation_conflict(str(exc))

        @app.post(
            "/simulation/inventory/{sku}/corrupt",
            response_model=SimulationMutationResponse,
        )
        def simulation_corrupt(
            sku: str, request: SimulationCorruptionRequest
        ) -> SimulationMutationResponse | JSONResponse:
            try:
                return app.state.store.simulation_corrupt(sku, request)
            except UnknownSkuError:
                return _not_found(sku)
            except (VersionConflictError, ValueError) as exc:
                return _simulation_conflict(str(exc))

        @app.post(
            "/simulation/inventory/{sku}/history",
            response_model=SimulationMutationResponse,
        )
        def simulation_history(
            sku: str, request: SimulationHistoryRequest
        ) -> SimulationMutationResponse | JSONResponse:
            try:
                return app.state.store.simulation_replace_history(sku, request)
            except UnknownSkuError:
                return _not_found(sku)
            except ValueError as exc:
                return _simulation_conflict(str(exc))

    return app


def _not_found(sku: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"detail": f"SKU not found: {sku}"},
    )


def _simulation_conflict(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"status": "conflict", "message": message},
    )


app = create_app()
