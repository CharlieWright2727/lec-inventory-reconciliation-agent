import json
from pathlib import Path

import httpx

from agent.models import WarehouseEndpoint
from warehouse.models import InventoryUpdateRequest
from warehouse.store import (
    SameVersionInventoryConflictError,
    TargetVersionError,
    VersionConflictError,
    WarehouseNotWritableError,
    WarehouseStore,
)


ROOT = Path(__file__).parents[1]
WAREHOUSE_IDS = ("warehouse-a", "warehouse-b", "warehouse-c")


def endpoints() -> dict[str, WarehouseEndpoint]:
    return {
        warehouse_id: WarehouseEndpoint(
            warehouse_id=warehouse_id,
            base_url=f"https://{warehouse_id}.test",
        )
        for warehouse_id in WAREHOUSE_IDS
    }


class V3Harness:
    def __init__(
        self,
        scenario: str,
        *,
        non_writable: set[str] | None = None,
    ) -> None:
        self.stores = {
            warehouse_id: WarehouseStore(
                warehouse_id,
                ROOT / "warehouse/data/products.json",
                scenario_data_path=(
                    ROOT / "scenarios" / scenario / f"{warehouse_id}.json"
                ),
                writable=warehouse_id not in (non_writable or set()),
            )
            for warehouse_id in WAREHOUSE_IDS
        }
        self.requests: list[tuple[str, str, str, int]] = []
        self.forced_put_status: dict[str, int] = {}
        self.forced_event_status: dict[str, int] = {}
        self.before_put = None
        self.verification_overrides: dict[str, object] = {}
        self._put_count = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        warehouse_id = request.url.host.removesuffix(".test")
        self.requests.append(
            (warehouse_id, request.method, request.url.path, len(request.content))
        )
        store = self.stores[warehouse_id]
        path = request.url.path
        if request.method == "GET" and path == "/inventory":
            return httpx.Response(
                200, json=store.catalogue().model_dump(mode="json")
            )
        if request.method == "GET" and path.endswith("/events"):
            forced = self.forced_event_status.get(warehouse_id)
            if forced is not None:
                return httpx.Response(forced, json={"detail": "forced failure"})
            sku = path.split("/")[2]
            return httpx.Response(
                200,
                json=store.events(
                    sku, int(request.url.params.get("limit", "10"))
                ).model_dump(mode="json"),
            )
        if request.method == "GET":
            sku = path.split("/")[2]
            response = self.verification_overrides.get(
                warehouse_id, store.inventory(sku)
            )
            return httpx.Response(200, json=response.model_dump(mode="json"))
        if request.method == "PUT":
            self._put_count += 1
            if self.before_put is not None:
                self.before_put(warehouse_id, self._put_count, store)
            forced = self.forced_put_status.get(warehouse_id)
            if forced is not None:
                return httpx.Response(forced, json={"detail": "forced failure"})
            sku = path.split("/")[2]
            update = InventoryUpdateRequest.model_validate(
                json.loads(request.content)
            )
            try:
                result = store.update(sku, update)
            except WarehouseNotWritableError:
                return httpx.Response(403, json={"detail": "not writable"})
            except (VersionConflictError, TargetVersionError, SameVersionInventoryConflictError):
                return httpx.Response(409, json={"detail": "version conflict"})
            return httpx.Response(200, json=result.model_dump(mode="json"))
        raise AssertionError(f"unexpected request: {request.method} {path}")

    def calls(self, *, method: str | None = None, suffix: str | None = None):
        return [
            call
            for call in self.requests
            if (method is None or call[1] == method)
            and (suffix is None or call[2].endswith(suffix))
        ]
