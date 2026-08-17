# Version 1 Agent Write-Up

> **Status:** Historical version-specific write-up. V2 and V3 now implement the
> later evidence, decision, execution, and verification stages.

## Overview

Version 1 is the first functional implementation of the inventory reconciliation
agent. It is intentionally read-only.

Its purpose is to inspect the three independent warehouse systems, build a
structured view of their inventory, and identify products whose records disagree.

V1 answers:

> Which products are inconsistent across the warehouse systems?

It does not yet answer:

> Which warehouse is wrong, and how should it be corrected?

This separation allows observation and conflict detection to be tested before
introducing reconciliation decisions or warehouse updates.

## How the Agent Works

The agent follows this flow:

```text
Load warehouse API addresses
        ↓
Query all warehouse catalogues concurrently
        ↓
Validate each API response
        ↓
Build one observation for each warehouse
        ↓
Group matching products by SKU
        ↓
Compare their logical inventory state
        ↓
Classify each SKU as consistent or conflicting
        ↓
Print the results and API cost
        ↓
Stop without changing anything
```

The agent only knows the three warehouse API addresses. It does not read scenario
files, test expectations, or warehouse memory.

By default, it connects to:

```text
warehouse-a → http://localhost:8001
warehouse-b → http://localhost:8002
warehouse-c → http://localhost:8003
```

These addresses can be changed using the `WAREHOUSE_A_URL`, `WAREHOUSE_B_URL`,
and `WAREHOUSE_C_URL` environment variables.

## Agent Components

### `models.py`

This file defines the agent's internal Pydantic models.

`WarehouseEndpoint` represents a configured warehouse API.

`WarehouseObservation` represents the catalogue returned by one warehouse during
a run.

`ProductObservation` groups one SKU's records across all warehouses.

`ProductConflict` describes the factual differences found for a SKU.

`RunState` contains the complete result of an agent run, including observations,
products, conflicts, status, and metrics.

These models keep the agent state structured and validated instead of passing
unstructured dictionaries between components.

### `client.py`

The warehouse client is the agent's HTTP communication layer.

It uses `httpx.AsyncClient` to request:

```http
GET /inventory
```

Every request is automatically measured. The client records its latency, status
code, response size, and whether it succeeded.

V1 deliberately has no update method, so it cannot make warehouse writes.

### `observer.py`

The observer requests all three warehouse catalogues concurrently using
`asyncio.gather`.

Concurrent requests reduce the overall wall-clock waiting time because the agent
does not need to wait for Warehouse A before contacting B and C.

Each response is validated using the existing warehouse `CatalogueResponse`
Pydantic model. The observer also verifies that the returned warehouse ID matches
the configured endpoint and that the catalogue does not contain duplicate SKUs.

If any warehouse cannot be observed, the complete run fails. The agent does not
analyse an incomplete warehouse view because this could produce misleading
conclusions.

### `detector.py`

The detector builds the union of all SKUs found across the warehouses.

For every discovered SKU, it creates a `ProductObservation` containing the
available record from each warehouse.

It compares four meaningful parts of the records:

- product identity: SKU, name, and barcode;
- inventory quantities: on-hand, reserved, and available;
- logical inventory version;
- event progression, currently represented by the event cursor.

It can report these conflict types:

```text
inventory_mismatch
version_mismatch
event_progress_mismatch
product_identity_mismatch
missing_sku
```

One product may have multiple conflict types.

The detector deliberately ignores warehouse-specific fields such as snapshot IDs
and heartbeat timestamps. These values can legitimately differ even when the
underlying inventory is consistent.

The detector only describes what differs. It does not choose which record is
correct.

### `metrics.py`

The metrics system records the operational cost of every warehouse request.

Each API call records:

- request and run IDs;
- warehouse ID;
- HTTP method and endpoint;
- request purpose;
- start time;
- latency;
- status code;
- success or failure;
- request and response payload sizes;
- error type, where applicable.

Run-level totals are derived from these individual records. These totals include:

```text
total API calls
successful and failed calls
GET and PUT calls
catalogue queries
request and response bytes
total bytes transferred
aggregate API latency
wall-clock run time
```

Failed requests are still counted because unsuccessful calls also consume time
and resources.

