"""HTTP-only control surface used by the live simulator, never by V3."""

import asyncio
import time
from datetime import datetime, timezone

import httpx

from agent.detector import build_product_observations, detect_conflicts
from agent.models import WarehouseEndpoint, WarehouseObservation
from simulation.models import EnvironmentObservation, SimulationControlCost
from warehouse.models import (
    CatalogueResponse,
    InventoryEvent,
    SimulationCorruptionRequest,
    SimulationEventRequest,
    SimulationHistoryRequest,
    SimulationMutationResponse,
)


class SimulationControlError(RuntimeError):
    pass


class SimulationWarehouseClient:
    """Separate uninstrumented-by-V3 client with its own transparent costs."""

    def __init__(
        self,
        endpoints: dict[str, WarehouseEndpoint],
        http_client: httpx.AsyncClient,
    ) -> None:
        self.endpoints = endpoints
        self.http_client = http_client
        self.cost = SimulationControlCost()

    async def reset_all(self) -> None:
        await asyncio.gather(
            *(
                self._post(warehouse_id, "/simulation/reset", None, "reset")
                for warehouse_id in sorted(self.endpoints)
            )
        )

    async def apply_event(
        self,
        warehouse_id: str,
        sku: str,
        request: SimulationEventRequest,
    ) -> SimulationMutationResponse:
        return await self._post(
            warehouse_id,
            f"/simulation/inventory/{sku}/event",
            request.model_dump(mode="json"),
            "mutation",
        )

    async def corrupt(
        self,
        warehouse_id: str,
        sku: str,
        request: SimulationCorruptionRequest,
    ) -> SimulationMutationResponse:
        return await self._post(
            warehouse_id,
            f"/simulation/inventory/{sku}/corrupt",
            request.model_dump(mode="json"),
            "mutation",
        )

    async def replace_history(
        self,
        warehouse_id: str,
        sku: str,
        *,
        expected_last_event_id: str,
        events: list[InventoryEvent],
    ) -> SimulationMutationResponse:
        request = SimulationHistoryRequest(
            expected_last_event_id=expected_last_event_id,
            events=events,
        )
        return await self._post(
            warehouse_id,
            f"/simulation/inventory/{sku}/history",
            request.model_dump(mode="json"),
            "mutation",
        )

    async def observe_environment(
        self,
    ) -> tuple[EnvironmentObservation, dict[str, WarehouseObservation]]:
        results = await asyncio.gather(
            *(self._get_catalogue(warehouse_id) for warehouse_id in self.endpoints)
        )
        observations = {item.warehouse_id: item for item in results}
        products = build_product_observations(observations)
        consistent, conflicts = detect_conflicts(products, self.endpoints)
        return (
            EnvironmentObservation(
                products=len(products),
                consistent=len(consistent),
                conflicts=len(conflicts),
                conflicting_skus=sorted(conflicts),
            ),
            observations,
        )

    async def _get_catalogue(self, warehouse_id: str) -> WarehouseObservation:
        endpoint = self.endpoints[warehouse_id]
        response = await self._request(
            warehouse_id,
            "GET",
            "/inventory",
            None,
            "observation",
        )
        catalogue = CatalogueResponse.model_validate_json(response.content)
        if catalogue.system.id != warehouse_id:
            raise SimulationControlError(
                f"{warehouse_id} returned identity {catalogue.system.id}"
            )
        return WarehouseObservation(
            warehouse_id=warehouse_id,
            observed_at=datetime.now(timezone.utc),
            health_status=catalogue.system.health.status,
            writable=catalogue.capabilities.writable,
            items={item.product.sku: item for item in catalogue.items},
        )

    async def _post(
        self,
        warehouse_id: str,
        path: str,
        payload: dict | None,
        purpose: str,
    ) -> SimulationMutationResponse:
        response = await self._request(
            warehouse_id, "POST", path, payload, purpose
        )
        result = SimulationMutationResponse.model_validate_json(response.content)
        if result.system_id != warehouse_id:
            raise SimulationControlError(
                f"{warehouse_id} returned identity {result.system_id}"
            )
        return result

    async def _request(
        self,
        warehouse_id: str,
        method: str,
        path: str,
        payload: dict | None,
        purpose: str,
    ) -> httpx.Response:
        endpoint = self.endpoints[warehouse_id]
        started = time.perf_counter()
        request_bytes = 0
        response_bytes = 0
        success = False
        try:
            if payload is None:
                response = await self.http_client.request(
                    method, f"{endpoint.base_url}{path}"
                )
            else:
                # httpx owns the final JSON encoding; measure its prepared body.
                request = self.http_client.build_request(
                    method, f"{endpoint.base_url}{path}", json=payload
                )
                request_bytes = len(request.content)
                response = await self.http_client.send(request)
            response_bytes = len(response.content)
            response.raise_for_status()
            success = True
            return response
        except (httpx.HTTPError, ValueError) as exc:
            raise SimulationControlError(
                f"{warehouse_id} {method} {path} failed: {exc}"
            ) from exc
        finally:
            self.cost.api_calls += 1
            self.cost.successful_calls += int(success)
            self.cost.failed_calls += int(not success)
            self.cost.reset_calls += int(purpose == "reset")
            self.cost.mutation_calls += int(purpose == "mutation")
            self.cost.observation_calls += int(purpose == "observation")
            self.cost.request_bytes += request_bytes
            self.cost.response_bytes += response_bytes
            self.cost.api_latency_ms += (time.perf_counter() - started) * 1000
