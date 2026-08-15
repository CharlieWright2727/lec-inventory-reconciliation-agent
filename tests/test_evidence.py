from datetime import datetime, timezone
from pathlib import Path

from agent.evidence import (
    derive_on_hand_from_complete_history,
    derive_state_extension_from_anchor,
    extract_evidence,
)
from agent.models import ConflictType, EvidenceType, ProductConflict
from warehouse.models import EventHistoryResponse, Inventory, InventoryEvent
from warehouse.store import WarehouseStore


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)
PRODUCTS = Path(__file__).parents[1] / "warehouse/data/products.json"


def event(event_id: str, delta: int, reference: str) -> InventoryEvent:
    return InventoryEvent(
        event_id=event_id,
        type="stock_received" if delta >= 0 else "stock_adjustment",
        quantity_delta=delta,
        occurred_at=NOW,
        processed_at=NOW,
        reference=reference,
    )


def record(warehouse_id: str, on_hand: int, version: int, cursor: int, tip):
    item = WarehouseStore(warehouse_id, PRODUCTS).inventory("SKU-001")
    result = item.model_dump(exclude={"system", "capabilities"})
    from warehouse.models import InventoryRecord

    parsed = InventoryRecord.model_validate(result)
    parsed.inventory = Inventory(on_hand=on_hand, reserved=8, available=on_hand - 8)
    parsed.state.version = version
    parsed.sync.event_cursor = cursor
    parsed.last_event = tip
    return parsed


def conflict(records):
    return ProductConflict(
        conflict_id="conflict-generic",
        sku="SKU-001",
        conflict_types=[
            ConflictType.INVENTORY_MISMATCH,
            ConflictType.VERSION_MISMATCH,
            ConflictType.EVENT_PROGRESS_MISMATCH,
        ],
        records=records,
        detected_at=NOW,
    )


def test_extracts_agreement_and_strict_relative_progress() -> None:
    old = event("event-10", 100, "opening-stock")
    new = event("event-11", 20, "delivery")
    evidence = extract_evidence(
        conflict(
            {
                "north": record("north", 120, 11, 11, new),
                "south": record("south", 100, 10, 10, old),
                "west": record("west", 120, 11, 11, new),
            }
        )
    )

    types = {finding.type for finding in evidence.findings}
    assert EvidenceType.WAREHOUSES_AGREE in types
    assert EvidenceType.WAREHOUSE_BEHIND in types
    assert EvidenceType.EVENT_PROGRESS_BEHIND in types
    agreement = evidence.findings_of_type(EvidenceType.WAREHOUSES_AGREE)[0]
    assert agreement.warehouse_ids == ["north", "west"]


def test_event_helpers_require_an_anchor_and_explain_an_extension() -> None:
    opening = event("event-10", 100, "opening-stock-10")
    delivery = event("event-11", 20, "delivery")
    adjustment = event("event-12", -15, "cycle-count")

    assert derive_on_hand_from_complete_history([adjustment, delivery, opening]) == 105
    assert derive_state_extension_from_anchor(
        [adjustment, delivery, opening],
        anchor_event_id="event-11",
        anchor_on_hand=120,
    ) == (105, ["event-12"])
    assert derive_on_hand_from_complete_history([delivery, adjustment]) is None


def test_event_evidence_supports_extension_and_marks_contradiction() -> None:
    opening = event("event-10", 100, "opening-stock-10")
    delivery = event("event-11", 20, "delivery")
    adjustment = event("event-12", -15, "cycle-count")
    records = {
        "north": record("north", 120, 11, 11, delivery),
        "south": record("south", 120, 11, 11, delivery),
        "west": record("west", 105, 12, 12, adjustment),
    }
    evidence = extract_evidence(
        conflict(records),
        {
            "west": EventHistoryResponse(
                system_id="west",
                sku="SKU-001",
                events=[adjustment, delivery, opening],
            )
        },
    )

    assert evidence.findings_of_type(
        EvidenceType.EVENT_HISTORY_EXTENDS_KNOWN_STATE
    )[0].subsequent_event_ids == ["event-12"]
    assert not evidence.findings_of_type(EvidenceType.INSUFFICIENT_EVIDENCE)

    records["west"].inventory = Inventory(on_hand=106, reserved=8, available=98)
    contradicted = extract_evidence(
        conflict(records),
        {
            "west": EventHistoryResponse(
                system_id="west",
                sku="SKU-001",
                events=[opening, delivery, adjustment],
            )
        },
    )
    assert contradicted.findings_of_type(
        EvidenceType.EVENT_HISTORY_CONTRADICTS_STATE
    )
