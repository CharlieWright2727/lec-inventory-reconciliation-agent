"""Convert factual conflicts and event histories into typed evidence."""

from collections import defaultdict

from agent.models import (
    AgreementGroup,
    ConflictType,
    EvidenceFinding,
    EvidenceSet,
    EvidenceType,
    LogicalState,
    ProductConflict,
)
from warehouse.models import EventHistoryResponse, InventoryEvent, InventoryRecord


def extract_evidence(
    conflict: ProductConflict,
    event_histories: dict[str, EventHistoryResponse] | None = None,
) -> EvidenceSet:
    """Build all currently-supported evidence for a single product conflict."""
    histories = event_histories or {}
    findings: list[EvidenceFinding] = []
    groups = _agreement_groups(conflict.records)

    for index, group in enumerate(groups):
        if len(group.warehouse_ids) >= 2:
            findings.append(
                _finding(
                    conflict.sku,
                    EvidenceType.WAREHOUSES_AGREE,
                    index,
                    group.warehouse_ids,
                    detail=(
                        f"{', '.join(group.warehouse_ids)} agree on inventory, "
                        f"revision {group.state.version}, event cursor "
                        f"{group.state.event_cursor}, and latest event"
                    ),
                )
            )

    findings.extend(_relative_progress_findings(conflict, groups))

    versions = {record.state.version for record in conflict.records.values()}
    cursors = {record.sync.event_cursor for record in conflict.records.values()}
    inventories = {
        _inventory_key(record) for record in conflict.records.values()
    }
    if len(inventories) > 1 and len(versions) == 1 and len(cursors) == 1:
        findings.append(
            _finding(
                conflict.sku,
                EvidenceType.SAME_VERSION_DIVERGENCE,
                0,
                sorted(conflict.records),
                detail=(
                    "Materialised inventory differs even though logical version "
                    "and event cursor agree"
                ),
            )
        )

    if ConflictType.PRODUCT_IDENTITY_MISMATCH in conflict.conflict_types:
        findings.append(
            _finding(
                conflict.sku,
                EvidenceType.INCOMPATIBLE_PRODUCT_IDENTITY,
                0,
                sorted(conflict.records),
                detail="Warehouse records do not identify the product consistently",
            )
        )
    if ConflictType.MISSING_SKU in conflict.conflict_types:
        findings.append(
            _finding(
                conflict.sku,
                EvidenceType.MISSING_WAREHOUSE_STATE,
                0,
                sorted(conflict.records),
                detail="At least one warehouse has no record for this SKU",
            )
        )

    if histories:
        findings.extend(_event_history_findings(conflict, histories, groups))

    return EvidenceSet(
        sku=conflict.sku,
        findings=findings,
        agreement_groups=groups,
        observed_records=conflict.records,
        investigated_warehouses=sorted(histories),
    )


def derive_on_hand_from_complete_history(
    events: list[InventoryEvent],
) -> int | None:
    """Replay only histories carrying an explicit opening-stock anchor."""
    ordered = _chronological(events)
    if not ordered:
        return None
    opening = ordered[0]
    if not (
        opening.reference.startswith("opening-stock")
        or opening.reference == "initial-catalogue-load"
    ):
        return None
    derived = sum(event.quantity_delta for event in ordered)
    return derived if derived >= 0 else None


def derive_state_extension_from_anchor(
    events: list[InventoryEvent],
    *,
    anchor_event_id: str,
    anchor_on_hand: int,
) -> tuple[int, list[str]] | None:
    """Apply events following a known observed state without assuming zero."""
    ordered = _chronological(events)
    matching_indexes = [
        index
        for index, event in enumerate(ordered)
        if event.event_id == anchor_event_id
    ]
    if len(matching_indexes) != 1:
        return None
    subsequent = ordered[matching_indexes[0] + 1 :]
    if not subsequent:
        return None
    derived = anchor_on_hand + sum(event.quantity_delta for event in subsequent)
    if derived < 0:
        return None
    return derived, [event.event_id for event in subsequent]


