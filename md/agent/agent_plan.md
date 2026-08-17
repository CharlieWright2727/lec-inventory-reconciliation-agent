# Reconciliation Agent — General Plan

> **Status:** Implementation-history reference. V1, V2, and V3 are complete;
> current runtime behaviour is documented in `README.md` and `agent_v3.md`.

## Purpose

The reconciliation agent is responsible for inspecting multiple independent warehouse systems, identifying inventory inconsistencies, deciding what information or actions are required, executing safe reconciliation steps, verifying the result, and producing an auditable explanation of what it did.

The agent should behave autonomously, but it should not rely on an LLM or machine-learning model for its core decision-making.

The intended approach is a deterministic, evidence-driven agent that can dynamically choose its next actions based on the warehouse state it observes at runtime.

---

## Core Goal

The agent's goal is:

> Safely bring warehouse inventory representations into agreement where the available evidence supports reconciliation, and avoid making changes where the correct state cannot be established confidently.

The agent should never assume in advance:

- which SKU is inconsistent;
- which warehouse is stale;
- how many conflicts exist;
- whether more evidence will be required;
- whether a write should be performed;
- how many API calls a run will require.

These decisions should be made during the run.

---

## High-Level Agent Loop

The agent should operate using an observe–assess–plan–act–verify loop.

```text
OBSERVE
   ↓
ASSESS CURRENT STATE
   ↓
DETECT CONFLICTS
   ↓
GATHER / EVALUATE EVIDENCE
   ↓
PLAN NEXT ACTIONS
   ↓
VALIDATE ACTIONS
   ↓
EXECUTE
   ↓
VERIFY RESULT
   ↓
REASSESS / REPLAN IF REQUIRED
   ↓
COMPLETE
```

The initial catalogue observation may be similar between runs, but the later action sequence must depend on the data discovered at runtime.

This prevents the reconciliation process from being a fixed script.

---

## Proposed Technology Stack

### Python

Python will be used for the complete reconciliation agent.

It is already used elsewhere in the project and is suitable for HTTP communication, asynchronous execution, typed data processing, reconciliation logic, planning, testing, metrics, and CLI output.

### HTTPX

`httpx` will be used for communication with warehouse APIs.

The agent will use warehouse endpoints such as:

```text
GET /inventory
GET /inventory/{sku}
GET /inventory/{sku}/events
PUT /inventory/{sku}
```

An `httpx.AsyncClient` should be used so independent warehouse requests can be executed concurrently where appropriate.

### asyncio

Python's `asyncio` will support concurrent communication with warehouse services.

For example, the initial catalogue query should be capable of querying Warehouse A, Warehouse B, and Warehouse C at the same time.

### Pydantic

Pydantic models will represent structured agent state.

- warehouse observations;
- product conflicts;
- evidence;
- findings;
- reconciliation decisions;
- planned actions;
- verification results;
- run state;
- final reports.


### pytest

`pytest` will be used to test conflict detection, evidence extraction, decision behaviour, planning, safe execution, verification, replanning, and escalation behaviour.

### Rich

`rich` may be introduced later for the demonstration CLI.

It can provide readable sections, tables, status messages, decision output, plans, verification results, and final reports.

It is not required for the initial agent implementation.

---

## Agent Components

The agent should be divided into clear responsibilities rather than implemented as one large reconciliation function.

A likely structure is:

```text
agent/
├── models.py
├── client.py
├── observer.py
├── detector.py
├── evidence.py
├── policy.py
├── planner.py
├── executor.py
├── verifier.py
├── explanations.py
└── runner.py
```

This structure is provisional and can be adjusted during implementation.

### Client

Responsible for communication with the warehouse APIs.

It should provide a small controlled interface such as:

```text
list_inventory(warehouse)
get_inventory(warehouse, sku)
get_events(warehouse, sku, limit)
update_inventory(warehouse, sku, ...)
```

The reasoning layer should not make arbitrary HTTP requests directly.

### Observer

Collects the current state of configured warehouses.

The initial observation should retrieve each warehouse catalogue and store the validated responses in the current run state.

### Conflict Detector

Compares warehouse observations and determines which SKUs are consistent and which require further attention.

Consistent SKUs should normally require no further investigation.

### Evidence Layer

Converts raw warehouse data into meaningful findings.

Examples may include:

```text
two warehouses agree on the same inventory state
one warehouse is on an older logical version
one warehouse has progressed further through the event stream
product identity differs between systems
a record contains a data-quality warning
a warehouse is degraded or unavailable
```

The exact evidence rules will be defined separately.

### Policy / Decision Layer

Evaluates the available evidence and chooses a high-level outcome.

```text
NO_ACTION
INVESTIGATE
RECONCILE
ESCALATE
```

This layer should remain deterministic and explainable.

### Planner

Turns decisions into explicit actions.

For example:

```text
1. Query recent events for Warehouse B / SKU-004.
2. Reassess the conflict.
```

or:

```text
1. Update Warehouse B / SKU-001.
2. Verify Warehouse B / SKU-001.
3. Reassess overall consistency.
```

Plans should be created from current evidence rather than being predefined for each scenario.

### Safety Validation

Before any write is executed, the proposed action should be checked against safety constraints.

This should include validation that:

- the SKU exists;
- the warehouse is writable;
- product identity is compatible;
- the target inventory is valid;
- the expected current version is known;
- the target version is supported by evidence;
- the action has not already been rejected or escalated.

The detailed safety policy will be specified later.

### Executor

Executes approved actions against warehouse APIs.

It should only execute known agent actions and should not contain reconciliation reasoning itself.

### Verifier

Checks the environment after an action.

A successful `PUT` response is not enough to consider reconciliation complete.

The agent should re-query the affected inventory and confirm that the resulting state matches the intended target.

