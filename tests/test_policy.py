from pathlib import Path

from agent.detector import build_product_observations, detect_conflicts
from agent.evidence import extract_evidence
from agent.models import (
    ConflictType,
    DecisionOutcome,
    ProductConflict,
    WarehouseObservation,
)
from agent.policy import decide
from warehouse.models import EventHistoryResponse
from warehouse.store import WarehouseStore


ROOT = Path(__file__).parents[1]
WAREHOUSES = ("node-1", "node-2", "node-3")


def scenario_evidence(
    scenario: str, *, history_warehouses: tuple[str, ...] = ()
):
    records = {}
    histories = {}
    for index, warehouse_id in enumerate(WAREHOUSES):
        source_id = f"warehouse-{chr(ord('a') + index)}"
        store = WarehouseStore(
            source_id,
            ROOT / "warehouse/data/products.json",
            scenario_data_path=ROOT / "scenarios" / scenario / f"{source_id}.json",
        )
        response = store.inventory("SKU-001")
        from warehouse.models import InventoryRecord

        records[warehouse_id] = InventoryRecord.model_validate(
            response.model_dump(exclude={"system", "capabilities"})
        )
        if warehouse_id in history_warehouses:
            history = store.events("SKU-001", 10)
            histories[warehouse_id] = EventHistoryResponse(
                system_id=warehouse_id,
                sku=history.sku,
                events=history.events,
            )
    conflict_types = [ConflictType.INVENTORY_MISMATCH]
    if len({item.state.version for item in records.values()}) > 1:
        conflict_types.extend(
            [ConflictType.VERSION_MISMATCH, ConflictType.EVENT_PROGRESS_MISMATCH]
        )
    conflict = ProductConflict(
        conflict_id="generic-conflict",
        sku="SKU-001",
        conflict_types=conflict_types,
        records=records,
        detected_at=next(iter(records.values())).state.updated_at,
    )
    return extract_evidence(conflict, histories)


def test_policy_reconciles_a_strictly_stale_replica_without_investigation() -> None:
    decision = decide(scenario_evidence("one-stale-warehouse"))

    assert decision.outcome == DecisionOutcome.RECONCILE
    assert decision.target_warehouses == ["node-2"]
    assert decision.requires_investigation is False


def test_policy_protects_a_newer_minority_then_reconciles_forward() -> None:
    initial = decide(scenario_evidence("newer-singleton"))
    final = decide(
        scenario_evidence("newer-singleton", history_warehouses=("node-3",))
    )

    assert initial.outcome == DecisionOutcome.INVESTIGATE
    assert initial.investigation_warehouses == ["node-3"]
    assert final.outcome == DecisionOutcome.RECONCILE
    assert final.canonical_source == "node-3"
    assert final.target_warehouses == ["node-1", "node-2"]


def test_policy_uses_shared_history_to_resolve_same_progress_divergence() -> None:
    initial = decide(scenario_evidence("same-version-divergence"))
    final = decide(
        scenario_evidence(
            "same-version-divergence", history_warehouses=WAREHOUSES
        )
    )

    assert initial.outcome == DecisionOutcome.INVESTIGATE
    assert initial.investigation_warehouses == list(WAREHOUSES)
    assert final.outcome == DecisionOutcome.RECONCILE
    assert final.target_warehouses == ["node-3"]
    assert final.canonical_state.inventory.on_hand == 120


def test_policy_escalates_incompatible_product_identity() -> None:
    evidence = scenario_evidence("one-stale-warehouse")
    conflict = ProductConflict(
        conflict_id="identity-conflict",
        sku=evidence.sku,
        conflict_types=[ConflictType.PRODUCT_IDENTITY_MISMATCH],
        records=evidence.observed_records,
        detected_at=next(iter(evidence.observed_records.values())).state.updated_at,
    )
    conflict.records["node-2"].product.name = "A different product"

    decision = decide(extract_evidence(conflict))

    assert decision.outcome == DecisionOutcome.ESCALATE
    assert decision.target_warehouses == []
