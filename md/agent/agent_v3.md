# Reconciliation Agent V3

## Purpose

V3 completes the deterministic reconciliation lifecycle. It reuses V1's
observation and conflict detection and V2's evidence, investigation, policy,
and reassessment before adding typed write plans, whole-plan safety validation,
sequential execution, independent verification, final resolution, and complete
request-cost reporting.

```text
observe → detect → evidence → investigate if needed → decide
        → plan writes → safety validate → execute sequentially
        → verify every participant → resolved or escalated
```

`run_agent()` remains the catalogue-only V1 path, `run_agent_v2()` remains the
read-only reasoning path, and `run_agent_v3()` performs the full lifecycle. The
CLI runs V3.

## Write planning

Only a final V2 `RECONCILE` decision can produce a `ReconciliationPlan`. Each
typed `UpdateInventoryAction` contains the target warehouse and SKU, observed
version for optimistic concurrency, target version and inventory, canonical
source metadata, and evidence-backed reason. The executor therefore performs no
policy or canonical-state selection.

For normal forward propagation, a target behind the canonical revision uses:

```text
expected_current_version = observed target version
target_version = canonical version
inventory = canonical inventory
```

The write source uses the real canonical warehouse ID, snapshot ID, and latest
event ID observed during the run.

## Same-version repair revisions

The warehouse correctly rejects different inventory at the same logical
revision. V3 never weakens that rule. If causal evidence supports one inventory
but the target already has the canonical revision, V3 creates:

```text
repair version = max(observed versions) + 1
```

Every warehouse participating in that SKU is included in the repair plan. This
moves all replicas to one new auditable revision instead of rewriting history.
Warehouses already holding the supported inventory receive a zero-delta repair
event; the unsupported materialisation receives the corrective delta. Because
each participant starts at the same event cursor, the existing store advances
all of them to the same next cursor and logical revision.

## Safety validation

The complete plan is validated before its first PUT. Structured checks confirm:

- the current decision is `RECONCILE` and has a canonical state;
- the plan belongs to that decision;
- canonical source metadata matches the observed source record;
- update targets exactly match the normal decision or complete repair scope;
- a repair uses one shared next revision for every participant;
- each target was configured and observed, contains the SKU, and is writable;
- product identities match;
- inventory satisfies the warehouse model and matches the plan;
- `expected_current_version` equals the observed target revision;
- target revisions never move backwards;
- different inventory is never written at the current revision.

Any failed check rejects the plan before mutation and produces an `ESCALATED`
resolution. Escalation means the agent completed safely, so the overall run
status remains `COMPLETED`.

## Execution and optimistic concurrency

Approved actions execute sequentially for a simple audit trail and predictable
failure boundary. Every write goes through `WarehouseClient.update_inventory`
and includes the observed version as `expected_current_version`.

If a warehouse changes after observation, its 409 response is recorded as a
rejected execution. V3 stops remaining actions, performs verification, and
escalates. It does not retry, overwrite the concurrent state, roll back earlier
writes, or hide partial mutation.

## Independent verification

A successful PUT is not proof of reconciliation. After execution—even after a
partial failure—V3 concurrently reads the reconciled SKU from every
participating warehouse through instrumented `GET /inventory/{sku}` calls.

The verifier converts those fresh responses back into `WarehouseObservation`
objects and reuses the original detector. Verification requires:

- every participant responded;
- no identity, inventory, version, event-progress, or coverage conflict remains;
- every inventory equals the plan;
- every logical revision equals the plan.

Only complete successful execution plus successful verification produces
`RESOLVED`. A missing response, remaining conflict, unexpected state, rejected
write, or partial execution produces `ESCALATED`.

## Scenario behaviour

### One stale warehouse

V2 selects A's revision 42 state and B as the stale target. V3 writes B once,
then verifies A, B, and C. Natural request cost: three catalogues, one PUT, and
three verification reads.

### Newer singleton

V2 selectively reads C's events and proves revision 43 extends the A/B tip. V3
writes A and B sequentially toward C, then verifies all three. Natural request
cost: three catalogues, one event investigation, two PUTs, and three verification
reads.

### Same-version divergence

V2 replays all three histories and supports inventory 120 while contradicting
C's 115. V3 creates shared repair revision 43 and writes A, B, and C before
verifying the common version, inventory, and event cursor. Natural request cost:
three catalogues, three event investigations, three PUTs, and three verification
reads.

## Cost and audit trail

Run state retains observations, conflicts, histories, evidence, decision
history, investigation plans, reconciliation plans, safety checks, ordered
execution results, verification records, final resolutions, and request metrics.

Each HTTP metric includes method, endpoint, purpose, warehouse, status, latency,
success/error, response bytes, and exact serialized PUT request bytes. The CLI
reports catalogue reads, event investigations, reconciliation writes,
verification reads, GET/PUT totals, transferred bytes, API latency, and run wall
time.

## Deliberate limits

V3 has no retry loop, rollback engine, distributed lock, persistence layer, or
LLM. Event data explains `on_hand` but has no semantics for independently
deriving `reserved`; reserved state remains supported by validated observed
agreement. These limits keep automatic behavior conservative and explainable.