### `runner.py`

The runner coordinates the V1 process.

It:

1. Generates a unique run ID.
2. Loads the warehouse endpoints.
3. Creates the initial `RunState`.
4. Observes all configured warehouses.
5. Stops safely if observation fails.
6. Builds the cross-warehouse product observations.
7. Detects consistent and conflicting SKUs.
8. Completes the run timing.
9. Prints a concise report.

The runner does not contain hidden scenario-specific rules.

## Current Scenario Result

When V1 runs against `one-stale-warehouse`, it independently discovers:

```text
Warehouses observed: 3
Products discovered: 10
Consistent products: 9
Conflicting products: 1
```

The conflict is `SKU-001`.

Warehouse A reports:

```text
on_hand: 120
reserved: 8
available: 112
version: 42
event_cursor: 1042
```

Warehouse B reports:

```text
on_hand: 100
reserved: 8
available: 92
version: 41
event_cursor: 1041
```

Warehouse C reports the same logical state as Warehouse A.

V1 therefore identifies:

```text
inventory_mismatch
version_mismatch
event_progress_mismatch
```

It does not describe Warehouse B as stale or Warehouse A and C as authoritative.
Those conclusions require evidence interpretation, which belongs to the next
version.

## Read-Only Safety

A successful V1 run currently performs:

```text
3 catalogue GET requests
0 PUT requests
```

It never calls the warehouse event-history or update endpoints.

Warehouse B remains at version 41 and an on-hand quantity of 100 after the run.
This confirms that V1 only observes and compares warehouse state.

## Failure Behaviour

If any configured warehouse is unavailable, returns an HTTP error, or provides an
invalid catalogue, the run is marked as failed.

The failed request is still included in the metrics report.

Conflict analysis is not performed because comparing only a subset of the
configured warehouses could cause a missing service to be mistaken for an
inventory inconsistency.

V1 does not use retry loops. Retry behaviour can be considered later if there is
a clear reason for it.

## Testing

The automated tests cover:

- construction and validation of agent models;
- consistent product detection;
- each supported conflict type;
- missing SKUs;
- exclusion of snapshot IDs from logical comparison;
- API request counting;
- response-byte and latency recording;
- failed-request accounting;
- concurrent warehouse observation;
- failure when the warehouse view is incomplete;
- detection of the real scenario through mocked warehouse APIs.

The current complete project suite passes 34 tests.

The agent was also tested against the running Docker services and correctly found
10 products, nine consistent products, and one conflict without changing
Warehouse B.

## Current Limitations

V1 does not implement:

- event-history investigation;
- interpretation of conflict evidence;
- stale-replica identification;
- selection of a correct or canonical state;
- reconciliation decisions;
- action planning;
- warehouse writes;
- post-write verification;
- replanning;
- escalation decisions;
- LLM or machine-learning functionality.

These are intentional boundaries rather than missing V1 requirements.

## Subsequent milestone (now implemented)

V2 subsequently introduced evidence investigation while remaining read-only.

For each detected conflict, V2 can decide whether the
catalogue contains enough information or whether additional evidence is required.

For `SKU-001`, it could request:

```http
GET /inventory/SKU-001/events
```

from the relevant warehouses.

The evidence layer would then compare:

- logical versions;
- inventory quantities;
- event cursors;
- recent event IDs;
- event types and quantity changes;
- timestamps;
- warehouse health;
- data-quality status.

From the current scenario, it should discover that Warehouse A and C processed
`evt-1042`, while Warehouse B's history stops at `evt-1041`.

The recommended development sequence is:

1. Add structured evidence and finding models.
2. Add instrumented event-history reads to the warehouse client.
3. Investigate only conflicting SKUs.
4. Determine whether the available evidence supports a stale-replica finding.
5. Add deterministic reconciliation decisions.
6. Build explicit reconciliation plans.
7. Validate every proposed action before execution.
8. Add version-safe warehouse writes.
9. Re-query affected records and verify the result.
10. Reassess, replan, or escalate when reconciliation cannot be completed safely.
11. Produce a final auditable report covering evidence, decisions, actions, and
    cost.

This incremental approach keeps each stage testable and makes it clear how the
eventual agent reaches and verifies its reconciliation decisions.
