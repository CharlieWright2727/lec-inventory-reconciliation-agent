# Warehouse API Query Contract

This document defines how the reconciliation agent interacts with each simulated warehouse system.

The API is intentionally small and focused on inventory reconciliation.

---

## 1. Get Inventory State

### Endpoint

```http
GET /inventory/{sku}
```

### Purpose

Returns the current inventory state for a single SKU.

The agent calls this endpoint on every warehouse system before deciding whether the systems agree or require reconciliation.

### Example request

```http
GET /inventory/SKU-001
```

### Example response

```json
{
  "system": {
    "id": "warehouse-a",
    "health": {
      "status": "healthy",
      "last_heartbeat_at": "2026-08-13T18:29:58Z",
      "error_rate_5m": 0.0
    }
  },

  "product": {
    "sku": "SKU-001",
    "name": "Wireless Keyboard",
    "barcode": "5012345678901"
  },

  "inventory": {
    "on_hand": 120,
    "reserved": 8,
    "available": 112
  },

  "state": {
    "version": 42,
    "snapshot_id": "snap-warehouse-a-SKU001-42",
    "updated_at": "2026-08-13T18:27:14Z",
    "updated_by": "inventory-event-processor",
    "checksum": "sha256:abc123..."
  },

  "last_event": {
    "event_id": "evt-1042",
    "type": "stock_received",
    "quantity_delta": 20,
    "occurred_at": "2026-08-13T18:27:12Z",
    "processed_at": "2026-08-13T18:27:14Z",
    "reference": "delivery-8841"
  },

  "sync": {
    "status": "up_to_date",
    "last_successful_sync_at": "2026-08-13T18:27:16Z",
    "last_synced_version": 42,
    "event_cursor": 1042,
    "sync_lag_seconds": 2
  },

  "data_quality": {
    "status": "valid",
    "warnings": [],
    "last_validated_at": "2026-08-13T18:27:15Z"
  },

  "capabilities": {
    "writable": true,
    "supports_version_check": true
  }
}
```

### How the agent uses it

The agent compares:

- stock quantities;
- shared inventory version;
- update timestamps;
- most recent processed event;
- event cursor;
- sync lag;
- system health;
- data quality;
- write capabilities.

If this provides enough evidence, the agent proceeds without deeper queries.

---

## 2. Get Recent Inventory Events

### Endpoint

```http
GET /inventory/{sku}/events?limit={n}
```

### Purpose

Returns recent inventory-changing events for a SKU.

This is a secondary query. The agent should call it only when the initial inventory responses do not provide enough evidence for a safe decision.

### Example request

```http
GET /inventory/SKU-001/events?limit=5
```

### Example response

```json
{
  "system_id": "warehouse-a",
  "sku": "SKU-001",
  "events": [
    {
      "event_id": "evt-1042",
      "type": "stock_received",
      "quantity_delta": 20,
      "occurred_at": "2026-08-13T18:27:12Z"
    },
    {
      "event_id": "evt-1041",
      "type": "order_allocated",
      "quantity_delta": -2,
      "occurred_at": "2026-08-13T18:21:03Z"
    }
  ]
}
```

### How the agent uses it

The agent may use event history to determine whether:

- one warehouse missed an event;
- one system is behind the shared inventory revision;
- a stock difference is explained by a legitimate inventory event;
- the conflict remains too ambiguous to resolve automatically.

---

## 3. Update Inventory State

### Endpoint

```http
PUT /inventory/{sku}
```

### Purpose

Updates a stale warehouse to a canonical inventory state chosen by the reconciliation agent.

The request carries two distinct version values:

- `expected_current_version`: the version the agent previously observed on the target warehouse;
- `target_version`: the shared logical inventory revision the target warehouse should adopt.

### Example request

```json
{
  "expected_current_version": 41,
  "target_version": 42,

  "inventory": {
    "on_hand": 120,
    "reserved": 8,
    "available": 112
  },

  "source": {
    "system_id": "warehouse-a",
    "snapshot_id": "snap-warehouse-a-SKU001-42",
    "event_id": "evt-1042"
  },

  "reason": "stale_inventory_reconciliation"
}
```

### Successful response

```json
{
  "status": "updated",
  "system_id": "warehouse-b",
  "sku": "SKU-001",
  "previous_version": 41,
  "new_version": 42
}
```

The warehouse adopts `target_version`. It does not independently increment its own local counter.

### Version conflict

If the target warehouse no longer matches `expected_current_version`, the update must not be applied.

```http
409 Conflict
```

```json
{
  "status": "conflict",
  "message": "Inventory changed after it was read by the reconciliation agent.",
  "expected_current_version": 41,
  "current_version": 42
}
```

---

## 4. Optional Health Query

### Endpoint

```http
GET /health
```

### Purpose

Provides a lightweight service health check.

This endpoint is optional because health information is already included in the inventory response.

---

# Intended Agent Query Flow

```text
1. GET inventory from Warehouse A
2. GET inventory from Warehouse B
3. GET inventory from Warehouse C

4. Compare current states

5. If evidence is sufficient:
      build a reconciliation plan

   If evidence is insufficient:
      GET recent events from relevant warehouses
      analyse again

6. If reconciliation is safe:
      PUT canonical state to stale warehouses

   Otherwise:
      escalate without writing

7. Re-query updated warehouses to verify the result

8. Report:
      decision
      actions
      API calls
      bytes transferred
      API latency
      wall-clock time
```

# Initial API Surface

```text
GET  /inventory/{sku}
GET  /inventory/{sku}/events?limit=N
PUT  /inventory/{sku}

Optional:
GET /health
```

No additional warehouse-management endpoints should be added unless they directly support reconciliation.
