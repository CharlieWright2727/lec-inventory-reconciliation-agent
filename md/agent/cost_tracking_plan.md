# Reconciliation Agent — Cost Tracking Plan

## Purpose

The reconciliation agent must report the operational cost of each run in a concrete and auditable way.

Cost tracking should measure every warehouse API interaction automatically so the agent can explain not only what it did, but what that run cost in requests, latency, and transferred data.

## V1 Implementation Status

The read-only V1 agent now records every catalogue request through the
instrumented `WarehouseClient`. Each run owns its own in-memory metric recorder,
and aggregate request counts, GET/PUT counts, catalogue queries, payload bytes,
API latency, failures, and wall-clock time are derived from the individual call
records.

Failed HTTP and connection attempts are retained as metrics. V1 has no write
method, so its PUT count naturally remains zero. Metrics currently represent
application-level request and response body bytes, excluding headers and network
protocol overhead.

The cost system should be designed so that individual agent components do not manually record metrics. Instead, all warehouse HTTP traffic should pass through one instrumented client layer.

---

## Core Principle

Every warehouse API request should automatically produce a metric record.

```text
Observer
Planner
Executor
Verifier
    │
    ↓
WarehouseClient
    │
    ↓
Instrumented request wrapper
    │
    ├── start timer
    ├── calculate request payload size
    ├── send HTTP request
    ├── receive response
    ├── calculate response payload size
    ├── stop timer
    └── create ApiCallMetric
            │
            ↓
         RunMetrics
```

This ensures that all API calls are measured consistently.

---

## Proposed Stack

### Python

Used for metric collection, aggregation, timing, reporting, and persistence.

### HTTPX

All warehouse communication should pass through the agent's `WarehouseClient`.

The client should wrap `httpx.AsyncClient` requests so metrics are captured automatically.

### `time.perf_counter()`

Latency should be measured with:

```python
time.perf_counter()
```

rather than wall-clock timestamps.

### Pydantic

Pydantic models should represent:

- individual API call metrics;
- run-level aggregated metrics;
- reconciliation summary metrics.

---

# Per-Request Cost Model

Each warehouse API interaction should produce an `ApiCallMetric`.

```python
class ApiCallMetric(BaseModel):
    request_id: str
    run_id: str

    action_id: str | None = None

    warehouse_id: str

    method: str
    endpoint: str
    purpose: str

    started_at: datetime
    latency_ms: float

    status_code: int | None
    success: bool

    request_bytes: int
    response_bytes: int

    error_type: str | None = None
```

Example:

```json
{
  "request_id": "request-17",
  "run_id": "run-20260814-001",
  "action_id": "action-investigate-SKU-007",
  "warehouse_id": "warehouse-b",
  "method": "GET",
  "endpoint": "/inventory/SKU-007/events",
  "purpose": "query_events",
  "started_at": "2026-08-14T19:20:00Z",
  "latency_ms": 13.7,
  "status_code": 200,
  "success": true,
  "request_bytes": 0,
  "response_bytes": 842,
  "error_type": null
}
```

---

# Request Identification

Every API call should have a unique `request_id`.

Each request should also be linked to:

```text
run_id
```

and, where relevant:

```text
action_id
```

This allows the final report to trace cost back to the action that caused it.

For example:

```text
SKU-007 investigation
    ↓
action-investigate-SKU-007
    ↓
3 event-history requests
    ↓
2.4 KB transferred
    ↓
38 ms aggregate API latency
```

---

# Request Purpose

Each metric should record why the request occurred.

Suggested values:

```text
catalogue_observation
targeted_inventory_query
event_investigation
reconciliation_write
verification_query
health_check
```

This makes the cost report more informative than simply counting GET and PUT calls.

---

# Latency Measurement

Latency should measure the duration of each HTTP request.

Conceptually:

```python
start = time.perf_counter()

response = await http_client.request(...)

elapsed = time.perf_counter() - start
latency_ms = elapsed * 1000
```

---

# Aggregate API Latency

The system should calculate:

```text
total_api_latency_ms
```

as the sum of individual request latencies.

