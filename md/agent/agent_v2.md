# Reconciliation Agent V2

## Status and scope

V2 adds deterministic reconciliation reasoning to the concurrent, validated,
cost-instrumented observations implemented by V1. It determines what should
happen but deliberately does not mutate warehouses. A `RECONCILE` outcome is a
recommendation for V3, not an executed update.

The runtime flow is:

```text
observe → detect → extract evidence → decide → plan targeted reads
        → gather evidence → extract again → decide again
```

The loop is bounded. If a decision still requests investigation after the
available planned actions have been attempted, V2 escalates instead of retrying
or guessing.

## Component boundaries

- `detector.py` reports factual inventory, version, event-progress, identity,
  and coverage mismatches.
- `evidence.py` gives those facts meaning, detects logical agreement groups,
  and reasons explicitly about event histories.
- `policy.py` maps typed evidence to a deterministic outcome.
- `planner.py` maps an investigation decision to SKU- and warehouse-specific
  event queries.
- `client.py` performs every HTTP call and records its cost.
- `runner.py` owns the bounded observe/decide/investigate/reassess lifecycle.

No policy rule parses display prose, refers to scenario names, or reads test
expectations.

## Evidence model

An `EvidenceSet` retains the observed records, agreement groups, investigated
warehouses, and machine-readable `EvidenceFinding` values. Logical agreement
uses material fields: inventory quantities, revision, event cursor, and latest
event. Snapshot IDs, heartbeat timestamps, and other warehouse-local noise do
not affect grouping.

The evidence types cover:

- warehouses agreeing on one logical state;
- a warehouse being ahead or behind in revision and event progress;
- inventory divergence at the same revision and cursor;
- complete event replay supporting or contradicting materialised on-hand;
- a newer history extending an already observed event tip;
- event histories agreeing or contradicting one another;
- incompatible product identity, missing state, or insufficient evidence.

Absolute on-hand is derived only when the earliest chronological event is an
explicit opening-stock/catalogue-load anchor. A newer state is preferably
evaluated by locating the older observed `last_event` in the newer history and
applying only subsequent `quantity_delta` values. V2 does not claim that event
history explains `reserved`, because the current event model has no reserved
quantity semantics.

## Decision outcomes

- `NO_ACTION`: no reconciliation is necessary.
- `INVESTIGATE`: a selective read can materially improve the evidence.
- `RECONCILE`: one state and target set are sufficiently supported; no write is
  performed in V2.
- `ESCALATE`: automatic reasoning cannot establish a unique safe state. This is
  a successful safety outcome, not a failed run.

The initial policy implements these conservative rules:

1. Two or more independent warehouses agreeing on inventory, revision, event
   progress, and latest event can support reconciliation of a warehouse that is
   strictly behind in both revision and event progress.
2. A differing warehouse that is ahead is never overwritten by majority alone.
   Its event history is queried first.
3. If that newer history contains the known older tip and later deltas explain
   the newer materialised state, the newer warehouse becomes canonical and the
   older warehouses become recommended targets.
4. Inventory divergence at identical revision and event cursor triggers event
   investigation for the involved warehouses. Shared complete history can
   identify which materialised state is supported and which is contradicted.
5. Missing identities, missing SKU state, contradictory/incomplete histories,
   competing newer states, or non-unique support escalate without mutation.

## Why majority is insufficient

Two matching replicas can both be one event behind. Automatically choosing the
majority would overwrite a legitimate newer minority and destroy information.
V2 therefore combines independent agreement with revision and event progress
for obvious stale replicas, and prefers causal event evidence whenever the
minority is newer or recency cannot distinguish states.

## Selective investigation and cost

Consistent products never trigger event queries. An obvious stale replica needs
only the three catalogue calls. A single newer warehouse triggers one event
query for that warehouse and conflicting SKU. Same-progress divergence queries
the histories involved in that SKU because cross-history comparison is the
question being answered.

Every call records warehouse, method, endpoint, status, latency, bytes,
success/error, and purpose. Cost summaries distinguish
`catalogue_observation` from `event_investigation`, making the additional cost
of safer reasoning visible.

## V3 boundary

V2 contains no PUT path and remains the programmatic dry-run option. V3 is
implemented as a separate layer that consumes V2's final decisions, adds
validated write planning, optimistic-concurrency execution, forward repair
revisions, and independent verification. See `agent_v3.md`.
