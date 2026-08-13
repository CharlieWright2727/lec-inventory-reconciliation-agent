# Warehouse Scenario Framework

This document describes how predefined warehouse scenarios will be used to test and demonstrate the inventory reconciliation agent.

The scenario system is intended to provide controlled, repeatable starting conditions while allowing the reconciliation agent to make its own decisions dynamically.

---

## Purpose

Each scenario represents a predefined state of the warehouse systems at the beginning of a reconciliation run.

A scenario does **not** tell the agent what action to take.

Instead, it defines the situation the agent must inspect and respond to.

The agent remains responsible for:

```text
retrieving complete warehouse catalogues
discovering the union of products and inconsistencies
ignoring products that are already consistent
deciding whether each conflict requires more information
planning reconciliation across all safe conflicts
executing and verifying targeted updates
escalating individual conflicts when necessary
reporting warehouse-wide synchronisation cost
```

---

## Scenario as Initial State

A scenario will provide a starting catalogue of products for each simulated warehouse service.

Conceptually:

```text
Scenario
│
├── Warehouse A
│   ├── SKU-001
│   ├── SKU-002
│   ├── SKU-003
│   ├── SKU-004
│   └── SKU-005
│
├── Warehouse B
│   ├── SKU-001
│   ├── SKU-002
│   ├── SKU-003
│   ├── SKU-004
│   └── SKU-005
│
└── Warehouse C
    ├── SKU-001
    ├── SKU-002
    ├── SKU-003
    ├── SKU-004
    └── SKU-005
```

A scenario may contain products that are already consistent, products with conflicts, products requiring deeper investigation, and products that may eventually be escalated. The scenario defines only their starting states; it does not label these categories for the agent.

When the warehouse services start, each service loads the data assigned to it by the selected scenario.

The warehouse services then expose that state through their normal HTTP APIs.

The reconciliation agent does not read scenario files directly and is not told the selected scenario's name.

It receives only warehouse API URLs and warehouse HTTP responses.

This keeps the agent separated from the test setup and makes its behaviour representative of interacting with independent external systems.

---

## Runtime Interaction

The intended flow is:

```text
Selected scenario
        |
        v
Warehouse services load their starting states
        |
        v
Warehouse A API
Warehouse B API
Warehouse C API
        |
        v
Agent queries each warehouse through HTTP
        |
        v
Agent retrieves complete catalogues
        |
        v
Agent discovers the union of SKUs and compares each product
        |
        +--> consistent --> no further investigation
        |
        +--> conflict + enough evidence --> plan per-SKU action
        |
        +--> conflict + insufficient evidence --> request SKU event data
        |
        v
Agent independently chooses for each conflict:
RECONCILE / ESCALATE
        |
        v
Any approved updates are sent through warehouse APIs
        |
        v
Agent verifies the resulting state
        |
        v
Agent reports warehouse-wide results, actions and synchronisation cost
```

---

## Deterministic Behaviour

Scenarios should be deterministic.

Running the same scenario from the same initial state should produce the same starting warehouse data every time.

Warehouse inventory should not change randomly in the background.

Changes should occur only because of:

```text
the predefined starting scenario
explicit warehouse operations
reconciliation actions performed by the agent
```

This makes the system:

```text
repeatable
testable
easy to debug
easy to demonstrate
easy for an assessor to reproduce
```

---

## Dynamic Agent Behaviour

Although the scenario is predefined, the agent's behaviour must not be hard-coded to the scenario.

The agent should not receive instructions such as:

```text
warehouse-b is wrong
update warehouse-b
use revision 42
```

It must also never receive:

```text
the scenario name
a list of conflicting SKUs
an expected result
instructions identifying which warehouse is stale
```

Instead it receives only warehouse API URLs and responses. The agent must discover all products, conflicts, and required actions itself.

The agent must derive its decision from available evidence such as:

```text
inventory quantities
shared inventory versions
timestamps
last processed event
event cursor
sync metadata
system health
data quality
warehouse capabilities
```

This allows the same agent implementation to operate across different scenarios without changing its reconciliation code.

---

## Additional Information Requests

The initial warehouse catalogue queries may not always provide enough evidence for every conflicting SKU.

When necessary, the agent can request additional information through endpoints such as:

```http
GET /inventory/{sku}/events?limit=N
```

The scenario therefore also supplies the warehouse event history that these APIs expose.

The agent decides independently for each conflict whether retrieving this SKU-specific information is worth the additional API calls, latency and transferred data.

This means information gathering itself contributes to the cost of the complete warehouse synchronisation run.

---

## Warehouse State Changes

Scenario data defines only the initial state.

After startup, warehouse services behave as normal mutable APIs.

For example:

```text
Initial scenario state:

Warehouse B
on_hand: 100
version: 41
```

If the reconciliation agent performs a valid update, the warehouse service may become:

```text
Runtime state:

Warehouse B
on_hand: 120
version: 42
```

A later GET request must return the updated state.

This ensures that reconciliation actions are real state changes rather than simulated console output.

---

## Resetting a Scenario

The project should provide a simple way to restore a scenario to its original starting conditions.

Restarting or resetting the warehouse services should reload the selected scenario data.

This allows:

```text
repeatable demonstrations
repeatable tests
clean experimentation
easy recovery after reconciliation changes the state
```

The exact reset command or mechanism can be decided during implementation.

---

## Multiple Scenarios

The system should support multiple scenarios using the same warehouse API and agent implementations.

Each scenario should differ only in its starting product catalogues and SKU-specific event histories.

The agent code should not contain scenario-specific branches such as:

```text
if scenario == "example-x":
    update warehouse-b
```

All decisions must be based on the state returned by the warehouse services.

Scenario-specific logic must never appear inside reconciliation code.

---

## Separation of Responsibilities

### Scenario Framework

Responsible for:

```text
defining starting warehouse states
defining starting product catalogues
defining available SKU-specific historical events
loading repeatable test conditions
resetting the environment
```

### Warehouse Services

Responsible for:

```text
loading their assigned starting state
serving inventory through HTTP
serving event history
validating updates
changing runtime state after valid writes
```

### Reconciliation Agent

Responsible for:

```text
observing warehouse state
discovering products and inconsistencies
investigating only conflicting SKUs
choosing reconcile or escalate independently per conflict
planning safe targeted actions
executing and verifying SKU-specific updates
measuring warehouse-wide API and synchronisation cost
```

---

## Design Principle

The scenario defines the **problem**.

The warehouse APIs expose the **evidence**.

The reconciliation agent decides the **solution**.

This keeps the assessment controlled and reproducible without predetermining how the agent must respond.