def _agreement_groups(records: dict[str, InventoryRecord]) -> list[AgreementGroup]:
    grouped: dict[tuple[object, ...], list[str]] = defaultdict(list)
    for warehouse_id, record in records.items():
        grouped[_logical_key(record)].append(warehouse_id)
    groups = [
        AgreementGroup(
            warehouse_ids=sorted(warehouse_ids),
            state=_logical_state(records[warehouse_ids[0]]),
        )
        for warehouse_ids in grouped.values()
    ]
    return sorted(groups, key=lambda group: group.warehouse_ids)


def _relative_progress_findings(
    conflict: ProductConflict,
    groups: list[AgreementGroup],
) -> list[EvidenceFinding]:
    findings: list[EvidenceFinding] = []
    agreed_groups = [group for group in groups if len(group.warehouse_ids) >= 2]
    for group_index, group in enumerate(agreed_groups):
        for warehouse_id, record in sorted(conflict.records.items()):
            if warehouse_id in group.warehouse_ids:
                continue
            comparisons = (
                (
                    "version",
                    record.state.version,
                    group.state.version,
                    EvidenceType.WAREHOUSE_BEHIND,
                    EvidenceType.WAREHOUSE_AHEAD,
                ),
                (
                    "event cursor",
                    record.sync.event_cursor,
                    group.state.event_cursor,
                    EvidenceType.EVENT_PROGRESS_BEHIND,
                    EvidenceType.EVENT_PROGRESS_AHEAD,
                ),
            )
            for offset, (label, actual, reference, behind, ahead) in enumerate(
                comparisons
            ):
                if actual == reference:
                    continue
                evidence_type = behind if actual < reference else ahead
                direction = "behind" if actual < reference else "ahead of"
                findings.append(
                    _finding(
                        conflict.sku,
                        evidence_type,
                        group_index * 10 + offset,
                        [warehouse_id],
                        related=group.warehouse_ids,
                        detail=(
                            f"{warehouse_id} {label} {actual} is {direction} "
                            f"the agreed {label} {reference}"
                        ),
                    )
                )
    return findings


def _event_history_findings(
    conflict: ProductConflict,
    histories: dict[str, EventHistoryResponse],
    groups: list[AgreementGroup],
) -> list[EvidenceFinding]:
    findings: list[EvidenceFinding] = []
    valid_histories: dict[str, list[InventoryEvent]] = {}

    for index, (warehouse_id, history) in enumerate(sorted(histories.items())):
        if (
            warehouse_id not in conflict.records
            or history.system_id != warehouse_id
            or history.sku != conflict.sku
            or not history.events
        ):
            findings.append(
                _finding(
                    conflict.sku,
                    EvidenceType.INSUFFICIENT_EVIDENCE,
                    index,
                    [warehouse_id],
                    detail="Event history is missing or does not match the observation",
                )
            )
            continue

        valid_histories[warehouse_id] = history.events
        record = conflict.records[warehouse_id]
        derived = derive_on_hand_from_complete_history(history.events)
        if derived is None:
            findings.append(
                _finding(
                    conflict.sku,
                    EvidenceType.INSUFFICIENT_EVIDENCE,
                    200 + index,
                    [warehouse_id],
                    detail=(
                        "Event history has no explicit opening-stock anchor, so "
                        "absolute on_hand cannot be replayed"
                    ),
                )
            )
        else:
            evidence_type = (
                EvidenceType.EVENT_HISTORY_SUPPORTS_STATE
                if derived == record.inventory.on_hand
                else EvidenceType.EVENT_HISTORY_CONTRADICTS_STATE
            )
            findings.append(
                _finding(
                    conflict.sku,
                    evidence_type,
                    index,
                    [warehouse_id],
                    detail=(
                        f"Complete event replay derives on_hand {derived}; "
                        f"observed on_hand is {record.inventory.on_hand}"
                    ),
                    derived_on_hand=derived,
                )
            )

    if set(valid_histories) == set(conflict.records) and valid_histories:
        signatures = {
            tuple(_event_key(event) for event in _chronological(events))
            for events in valid_histories.values()
        }
        evidence_type = (
            EvidenceType.EVENT_HISTORIES_AGREE
            if len(signatures) == 1
            else EvidenceType.EVENT_HISTORIES_CONTRADICT
        )
        findings.append(
            _finding(
                conflict.sku,
                evidence_type,
                0,
                sorted(valid_histories),
                detail=(
                    "All investigated warehouses report the same causal history"
                    if len(signatures) == 1
                    else "Investigated warehouses report incompatible causal histories"
                ),
            )
        )

    agreed_groups = [group for group in groups if len(group.warehouse_ids) >= 2]
    for group_index, group in enumerate(agreed_groups):
        for warehouse_id, events in sorted(valid_histories.items()):
            if warehouse_id in group.warehouse_ids:
                continue
            record = conflict.records[warehouse_id]
            is_ahead = (
                record.state.version > group.state.version
                or record.sync.event_cursor > group.state.event_cursor
            )
            if not is_ahead:
                continue
            extension = derive_state_extension_from_anchor(
                events,
                anchor_event_id=group.state.last_event_id,
                anchor_on_hand=group.state.inventory.on_hand,
            )
            if extension is None:
                findings.append(
                    _finding(
                        conflict.sku,
                        EvidenceType.INSUFFICIENT_EVIDENCE,
                        100 + group_index,
                        [warehouse_id],
                        related=group.warehouse_ids,
                        detail="History cannot extend the agreed warehouse event tip",
                    )
                )
                continue
            derived, event_ids = extension
            if (
                derived == record.inventory.on_hand
                and event_ids[-1] == record.last_event.event_id
            ):
                findings.append(
                    _finding(
                        conflict.sku,
                        EvidenceType.EVENT_HISTORY_EXTENDS_KNOWN_STATE,
                        group_index,
                        [warehouse_id],
                        related=group.warehouse_ids,
                        detail=(
                            f"Events after {group.state.last_event_id} derive "
                            f"on_hand {derived} and explain the newer state"
                        ),
                        derived_on_hand=derived,
                        anchor_event_id=group.state.last_event_id,
                        subsequent_event_ids=event_ids,
                    )
                )
            else:
                findings.append(
                    _finding(
                        conflict.sku,
                        EvidenceType.EVENT_HISTORY_CONTRADICTS_STATE,
                        100 + group_index,
                        [warehouse_id],
                        related=group.warehouse_ids,
                        detail=(
                            f"Events after {group.state.last_event_id} derive "
                            f"on_hand {derived}, not the observed newer state"
                        ),
                        derived_on_hand=derived,
                    )
                )

    return findings


