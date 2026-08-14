# Warehouse Inventory JSON Schema

This document defines the agreed warehouse inventory response schemas for the LEC AI inventory reconciliation agent.

Each warehouse stores a catalogue containing multiple products. The agent normally retrieves the complete catalogue from every warehouse, discovers the union of SKUs, and compares matching product records. Deeper event history is deliberately excluded and is retrieved separately only for conflicting SKUs when required.

## Primary Warehouse Catalogue Response

The primary response from `GET /inventory` is a warehouse catalogue. Its `items` array contains one complete `InventoryRecord` for every product/SKU held by the warehouse.

An `InventoryRecord` contains these product-specific sections:

```text
product
inventory
state
last_event
sync
data_quality
```

The example below shows one complete array entry for readability. In a real response, the `items` array repeats this complete structure for every SKU.

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
  "items": [
    {
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
      }
    }
  ],
  "capabilities": {
    "writable": true,
    "supports_version_check": true
  }
}
```

## Expanded Warehouse Catalogue Example

`GET /inventory` returns all inventory records currently stored by one warehouse. The `items` array contains one complete `InventoryRecord` for every SKU in that warehouse; it is not a list of product identities or abbreviated records.

Every element in `items` therefore contains all six SKU-scoped sections:

```text
items[]
├── product
├── inventory
├── state
├── last_event
├── sync
└── data_quality
```

Warehouse/service information appears once, outside `items`, rather than being repeated for every SKU. The example below shows two complete items to demonstrate the repeated structure. A real response repeats the same complete structure for every product in the warehouse catalogue (currently `SKU-001` through `SKU-010`).

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
  "items": [
    {
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
      }
    },
    {
      "product": {
        "sku": "SKU-002",
        "name": "USB-C Hub",
        "barcode": "5012345678902"
      },
      "inventory": {
        "on_hand": 45,
        "reserved": 3,
        "available": 42
      },
      "state": {
        "version": 18,
        "snapshot_id": "snap-warehouse-a-SKU002-18",
        "updated_at": "2026-08-13T18:20:00Z",
        "updated_by": "inventory-event-processor",
        "checksum": "sha256:def456..."
      },
      "last_event": {
        "event_id": "evt-2018",
        "type": "order_allocated",
        "quantity_delta": -1,
        "occurred_at": "2026-08-13T18:19:58Z",
        "processed_at": "2026-08-13T18:20:00Z",
        "reference": "order-5521"
      },
      "sync": {
        "status": "up_to_date",
        "last_successful_sync_at": "2026-08-13T18:20:01Z",
        "last_synced_version": 18,
        "event_cursor": 2018,
        "sync_lag_seconds": 1
      },
      "data_quality": {
        "status": "valid",
        "warnings": [],
        "last_validated_at": "2026-08-13T18:20:01Z"
      }
    }
  ],
  "capabilities": {
    "writable": true,
    "supports_version_check": true
  }
}
```

The catalogue separates scope deliberately:

- `system`, `system.health`, and `capabilities` describe the warehouse/service as a whole;
- each entry in `items` is a complete `InventoryRecord` for one SKU;
- every entry contains its own `product`, `inventory`, `state`, `last_event`, `sync`, and `data_quality` sections;
- none of those SKU-scoped sections may be omitted merely because another item already contains fields with the same names.

Conceptually, the complete response is:

```text
CatalogueResponse
├── system                         warehouse-wide, included once
├── items
│   ├── InventoryRecord: SKU-001   all six SKU-scoped sections
│   ├── InventoryRecord: SKU-002   all six SKU-scoped sections
│   ├── InventoryRecord: SKU-003   all six SKU-scoped sections
│   └── ...one complete record for every remaining SKU
└── capabilities                   warehouse-wide, included once
```

## Targeted SKU Response

`GET /inventory/{sku}` returns one selected `InventoryRecord` rather than a catalogue. That targeted response intentionally has no `items` array: the selected record's `product`, `inventory`, `state`, `last_event`, `sync`, and `data_quality` sections appear directly between the warehouse-wide `system` and `capabilities` fields.

Use `GET /inventory` when the agent needs to discover and compare the complete warehouse catalogue. Use `GET /inventory/{sku}` only for a targeted read or post-update verification.

## Version Semantics

Inventory versions are scoped to an individual SKU.

Each SKU has its own shared logical revision stream across warehouse systems. If the same logical state for `SKU-001` is revision `42`, every warehouse that has fully applied it should report version `42` for that SKU.

```text
SKU-001:
Warehouse A = version 42
Warehouse B = version 41
Warehouse C = version 42

→ meaningful comparison: Warehouse B may be behind for SKU-001
```

Versions belonging to different SKUs must never be compared:

```text
SKU-001 version 42
SKU-002 version 18

→ not a meaningful comparison
```

## Event History

Each `InventoryRecord` contains only the `last_event` for that SKU. Its event cursor and deeper event history also belong exclusively to that SKU's logical inventory stream.

Deeper history is queried separately through:

```http
GET /inventory/{sku}/events?limit=N
```

This keeps the warehouse catalogue smaller and allows the agent to incur the additional API cost only when a particular conflict requires deeper investigation.

## Agent Isolation

The reconciliation agent receives only warehouse API URLs and warehouse HTTP responses. It must never read scenario files directly or receive a scenario name, a list of conflicting SKUs, an expected result, or instructions identifying a stale warehouse. It discovers the product catalogue and all conflicts itself.
