"""Concurrent warehouse catalogue observation."""

import asyncio
from datetime import datetime, timezone

from agent.client import WarehouseClient
from agent.models import WarehouseEndpoint, WarehouseObservation


class ObservationError(RuntimeError):
    def __init__(self, failures: dict[str, str]) -> None:
        self.failures = failures
        super().__init__("one or more warehouses could not be observed")


async def observe_warehouses(
    endpoints: dict[str, WarehouseEndpoint],
    client: WarehouseClient,
    *,
    run_id: str,
) -> dict[str, WarehouseObservation]:
    results = await asyncio.gather(
        *(
            _observe_warehouse(endpoint, client, run_id=run_id)
            for endpoint in endpoints.values()
        ),
        return_exceptions=True,
    )

    observations: dict[str, WarehouseObservation] = {}
    failures: dict[str, str] = {}
    for endpoint, result in zip(endpoints.values(), results):
        if isinstance(result, BaseException):
            failures[endpoint.warehouse_id] = str(result)
        else:
            observations[endpoint.warehouse_id] = result

    if failures:
        raise ObservationError(failures)
    return observations


async def _observe_warehouse(
    endpoint: WarehouseEndpoint,
    client: WarehouseClient,
    *,
    run_id: str,
) -> WarehouseObservation:
    catalogue = await client.get_catalogue(endpoint, run_id=run_id)
    if catalogue.system.id != endpoint.warehouse_id:
        raise ValueError(
            f"expected system ID {endpoint.warehouse_id}, "
            f"received {catalogue.system.id}"
        )

    items = {record.product.sku: record for record in catalogue.items}
    if len(items) != len(catalogue.items):
        raise ValueError("catalogue contains duplicate SKUs")

    return WarehouseObservation(
        warehouse_id=endpoint.warehouse_id,
        observed_at=datetime.now(timezone.utc),
        health_status=catalogue.system.health.status,
        writable=catalogue.capabilities.writable,
        items=items,
    )