def _finding(
    sku: str,
    evidence_type: EvidenceType,
    index: int,
    warehouses: list[str],
    *,
    detail: str,
    related: list[str] | None = None,
    derived_on_hand: int | None = None,
    anchor_event_id: str | None = None,
    subsequent_event_ids: list[str] | None = None,
) -> EvidenceFinding:
    identity = "-".join(warehouses) or "none"
    return EvidenceFinding(
        evidence_id=f"{sku}:{evidence_type.value}:{identity}:{index}",
        type=evidence_type,
        warehouse_ids=warehouses,
        related_warehouse_ids=related or [],
        detail=detail,
        derived_on_hand=derived_on_hand,
        anchor_event_id=anchor_event_id,
        subsequent_event_ids=subsequent_event_ids or [],
    )


def _inventory_key(record: InventoryRecord) -> tuple[int, int, int]:
    inventory = record.inventory
    return inventory.on_hand, inventory.reserved, inventory.available


def _logical_key(record: InventoryRecord) -> tuple[object, ...]:
    event = record.last_event
    return (
        *_inventory_key(record),
        record.state.version,
        record.sync.event_cursor,
        event.event_id,
        event.type,
        event.quantity_delta,
        event.occurred_at,
        event.processed_at,
        event.reference,
    )


def _logical_state(record: InventoryRecord) -> LogicalState:
    return LogicalState(
        inventory=record.inventory,
        version=record.state.version,
        event_cursor=record.sync.event_cursor,
        last_event_id=record.last_event.event_id,
    )


def _event_key(event: InventoryEvent) -> tuple[object, ...]:
    return (
        event.event_id,
        event.type,
        event.quantity_delta,
        event.occurred_at,
        event.processed_at,
        event.reference,
    )


def _chronological(events: list[InventoryEvent]) -> list[InventoryEvent]:
    return sorted(events, key=lambda event: (event.processed_at, event.event_id))
