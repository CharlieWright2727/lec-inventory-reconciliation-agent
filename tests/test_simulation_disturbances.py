import asyncio

import httpx
import pytest

from simulation.client import SimulationWarehouseClient
from simulation.disturbances import DISTURBANCE_SKUS, inject_disturbance
from simulation.models import DisturbanceType
from tests.simulation_support import MultiWarehouseTransport, simulation_endpoints


REQUIRED_DISTURBANCES = list(DisturbanceType)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("disturbance_type", REQUIRED_DISTURBANCES)
def test_each_disturbance_changes_only_its_intended_sku(disturbance_type) -> None:
    async def exercise():
        transport = MultiWarehouseTransport()
        async with httpx.AsyncClient(transport=transport) as http_client:
            control = SimulationWarehouseClient(
                simulation_endpoints(), http_client
            )
            await control.reset_all()
            clean, before = await control.observe_environment()
            metadata = await inject_disturbance(
                disturbance_type, control, before
            )
            disturbed, after = await control.observe_environment()
            return transport, clean, before, metadata, disturbed, after

    transport, clean, before, metadata, disturbed, after = run(exercise())
    sku = DISTURBANCE_SKUS[disturbance_type]

    assert clean.clean is True
    assert metadata.sku == sku
    assert disturbed.conflicting_skus == [sku]
    for warehouse_id in before:
        for candidate_sku, before_record in before[warehouse_id].items.items():
            if candidate_sku != sku:
                assert after[warehouse_id].items[candidate_sku] == before_record

    for warehouse_id in metadata.affected_warehouses:
        record = after[warehouse_id].items[sku]
        assert record.state.updated_by != "reconciliation-agent"

    if disturbance_type == DisturbanceType.MATERIALISED_CORRUPTION:
        before_record = before["warehouse-c"].items[sku]
        after_record = after["warehouse-c"].items[sku]
        assert after_record.state.version == before_record.state.version
        assert after_record.sync.event_cursor == before_record.sync.event_cursor
        assert after_record.last_event == before_record.last_event
        assert transport.apps["warehouse-c"].state.store.events(sku, 100).events == [
            before_record.last_event
        ]


@pytest.mark.parametrize("disturbance_type", REQUIRED_DISTURBANCES)
def test_reset_repairs_every_disturbance(disturbance_type) -> None:
    async def exercise():
        transport = MultiWarehouseTransport()
        async with httpx.AsyncClient(transport=transport) as http_client:
            control = SimulationWarehouseClient(
                simulation_endpoints(), http_client
            )
            await control.reset_all()
            clean, observations = await control.observe_environment()
            assert clean.clean
            await inject_disturbance(disturbance_type, control, observations)
            await control.reset_all()
            reset_view, reset_observations = await control.observe_environment()
            return transport, reset_view, reset_observations

    transport, reset_view, reset_observations = run(exercise())

    assert reset_view.clean is True
    assert all(len(item.items) == 10 for item in reset_observations.values())
    for warehouse_id, app in transport.apps.items():
        store = app.state.store
        for sku, record in reset_observations[warehouse_id].items.items():
            assert record.state.version == 41
            assert len(store.events(sku, 100).events) == 1
