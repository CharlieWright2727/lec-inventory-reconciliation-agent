# Warehouse Inventory JSON Schema

This document defines the agreed warehouse inventory response schema for the LEC AI inventory reconciliation agent.

The standard inventory response contains enough information for the agent to make an initial reconciliation decision. Deeper event history is deliberately excluded and is retrieved separately only when required.

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

## Version Semantics

`state.version` is a shared logical inventory revision for the SKU, not an independent per-warehouse counter.

If the same logical inventory state is revision `42`, every warehouse that has fully applied that state should report version `42`.

Example:

```text
Warehouse A: quantity 120, version 42
Warehouse B: quantity 100, version 41
Warehouse C: quantity 120, version 42
```

This indicates that Warehouse B is likely behind the current logical inventory revision.

## Event History

The standard inventory response contains only `last_event`.

Deeper history must be queried separately through:

```http
GET /inventory/{sku}/events?limit=N
```

This keeps the normal response smaller and allows the agent to incur the additional API cost only when deeper investigation is necessary.
