from pathlib import Path

import httpx

from agent.models import WarehouseEndpoint
from warehouse.app import create_app


ROOT = Path(__file__).parents[1]
WAREHOUSE_IDS = ("warehouse-a", "warehouse-b", "warehouse-c")


def simulation_endpoints() -> dict[str, WarehouseEndpoint]:
    return {
        warehouse_id: WarehouseEndpoint(
            warehouse_id=warehouse_id,
            base_url=f"https://{warehouse_id}.test",
        )
        for warehouse_id in WAREHOUSE_IDS
    }


class MultiWarehouseTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.apps = {
            warehouse_id: create_app(
                warehouse_id=warehouse_id,
                simulation_mode=True,
            )
            for warehouse_id in WAREHOUSE_IDS
        }
        self.transports = {
            warehouse_id: httpx.ASGITransport(app=app)
            for warehouse_id, app in self.apps.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        warehouse_id = request.url.host.removesuffix(".test")
        return await self.transports[warehouse_id].handle_async_request(request)

    async def aclose(self) -> None:
        for transport in self.transports.values():
            await transport.aclose()
