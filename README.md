# Inventory Reconciliation Agent

This repository contains my submission for the LEC AI Engineering Intern build assessment.

## Task

Build an agent that synchronises product inventory across three or more independent warehouse systems.

The agent will:

- query multiple warehouse inventory sources;
- detect inconsistencies between their stock records;
- decide how those inconsistencies should be reconciled;
- create and execute a sequence of reconciliation actions;
- update affected warehouse systems;
- measure and report the cost of the synchronisation process, including metrics such as API calls, latency and data transferred.

## Reconciliation strategy

The project simulates independent inventory systems through local APIs. The
agent observes their catalogues, detects factual conflicts, interprets typed
evidence, selectively investigates causal event history, plans and safety-checks
writes, executes with optimistic concurrency, and independently verifies the
result.

The strategy deliberately avoids blind majority voting: two matching replicas
can both be behind a legitimate newer minority. It also avoids latest timestamp
only, averaging stock values, and fixed source priority because none proves how
a state arose. Instead it uses consensus plus logical progress for obvious stale
replicas, causal event evidence for ambiguous conflicts, forward repair revisions
instead of rewriting historical versions, mandatory post-write verification,
and escalation rather than guessing.

## Current status

Implemented:

- three independent simulated warehouse APIs;
- one reusable FastAPI implementation with multi-product catalogues;
- catalogue, targeted SKU, and SKU-specific event-history reads;
- validated, version-aware inventory updates with optimistic concurrency;
- a Docker Compose runtime for `warehouse-a`, `warehouse-b`, and `warehouse-c`;
- deterministic scenario loading for three warehouse conflict scenarios;
- a read-only V1 agent that observes warehouse catalogues concurrently;
- structured run, observation, product, conflict, and API-cost models;
- factual cross-warehouse conflict detection and a command-line run summary;
- a read-only V2 evidence, policy, and selective investigation loop;
- deterministic stale-replica, newer-minority, and same-progress decisions;
- dynamically planned, instrumented SKU event-history investigation;
- typed V3 reconciliation plans and whole-plan safety validation;
- sequential optimistic-concurrency writes with partial-failure audit trails;
- shared forward repair revisions for same-version divergence;
- independent targeted verification using the original conflict detector;
- explicit per-SKU `RESOLVED`, `NO_ACTION`, or `ESCALATED` outcomes;
- request-purpose, latency, exact PUT-byte, response-byte, and transfer metrics;
- focused warehouse API tests.

V3 completes the core reconciliation lifecycle. V2 remains available as a
read-only dry-run API.

The three warehouse containers share one implementation and image while using separate identities and independent in-memory state.

With the warehouses running, execute V3 with:

```bash
.venv/bin/python -m agent.runner
```

## Deterministic scenarios

`one-stale-warehouse` is the default Docker Compose scenario. Warehouse A and C
agree on revision 42 while Warehouse B remains on revision 41.

`newer-singleton` presents a different safety case: Warehouse A and B agree on
revision 42 while the healthy, internally valid Warehouse C reports revision 43.
This tests whether the future decision layer avoids blindly applying majority
consensus when the minority may contain newer legitimate data.

Start `newer-singleton` with:

```bash
docker compose \
  -f compose.yaml \
  -f compose.newer-singleton.yaml \
  up --build
```

V3 initially classifies this pattern as `INVESTIGATE`, selectively queries only
Warehouse C's `SKU-001` event history, writes A and B forward when the additional
event explains C's state, and verifies all three warehouses.

`same-version-divergence` gives all three warehouses version 42, event cursor
1042, and the same event history, but Warehouse C contains a different
materialised inventory value. Recency and latest-version comparison therefore
cannot identify the supported state. Majority agreement is useful evidence, but
event history provides the stronger causal explanation: the shared events imply
120 units, while C reports 115 without an event explaining the difference.

Start `same-version-divergence` with:

```bash
docker compose \
  -f compose.yaml \
  -f compose.same-version-divergence.yaml \
  up --build
```

V3 investigates event history only for the conflicting SKU. Shared complete
history derives 120 units and identifies Warehouse C's materialised value as
unsupported. It then advances A, B, and C to one repair revision and verifies the
new shared state.

The catalogue-only API remains `run_agent()`, V2 dry-run reasoning remains
`run_agent_v2()`, and full reconciliation is `run_agent_v3()`. The CLI runs V3.
See `md/agent/agent_v2.md` and `md/agent/agent_v3.md` for the complete design.