This is different from total wall-clock run time.

Example:

```text
Warehouse A request = 30 ms
Warehouse B request = 40 ms
Warehouse C request = 50 ms
```

If all three run concurrently:

```text
aggregate API latency ≈ 120 ms
wall-clock wait time ≈ 50 ms
```

Both should be reported.

---

# Wall-Clock Run Time

The full reconciliation run should also measure:

```text
wall_clock_time_ms
```

This should start when the agent run begins and stop when the final run state/report is produced.

It includes:

- API calls;
- concurrent waiting;
- conflict detection;
- evidence evaluation;
- planning;
- execution;
- verification;
- replanning;
- reporting.

---

# Request Byte Measurement

For requests with no body:

```text
request_bytes = 0
```

For JSON writes such as:

```text
PUT /inventory/{sku}
```

measure the encoded JSON payload size.

Conceptually:

```python
encoded_payload = json.dumps(
    payload,
    separators=(",", ":")
).encode("utf-8")

request_bytes = len(encoded_payload)
```

If HTTPX exposes the exact encoded request body conveniently, that may be measured instead.

---

# Response Byte Measurement

Response bytes should be measured from the actual HTTP response body:

```python
response_bytes = len(response.content)
```

This applies to successful and unsuccessful responses.

A `409 Conflict`, for example, still consumes response bytes.

---

# Definition of Byte Cost

The project should document that:

> Byte metrics represent application-level HTTP payload bytes and do not include TCP/IP, TLS, Ethernet, or other protocol overhead.

This is sufficient for concrete API/data-transfer accounting without requiring packet-level instrumentation.

---

# Failed Requests

Failed calls must still count towards cost.

Examples:

```text
404
409
422
500
timeout
connection failure
```

A failed request still consumed:

- an API call;
- latency;
- request bytes;
- potentially response bytes.

Suggested fields:

```text
success = false
status_code = HTTP status if available
error_type = timeout / connection_error / etc.
```

---

# Run-Level Metrics

Individual request metrics should be aggregated into a `RunMetrics` model.

```python
class RunMetrics(BaseModel):
    api_calls: list[ApiCallMetric]

    total_api_calls: int = 0

    successful_calls: int = 0
    failed_calls: int = 0

    get_calls: int = 0
    put_calls: int = 0

    catalogue_queries: int = 0
    inventory_queries: int = 0
    event_queries: int = 0
    update_requests: int = 0
    verification_queries: int = 0

    total_request_bytes: int = 0
    total_response_bytes: int = 0
    total_bytes_transferred: int = 0

    total_api_latency_ms: float = 0
    wall_clock_time_ms: float = 0
```

Derived values should be calculated from the individual call records wherever possible rather than maintained separately in multiple places.

---

# Reconciliation Summary Metrics

Operational cost and reconciliation outcome should be kept conceptually separate.

A second model can describe what the agent accomplished:

```python
class ReconciliationSummary(BaseModel):
    warehouses_queried: int
    products_scanned: int

    consistent_products: int
    conflicts_detected: int

    products_investigated: int
    products_reconciled: int
    products_escalated: int

    writes_attempted: int
    writes_successful: int
    writes_failed: int
```

The final report should combine:

```text
ReconciliationSummary
+
RunMetrics
```

---

# Example Final Cost Report

```text
RECONCILIATION RUN COMPLETE

Warehouses queried:      3
Products scanned:       10
Initially consistent:    8
Conflicts detected:      2

Investigated:             1
Reconciled:               1
Escalated:                1

API calls:               11
Successful calls:        11
Failed calls:             0

GET calls:               10
PUT calls:                1

Catalogue queries:        3
Inventory queries:        2
Event queries:            3
Update requests:          1
Verification queries:     2

Request bytes:          281 B
Response bytes:       18,432 B
Total transfer:       18,713 B

Aggregate API latency: 121.4 ms
Wall-clock run time:   182.7 ms
```

---

# Per-Conflict Cost Attribution

Where practical, the agent should be able to show how much additional work a specific conflict required.

Example:

