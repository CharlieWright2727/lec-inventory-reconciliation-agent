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
- focused warehouse API tests.

Not yet implemented:

- evidence interpretation and stale-replica determination;
- reconciliation decisions, planning, writes, and verification;
- event-history investigation by the agent.

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

V1 only detects and reports the conflict. A forthcoming decision layer is
expected to classify this pattern as requiring investigation. Selective event
history queries can later provide additional evidence at additional API cost.

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

V1 currently reports the inventory mismatch and stops. A future evidence layer
should investigate event history only for the conflicting SKU. This scenario
intentionally creates a case where spending additional API cost produces
materially better evidence and lowers reconciliation risk.
