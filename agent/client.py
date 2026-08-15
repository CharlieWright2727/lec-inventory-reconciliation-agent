"""Instrumented asynchronous HTTP client for read-only warehouse calls."""

import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from pydantic import ValidationError

from agent.metrics import ApiCallMetric, MetricRecorder
from agent.models import WarehouseEndpoint
from warehouse.models import CatalogueResponse, EventHistoryResponse


class WarehouseClientError(RuntimeError):
    def __init__(self, warehouse_id: str, error_type: str, detail: str) -> None:
        self.warehouse_id = warehouse_id
        self.error_type = error_type
        self.detail = detail
        super().__init__(f"{warehouse_id}: {error_type}: {detail}")


class WarehouseClient:
    """Controlled read-only interface used by observation and reasoning."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        metrics: MetricRecorder,
    ) -> None:
        self._http_client = http_client
        self._metrics = metrics

    async def get_catalogue(
        self,
        endpoint: WarehouseEndpoint,
        *,
        run_id: str,
    ) -> CatalogueResponse:
        path = "/inventory"
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        status_code: int | None = None
        response_bytes = 0
        error_type: str | None = None

        try:
            response = await self._http_client.get(f"{endpoint.base_url}{path}")
            status_code = response.status_code
            response_bytes = len(response.content)
            response.raise_for_status()
            catalogue = CatalogueResponse.model_validate_json(response.content)
        except httpx.HTTPStatusError as exc:
            error_type = "http_error"
            detail = f"HTTP {exc.response.status_code}"
            raise WarehouseClientError(
                endpoint.warehouse_id, error_type, detail
            ) from exc
        except httpx.TimeoutException as exc:
            error_type = "timeout"
            raise WarehouseClientError(
                endpoint.warehouse_id, error_type, str(exc) or "request timed out"
            ) from exc
        except httpx.RequestError as exc:
            error_type = "connection_error"
            raise WarehouseClientError(
                endpoint.warehouse_id, error_type, str(exc) or "request failed"
            ) from exc
        except ValidationError as exc:
            error_type = "validation_error"
            raise WarehouseClientError(
                endpoint.warehouse_id, error_type, "invalid catalogue response"
            ) from exc
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            self._metrics.record(
                ApiCallMetric(
                    request_id=f"request-{uuid4()}",
                    run_id=run_id,
                    warehouse_id=endpoint.warehouse_id,
                    method="GET",
                    endpoint=path,
                    purpose="catalogue_observation",
                    started_at=started_at,
                    latency_ms=latency_ms,
                    status_code=status_code,
                    success=error_type is None,
                    request_bytes=0,
                    response_bytes=response_bytes,
                    error_type=error_type,
                )
            )

        return catalogue

    async def get_events(
        self,
        endpoint: WarehouseEndpoint,
        sku: str,
        *,
        run_id: str,
        limit: int = 10,
    ) -> EventHistoryResponse:
        path = f"/inventory/{sku}/events"
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        status_code: int | None = None
        response_bytes = 0
        error_type: str | None = None

        try:
            response = await self._http_client.get(
                f"{endpoint.base_url}{path}", params={"limit": limit}
            )
            status_code = response.status_code
            response_bytes = len(response.content)
            response.raise_for_status()
            history = EventHistoryResponse.model_validate_json(response.content)
            if history.system_id != endpoint.warehouse_id or history.sku != sku:
                raise ValueError("event history identity does not match request")
        except httpx.HTTPStatusError as exc:
            error_type = "http_error"
            raise WarehouseClientError(
                endpoint.warehouse_id,
                error_type,
                f"HTTP {exc.response.status_code}",
            ) from exc
        except httpx.TimeoutException as exc:
            error_type = "timeout"
            raise WarehouseClientError(
                endpoint.warehouse_id, error_type, str(exc) or "request timed out"
            ) from exc
        except httpx.RequestError as exc:
            error_type = "connection_error"
            raise WarehouseClientError(
                endpoint.warehouse_id, error_type, str(exc) or "request failed"
            ) from exc
        except (ValidationError, ValueError) as exc:
            error_type = "validation_error"
            raise WarehouseClientError(
                endpoint.warehouse_id, error_type, "invalid event-history response"
            ) from exc
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            self._metrics.record(
                ApiCallMetric(
                    request_id=f"request-{uuid4()}",
                    run_id=run_id,
                    warehouse_id=endpoint.warehouse_id,
                    method="GET",
                    endpoint=path,
                    purpose="event_investigation",
                    started_at=started_at,
                    latency_ms=latency_ms,
                    status_code=status_code,
                    success=error_type is None,
                    request_bytes=0,
                    response_bytes=response_bytes,
                    error_type=error_type,
                )
            )

        return history
