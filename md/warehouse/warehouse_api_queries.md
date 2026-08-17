# Warehouse API Query Contract

This document defines how the reconciliation agent interacts with each simulated warehouse system.

The API is intentionally small and focused on multi-product inventory reconciliation. Each warehouse contains multiple SKUs, and the agent discovers products and conflicts from warehouse HTTP responses rather than receiving a preselected SKU.

---

## 1. Get Inventory Catalogue

### Endpoint

```http
GET /inventory
```

### Purpose

Returns all inventory records currently stored by one warehouse using the warehouse-wide catalogue response defined in `warehouse_schema.md`.

This is the agent's normal first request to every warehouse:

```text
GET /inventory → Warehouse A
GET /inventory → Warehouse B
GET /inventory → Warehouse C
```

The agent builds the union of all returned SKUs and compares each matching product record across the systems. It discovers which products are consistent or conflicting without being told which SKUs to inspect.

Warehouse-wide `system`, health, and `capabilities` information appears once in the response. Each entry in `items` is an `InventoryRecord` containing product, inventory, state, last-event, sync, and data-quality information for one SKU.

---

## 2. Get Targeted Inventory State

### Endpoint

```http
GET /inventory/{sku}
```

### Purpose

Returns the current inventory state for one SKU. This endpoint is used for:

- inspecting one SKU in greater detail;
- verifying an updated SKU;
- targeted tests and debugging.

It is not the primary warehouse-discovery call.

### Example request

```http
GET /inventory/SKU-001
```

The response uses the single-record form defined in `warehouse_schema.md`, including warehouse-wide context and one product's inventory evidence.

---

## 3. Get Recent Inventory Events

### Endpoint

```http
GET /inventory/{sku}/events?limit={n}
```

### Purpose

Returns recent inventory-changing events for the requested SKU only.

This is a secondary query. The agent calls it only when catalogue evidence is insufficient to safely resolve that particular SKU.

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

The agent may use event history to determine whether a warehouse missed an event, is behind the shared revision for that SKU, or presents a conflict that remains too ambiguous to resolve automatically.

---

## 4. Update Inventory State

### Endpoint

```http
PUT /inventory/{sku}
```

### Purpose

Updates one stale product record to a canonical inventory state chosen by the reconciliation agent. A write must modify only the SKU named in the request path and must not alter other products.

The request carries two distinct version values:

- `expected_current_version`: the version the agent previously observed for this SKU on the target warehouse;
- `target_version`: the shared logical revision this SKU should adopt.

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

The warehouse adopts `target_version`; it does not independently increment a local counter.

If `target_version` equals the current version and the supplied inventory is identical, the request is an idempotent no-op:

```json
{
  "status": "unchanged",
  "system_id": "warehouse-b",
  "sku": "SKU-001",
  "previous_version": 42,
  "new_version": 42
}
```

This response does not create a snapshot or event and does not update timestamps, the event cursor, or sync metadata. If the supplied inventory differs while using the same version, the warehouse returns `409 Conflict` because one logical version cannot represent two inventory states.

If the SKU no longer matches `expected_current_version`, the update must not be applied and the warehouse returns `409 Conflict`.

```json
{
  "status": "conflict",
  "message": "Inventory changed after it was read by the reconciliation agent.",
  "expected_current_version": 41,
  "current_version": 42
}
```

---

## 5. Optional Health Query

```http
GET /health
```

This optional endpoint provides a lightweight service health check. Warehouse health is also included once in the catalogue and targeted inventory responses.

---

# Intended Agent Query Flow

```text
1. GET /inventory from Warehouse A
2. GET /inventory from Warehouse B
3. GET /inventory from Warehouse C

4. Build the union of SKUs returned by all systems.

5. Compare every SKU across warehouses.

6. Classify each SKU initially as:
      CONSISTENT
      CONFLICT

7. Ignore consistent SKUs for further investigation.

8. For each conflicting SKU, analyse:
      version
      quantities
      timestamps
      last event
      sync metadata
      warehouse health
      data quality

9. If evidence is insufficient:
      GET /inventory/{sku}/events from relevant warehouses

10. Decide independently for each conflict:
      RECONCILE
      ESCALATE

11. Build a reconciliation plan covering all safe updates.

12. PUT canonical state only to warehouses and SKUs that need correction.

13. Re-query affected SKUs for verification.

14. Produce a warehouse-wide report containing:
      warehouses queried
      products scanned
      products consistent
      conflicts detected
      products reconciled
      products escalated
      API calls
      bytes transferred
      API latency
      wall-clock time
```

The agent discovers conflicting SKUs itself. It receives only warehouse API URLs and HTTP responses, never scenario files, a scenario name, a conflict list, expected results, or instructions identifying a stale warehouse.

# Initial API Surface

```text
GET  /inventory
GET  /inventory/{sku}
GET  /inventory/{sku}/events?limit=N
PUT  /inventory/{sku}

Optional:
GET /health
```

```text
GET /inventory
→ warehouse-wide discovery

GET /inventory/{sku}
→ targeted read / verification

GET /inventory/{sku}/events
→ deeper investigation

PUT /inventory/{sku}
→ targeted reconciliation write
```

No additional warehouse-management endpoints should be added unless they directly support reconciliation.

# Cost Reporting Scope

Cost measurement covers a complete warehouse synchronisation run, not only one product. A report contains values such as:

```text
Warehouses queried:      3
Products scanned:        5
Products consistent:     3
Conflicts detected:      2
Products reconciled:     1
Products escalated:      1

API calls:               9
Data transferred:        18.4 KB
API latency:             185 ms
Wall-clock time:         231 ms
```

The agent measures these costs; the warehouse services do not implement reconciliation policy or run-level cost accounting.
