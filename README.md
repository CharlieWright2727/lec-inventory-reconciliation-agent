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
- deterministic scenario loading for seven warehouse conflict scenarios;
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

| Scenario | Core behaviour |
| --- | --- |
| `one-stale-warehouse` | Direct stale reconciliation |
| `newer-singleton` | Investigate a newer minority |
| `same-version-divergence` | Causal replay and a shared repair revision |
| `mixed-conflicts` | Multiple evidence strategies in one run |
| `incomplete-event-history` | Safe escalation when causality is incomplete |
| `competing-newer-states` | Contradictory causal branches trigger escalation |
| `missing-sku` | Incomplete product coverage triggers escalation |

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

`mixed-conflicts` contains three conflicting SKUs with three different patterns
in one run. V3 directly repairs one stale replica, selectively investigates one
newer singleton, and performs causal replay plus a shared repair revision for a
same-version divergence. Each path is selected independently from runtime data.

Start it with:

```bash
docker compose \
  -f compose.yaml \
  -f compose.mixed-conflicts.yaml \
  up --build
```

`incomplete-event-history` presents an apparently newer warehouse whose exposed
history omits the known causal anchor. V3 investigates, cannot establish a safe
canonical state, performs no writes, and escalates rather than guessing.

Start it with:

```bash
docker compose \
  -f compose.yaml \
  -f compose.incomplete-event-history.yaml \
  up --build
```

`competing-newer-states` gives Warehouse B and C two different, individually
valid extensions of the same Warehouse A event tip. V3 investigates both
branches, recognises that the complete causal evidence is contradictory, and
escalates without choosing either branch or performing a write.

Start it with:

```bash
docker compose \
  -f compose.yaml \
  -f compose.competing-newer-states.yaml \
  up --build
```

`missing-sku` removes `SKU-005` entirely from Warehouse C while A and B retain
matching records. V3 reports `missing_sku` and escalates without inventing,
deleting, investigating, or mutating product state.

Start it with:

```bash
docker compose \
  -f compose.yaml \
  -f compose.missing-sku.yaml \
  up --build
```

The catalogue-only API remains `run_agent()`, V2 dry-run reasoning remains
`run_agent_v2()`, and full reconciliation is `run_agent_v3()`. The CLI runs V3.
See `md/agent/agent_v2.md` and `md/agent/agent_v3.md` for the complete design.

## Live warehouse simulation

The final simulation milestone runs the unchanged V3 agent against five live
disturbances injected through guarded warehouse HTTP controls. Every disturbance
runs exactly once in a seeded random order. V3 must resolve three safe cases and
safely escalate two ambiguous causal cases with zero writes; the simulator then
independently marks each round `PASS` or `FAIL` and gates the next round on a
full clean-state check.

Start the clean simulation-enabled warehouse services:

```bash
docker compose \
  -f compose.yaml \
  -f compose.simulation.yaml \
  up --build -d
```

Run the simulation and optionally reproduce it with a seed:

```bash
.venv/bin/python -m simulation.runner
.venv/bin/python -m simulation.runner --seed 81724
```

Then stop the services:

```bash
docker compose \
  -f compose.yaml \
  -f compose.simulation.yaml \
  down
```

See `simulation/README.md` for the disturbance model, round gating, reset rules,
cost separation, and optional JSON report command.