```text
SKU-001
Result: RECONCILED

Additional API cost:
- 1 PUT
- 1 verification GET
- 611 bytes transferred
- 19.4 ms aggregate API latency
```

Another conflict might show:

```text
SKU-007
Result: ESCALATED

Investigation cost:
- 3 event-history GET requests
- 2.4 KB transferred
- 38.1 ms aggregate API latency
```

This demonstrates that the agent selectively gathers extra evidence rather than calling every endpoint for every SKU.

---

# Integration with Agent Actions

Every planned agent action should have an `action_id`.

When the action causes an API request, that ID should be passed into the warehouse client.

```text
PlannedAction
action_id = action-004

        ↓

WarehouseClient.get_events(
    ...,
    action_id="action-004"
)

        ↓

ApiCallMetric
action_id = action-004
```

This creates a full audit chain:

```text
evidence
    ↓
decision
    ↓
plan
    ↓
action
    ↓
API request
    ↓
cost
    ↓
result
```

---

# Metrics Module

A dedicated module should own cost tracking:

```text
agent/
└── metrics.py
```

Responsibilities:

- metric Pydantic models;
- recording API calls;
- aggregation;
- derived totals;
- per-action cost summaries;
- final run cost summaries.

The HTTP client should collect measurements but delegate storage and aggregation to the metrics system.

---

# Warehouse Client Integration

`agent/client.py` should be the normal path for all warehouse HTTP communication.

```text
client method called
    ↓
create request context
    ↓
start timer
    ↓
send HTTPX request
    ↓
capture status / bytes
    ↓
stop timer
    ↓
record ApiCallMetric
    ↓
parse typed response
    ↓
return to caller
```

This prevents cost logic from becoming scattered through the rest of the agent.

---

# Concurrency

Cost tracking must work correctly when requests are performed concurrently.

For example:

```python
await asyncio.gather(
    query_warehouse_a(),
    query_warehouse_b(),
    query_warehouse_c(),
)
```

Each request independently records:

```text
start time
latency
status
bytes
```

The records are then aggregated into the run metrics.

---

# Persistence

For the first implementation, metrics can live in memory as part of `RunState`.

```text
RunState
└── metrics
    └── api_calls[]
```

Later, final run reports may be saved as structured JSON, for example:

```text
logs/
└── run-20260814-001.json
```

Persistence is useful for the final demonstration but is not required for the first cost-tracking implementation.

---

# Testing Plan

Cost tracking should have dedicated tests.

At minimum test:

## Request counting

Three catalogue calls should produce:

```text
total_api_calls = 3
get_calls = 3
catalogue_queries = 3
```

## Successful response bytes

A successful response should produce:

```text
response_bytes > 0
```

## PUT request bytes

A reconciliation write should produce:

```text
request_bytes > 0
```

## Failed requests

A failed/rejected request must still increment:

```text
total_api_calls
failed_calls
```

## Latency

Every completed request should have:

```text
latency_ms >= 0
```

## Byte aggregation

Verify:

```text
total_bytes_transferred
=
total_request_bytes
+
total_response_bytes
```

## Concurrent requests

Three concurrent catalogue requests should produce three separate metric records.

## Action attribution

Requests caused by a planned action should retain the correct `action_id`.

---

# Implementation Order

```text
1. Define ApiCallMetric.
2. Define RunMetrics.
3. Create metrics.py aggregation logic.
4. Instrument WarehouseClient.
5. Record GET metrics.
6. Record PUT metrics.
7. Record failed-request metrics.
8. Add wall-clock run timing.
9. Add ReconciliationSummary.
10. Add per-action attribution.
11. Add final report formatting.
12. Add JSON persistence if useful.
```

Cost tracking should be introduced early enough that later agent behaviour is measured automatically.

---

# Design Principle

> If the agent performs an API interaction, that interaction must appear in the final cost report.

The final report should make it possible to understand:

- how many calls the agent made;
- why those calls occurred;
- how much data was transferred;
- how long the calls took;
- which calls failed;
- which decisions caused additional investigation cost;
- how the final reconciliation outcome was achieved.
