# Warehouse Behaviour Rules

This document defines the behaviour of each simulated warehouse service used by the inventory reconciliation agent.

The goal is to make each warehouse behave like a small, consistent inventory system rather than a static JSON endpoint.

---

## 1. Inventory Consistency

Each inventory record must satisfy:

```text
available = on_hand - reserved
```

Rules:

- `on_hand >= 0`
- `reserved >= 0`
- `reserved <= on_hand`
- `available == on_hand - reserved`
- Invalid updates must be rejected.

---

## 2. Shared Inventory Revision

Every inventory record contains a logical `version`.

This version is shared across warehouse systems for the same SKU and represents the logical inventory revision, not a local per-warehouse write counter.

Example:

```text
Revision 41 = 100 units
Revision 42 = 120 units
```

A warehouse that has applied revision 42 reports version 42.

A warehouse still reporting version 41 is behind.

Rules:

- Versions must never move backwards.
- Reads do not change version.
- Failed writes do not change version.
- A reconciliation write adopts the chosen canonical `target_version`.
- A reconciliation write must not simply calculate `current_version + 1`.

---

## 3. Optimistic Concurrency

Every reconciliation update must include:

```json
{
  "expected_current_version": 41,
  "target_version": 42
}
```

Rules:

- `expected_current_version` must match the warehouse state observed by the agent.
- If it matches, the update may proceed.
- If it does not match, return `409 Conflict`.
- Rejected writes must not alter state.
- `target_version` must not be lower than the warehouse's current version.

This prevents the agent from overwriting a newer change that occurred after its initial read.

---

## 4. Timestamps

Rules:

- `updated_at` changes after every successful inventory update.
- `processed_at` records when an inventory event was applied.
- `occurred_at` records when the underlying business event occurred.
- `last_heartbeat_at` represents service freshness.
- `last_successful_sync_at` records successful synchronisation activity.
- All timestamps use UTC ISO 8601 format.

---

## 5. Snapshot IDs

Each accepted inventory state has a snapshot ID.

Example:

```text
snap-warehouse-a-SKU001-42
```

Rules:

- Snapshot IDs identify warehouse, SKU and logical version.
- Reads return the current snapshot ID.
- Successful state changes generate a new snapshot ID.
- Failed writes leave the snapshot unchanged.

---

## 6. Inventory Events

Supported event types may include:

```text
stock_received
order_allocated
order_cancelled
order_shipped
return_received
stock_adjustment
```

Each event contains:

```text
event_id
type
quantity_delta
occurred_at
processed_at
reference
```

Rules:

- Event IDs are unique.
- `last_event` is included in the standard inventory response.
- Deeper history is stored internally and exposed only through:
  GET /inventory/{sku}/events
- Failed writes do not create events.
- Event history must remain ordered by processing sequence.

---

## 7. Event Cursor

Each warehouse tracks an `event_cursor`.

Rules:

- It represents the latest event the warehouse has processed.
- It must never move backwards.
- Processing a newer event advances the cursor.
- A stale warehouse may intentionally have an older cursor.
- Event cursor values should be comparable across systems participating in the same logical inventory stream.

---

## 8. Sync Metadata

Each warehouse tracks:

```text
status
last_successful_sync_at
last_synced_version
event_cursor
sync_lag_seconds
```

Suggested statuses:

```text
up_to_date
behind
degraded
unknown
```

Rules:

- `last_synced_version` refers to the latest shared inventory revision successfully applied.
- `sync_lag_seconds` represents estimated synchronisation lag.
- A warehouse may be healthy while still being behind.
- Sync metadata must not silently alter inventory quantities.

---

## 9. Health Behaviour

Suggested statuses:

```text
healthy
degraded
unavailable
```

Rules:

- `healthy`: service is operating normally.
- `degraded`: service responds but may be delayed or unreliable.
- `unavailable`: inventory operations fail with a service error.
- Health does not directly change inventory values.
- A healthy service may still contain stale data.

---

## 10. Data Quality

Suggested statuses:

```text
valid
warning
invalid
```

Rules:

- `valid`: all required internal constraints pass.
- `warning`: data is usable but suspicious.
- `invalid`: core constraints fail.
- Warnings must describe the issue.
- Invalid inventory must not be silently propagated.

Example:

```text
available quantity does not equal on_hand - reserved
```

---

## 11. Capabilities

Each warehouse declares:

```json
{
  "writable": true,
  "supports_version_check": true
}
```

Rules:

- If `writable` is false, update requests are rejected.
- If `supports_version_check` is true, `expected_current_version` is required.
- Capabilities describe what the warehouse supports.
- Reconciliation policy belongs to the agent, not the warehouse.

---

## 12. Successful Reconciliation Update

A valid reconciliation write should:

```text
1. Confirm the SKU exists.
2. Confirm the warehouse is writable.
3. Validate expected_current_version.
4. Confirm target_version is not older than current state.
5. Validate proposed inventory quantities.
6. Apply the canonical inventory state.
7. Set state.version to target_version.
8. Generate a new snapshot_id.
9. Update updated_at.
10. Record the reconciliation event/audit reference.
11. Update event and sync metadata where appropriate.
12. Recalculate available quantity.
13. Re-run data-quality validation.
14. Return the resulting state.
```

The warehouse must not invent a new logical version merely because reconciliation occurred.

---

## 13. Rejected Update Behaviour

Typical rejection cases:

```text
Unknown SKU
Warehouse not writable
Version mismatch
Target version older than current state
Negative inventory
Reserved quantity greater than on-hand quantity
Invalid available quantity
Malformed request
Warehouse unavailable
```

Failed writes must not:

```text
change inventory
change version
create a snapshot
create an inventory event
advance the event cursor
```

---

## 14. Deterministic Demo Behaviour

Rules:

- Stock does not change randomly in the background.
- Changes happen only through predefined scenarios or explicit API requests.
- Demo scenarios have a clear narrative explaining why systems disagree.
- Resetting a scenario restores the exact same starting state.

---

## 15. Separation of Responsibilities

The warehouse is responsible for:

```text
storing inventory state
validating its own data
tracking shared inventory revision
tracking events
enforcing safe writes
reporting health and sync metadata
```

The reconciliation agent is responsible for:

```text
querying multiple warehouses
comparing evidence
requesting deeper event history when necessary
deciding which state is credible
choosing reconcile / no-action / escalate
planning writes
executing writes
verifying results
measuring synchronisation cost
```

The warehouse must not decide which system is correct. That decision belongs to the reconciliation agent.