### Explanation Builder

Each important agent decision should carry structured evidence.

The explanation layer should convert that evidence into readable justification.

For example:

```text
Decision: RECONCILE warehouse-b

Reason:
Warehouses A and C report the same inventory state and logical
revision, while Warehouse B is one revision and one event behind.
This provides strong evidence that Warehouse B is stale.
```

The explanation should be generated from the actual evidence and rule that caused the decision.

An LLM is not required.

---

## Agent State

The agent should maintain a run-level state object containing everything it has learned and done during the current reconciliation run.

Conceptually:

```text
RunState
├── configured warehouses
├── warehouse observations
├── discovered products
├── consistent SKUs
├── conflicts
├── gathered evidence
├── decisions
├── current plan
├── completed actions
├── failed actions
├── verification results
├── escalations
└── cost / request metrics
```

This allows the agent to update its understanding after every observation or action.

---

## Dynamic Planning

The key requirement is that the agent must not follow a fixed reconciliation script.

For example, one conflict may result in:

```text
observe
→ detect stale warehouse
→ update
→ verify
→ complete
```

while another may result in:

```text
observe
→ detect conflict
→ evidence insufficient
→ query events
→ reassess
→ query another warehouse
→ reassess
→ escalate
```

Another run may contain no conflicts at all and terminate after observation.

The sequence of actions therefore depends on the environment observed during the run.

---

## Example Scenario

Assume the agent discovers:

```text
SKU-001

Warehouse A
120 units
version 42
event cursor 1042

Warehouse B
100 units
version 41
event cursor 1041

Warehouse C
120 units
version 42
event cursor 1042
```

The agent may derive findings such as:

```text
A and C agree on inventory.
A and C agree on logical revision 42.
A and C agree on event progress.
B is on an older version.
B is behind in the event stream.
```

If the eventual reconciliation policy considers this sufficient evidence, the agent may create:

```text
Decision:
RECONCILE warehouse-b

Plan:
1. Update Warehouse B to the supported version 42 state.
2. Use expected_current_version=41.
3. Re-query Warehouse B.
4. Verify that A, B and C now agree.
```

If the write succeeds and verification confirms agreement, the conflict is resolved.

---

## Example Investigation Scenario

Assume:

```text
Warehouse A
70 units
version 25
event cursor 700

Warehouse B
74 units
version 26
event cursor 701

Warehouse C
70 units
version 25
event cursor 700
```

Although A and C agree on quantity, Warehouse B appears to contain a newer logical state.

The agent should not automatically overwrite B.

A possible dynamically generated plan is:

```text
1. Query recent Warehouse B events for the SKU.
2. Reassess the conflict using the new evidence.
```

If the event history reveals a legitimate new inventory event, the agent may change its understanding and create a new plan.

This demonstrates replanning based on newly observed evidence.

---

## Escalation

The agent should not be required to reconcile every inconsistency.

If a trustworthy canonical state cannot be determined safely, the correct outcome should be:

```text
ESCALATE
```

An escalation should include:

- the conflicting state;
- evidence considered;
- why automatic reconciliation was unsafe;
- confirmation that no unsafe writes were performed.

---

## AI / Machine Learning Position

The implemented agent does not use an LLM or trained machine-learning model for core reconciliation decisions.

The warehouse data is structured and the reconciliation process needs to be deterministic, auditable, testable, explainable, and safe.

The agent therefore uses deterministic evidence extraction, policy evaluation, planning, execution, and verification.

The system is still agentic because it observes an unknown environment, determines which problems exist, chooses what evidence to gather, constructs actions at runtime, executes actions, observes their outcome, replans when necessary, and stops or escalates when appropriate.

An AI reasoning layer may be considered as future work for genuinely unstructured or ambiguous reconciliation cases, but it is not required for the current problem.

---

## Cost Tracking

The implemented agent measures the operational cost of each reconciliation run.

Metrics are expected to include:

```text
API call count
request bytes
response bytes
total bytes transferred
API latency
failed requests
GET / PUT counts
wall-clock run time
```

Cost instrumentation should sit around the HTTP client layer so all agent requests are measured consistently.

The implemented cost model is documented in `cost_tracking_plan.md` and the root README.

---

## Implemented V1 Boundary

Version 1 implements the read-only beginning of the agent loop:

```text
configured warehouse API URLs
    ↓
concurrent catalogue observation
    ↓
validated WarehouseObservations
    ↓
cross-warehouse ProductObservations
    ↓
factual consistency and conflict detection
    ↓
run summary and API cost report
```

V1 compares product identity, inventory quantities, logical version, and event
cursor. It deliberately excludes warehouse-specific fields such as snapshot IDs
and heartbeat timestamps from logical conflict detection.

All V1 HTTP traffic passes through the instrumented asynchronous warehouse
client. A normal run performs catalogue reads only. If any configured warehouse
cannot be observed, the run retains its request metrics, reports failure, and
does not analyse an incomplete warehouse view.

The V1 API does not interpret which warehouse is stale, query event-history endpoints,
choose a canonical state, make reconciliation decisions, create plans, perform
writes, or verify updates. V2 and V3 add those later lifecycle stages.

The V1 path remains available programmatically as `run_agent()`. The current
command-line entry point runs the complete V3 lifecycle.

## Completed implementation order

The implementation followed this development order:

```text
1. Define evidence and decision rules.
2. Implement selective event investigation.
3. Implement planning.
4. Implement safe execution.
5. Implement verification and replanning.
6. Implement deterministic explanations.
7. Complete write-path integration and scenario tests.
8. Refine the final CLI / demo presentation.
```

---

## Design Principle

> Observe first, act only on evidence, verify every change, and escalate rather than guess.

The warehouse APIs expose the environment.

The agent determines what the environment means and what should happen next.
