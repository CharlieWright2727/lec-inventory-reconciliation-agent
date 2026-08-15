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

## Planned approach

The project will simulate multiple independent inventory systems through local APIs. A reconciliation agent will inspect their inventory state, determine whether conflicting records can safely be resolved, execute any required updates, verify the resulting state and produce an auditable cost report.

The exact reconciliation strategy and architecture will be developed as part of the assessment.

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
- focused warehouse API tests.

Not yet implemented:

- reconciliation writes and post-write verification;
- safe execution semantics for same-version materialised-state correction.

V2 determines what should happen but deliberately does not mutate warehouses.
Its `RECONCILE` result is a recommendation that V3 can later validate, execute,
and verify.

The three warehouse containers share one implementation and image while using separate identities and independent in-memory state.

With the warehouses running, execute the read-only agent with:

```bash
python -m agent.runner
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

V2 initially classifies this pattern as `INVESTIGATE`, selectively queries only
Warehouse C's `SKU-001` event history, and recommends reconciling A and B forward
when the additional event is shown to explain C's state.

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

V2 investigates event history only for the conflicting SKU. Shared complete
history derives 120 units and identifies Warehouse C's materialised value as
unsupported. This intentionally demonstrates a case where additional API cost
produces materially better evidence and lowers reconciliation risk.

The original catalogue-only API remains available as `run_agent()`. The V2 API
is `run_agent_v2()`, and `python -m agent.runner` runs V2 for the demonstration
CLI. See `md/agent/agent_v2.md` for the evidence and decision rules.
