# Live Warehouse Simulation — Implementation Plan

> **Status:** Implemented. This document is retained as the design and
> implementation-history reference; operational instructions are in
> `README.md` and `simulation/README.md`.

## 1. Purpose

The deterministic scenario suite proves that the reconciliation agent behaves correctly for known isolated conditions.

The live warehouse simulation should prove something different:

> The same V3 reconciliation agent can repeatedly observe an evolving distributed warehouse environment, react to disturbances it was not told about, choose the correct action path, and safely reach a terminal outcome before the environment moves on.

The simulation is not a replacement for the seven deterministic scenarios.

It is a higher-level system demonstration built around the existing V3 agent.

The simulation should not introduce a V4 decision engine or scenario-specific logic inside the agent.

---

## 2. Core Design Principles

### 2.1 The simulator creates problems; the agent discovers them

The simulator knows which disturbance it injects.

The reconciliation agent must not.

Flow:

```text
Simulator
   ↓
mutates warehouse state externally
   ↓
Warehouse A / Warehouse B / Warehouse C
   ↓
V3 observes current API state
   ↓
detects conflict
   ↓
collects evidence
   ↓
investigates when required
   ↓
decides
   ↓
reconciles or escalates
   ↓
verifies
```

There must be no production logic such as:

```python
if simulation_round == "stale-replica":
    reconcile_warehouse_b()
```

or:

```python
if sku == "SKU-001":
    use_stale_policy()
```

The existing generic V3 agent must make all reconciliation decisions from observable warehouse state.

---

### 2.2 Only one active disturbance at a time

A new disturbance must never be injected while the previous disturbance is still being processed.

Each round owns the environment until the agent reaches a valid terminal outcome:

```text
RESOLVED
```

or:

```text
ESCALATED
```

Only after the round has been evaluated may the simulator move to the next disturbance.

---

### 2.3 Escalation is a successful terminal outcome when expected

Some warehouse conditions cannot be reconciled safely.

In those cases:

```text
ESCALATED
```

is the correct agent behaviour.

The simulation should distinguish:

```text
Agent outcome:
RESOLVED / ESCALATED
```

from:

```text
Simulation round result:
PASS / FAIL
```

Example:

```text
Scenario:
incomplete-history

Expected agent outcome:
ESCALATED

Actual:
ESCALATED

Unexpected writes:
0

Round result:
PASS
```

A round must not require automatic reconciliation when the safe action is escalation.

---

### 2.4 Escalated environments must be reset

A successfully reconciled environment should naturally converge to a clean state.

An escalated environment may intentionally remain inconsistent.

Therefore:

```text
RESOLVED
→ verify clean state
→ continue
```

but:

```text
ESCALATED
→ validate safe escalation
→ record result
→ reset all warehouses to clean baseline
→ verify clean baseline
→ continue
```

This prevents unresolved conflicts from contaminating later rounds.

---

### 2.5 All required rounds must execute

The simulation should contain a fixed required set of disturbance types.

At startup:

```python
rounds = [...]
random.shuffle(rounds)
```

Every required round must execute exactly once.

The order should be randomised once per simulation run.

The simulation is successful only if every required round executes and passes.

---

### 2.6 Random runs must be reproducible

Generate or accept a random seed.

Example:

```text
Simulation seed: 81724
```

The seed controls round order.

Allow:

```bash
python -m simulation.runner
```

to generate a seed automatically.

Also allow:

```bash
python -m simulation.runner --seed 81724
```

to reproduce the exact round ordering.

The simulation must print the seed in its final report.

---

# 3. Scope

## Required live rounds

Use five live disturbance types.

These cover the strongest operational behaviours without duplicating every static scenario.

### Round A — stale replica

Expected:

```text
RESOLVED
```

Behaviour demonstrated:

- one warehouse falls behind;
- agent identifies a strictly stale replica;
- no event investigation required;
- agent reconciles forward;
- post-write verification succeeds.

---

### Round B — legitimate newer state

Expected:

```text
RESOLVED
```

Behaviour demonstrated:

- two warehouses still agree on an older state;
- a third warehouse has a newer legitimate event;
- the agent does not blindly trust majority consensus;
- it selectively investigates the newer warehouse;
- causal evidence proves the new state;
- older replicas are reconciled forward;
- verification succeeds.

---

### Round C — materialised-state corruption

Expected:

```text
RESOLVED
```

Behaviour demonstrated:

- warehouses report the same logical progress;
- one warehouse has incorrect materialised inventory;
- version/cursor comparison alone cannot identify truth;
- agent investigates event histories;
- causal replay identifies the supported state;
- a shared repair revision is created;
- verification succeeds.

---

### Round D — incomplete event history

Expected:

```text
ESCALATED
```

Behaviour demonstrated:

- one warehouse appears newer;
- event history cannot prove a causal extension;
- the agent refuses to infer missing history;
- no writes occur;
- the conflict is escalated safely.

After this round:

```text
reset warehouses to clean baseline
```

---

### Round E — competing causal branches

Expected:

```text
ESCALATED
```

Behaviour demonstrated:

- two warehouses independently progress from the same known event tip;
- both branches are individually valid;
- the branches result in different supported inventory states;
- causal evidence is complete but contradictory;
- agent refuses to arbitrarily choose a winner;
- no writes occur.

After this round:

```text
reset warehouses to clean baseline
```

---

## Existing deterministic scenarios not required as separate live rounds

Keep these in the static test suite:

```text
mixed-conflicts
missing-sku
```

Reason:

`mixed-conflicts` already proves several conflicts can be handled independently in one observation cycle.

`missing-sku` already proves incomplete product coverage is escalated safely.

The live simulation should focus on temporal behaviour rather than replaying all seven fixture scenarios.

---

# 4. Proposed Project Structure

Add a separate simulation package.

```text
simulation/
├── __init__.py
├── runner.py
├── controller.py
├── disturbances.py
├── models.py
├── reporter.py
└── README.md
```

Optional test structure:

```text
tests/
├── test_simulation_disturbances.py
├── test_simulation_controller.py
└── test_live_simulation.py
```

Do not place simulation orchestration inside:

```text
agent/
```

The agent should remain unaware that it is participating in a simulation.

---

# 5. Simulation Components

## 5.1 `simulation/models.py`

Define structured models for the simulation itself.

Suggested concepts:

### `DisturbanceType`

```text
STALE_REPLICA
NEWER_LEGITIMATE_STATE
MATERIALISED_CORRUPTION
INCOMPLETE_HISTORY
COMPETING_CAUSAL_BRANCHES
```

---

### `ExpectedAgentOutcome`

```text
RESOLVED
ESCALATED
```

---

### `RoundStatus`

```text
PENDING
RUNNING
PASS
FAIL
```

---

### `SimulationRound`

Fields should include approximately:

```text
round_id
disturbance_type
display_name
expected_outcome
expected_zero_writes
started_at
completed_at
agent_cycles
status
failure_reason
```

---

### `RoundResult`

Capture:

```text
disturbance
expected_outcome
actual_outcome
pass/fail
agent_cycles
conflicting_skus
investigation_calls
reconciliation_writes
verification_reads
api_calls
request_bytes
response_bytes
latency_ms
wall_clock_ms
reset_required
reset_completed
```

---

### `SimulationResult`

Capture:

```text
seed
round_order
required_rounds
executed_rounds
passed_rounds
failed_rounds
resolved_rounds
escalated_rounds
unexpected_writes
verification_failures
total_agent_cycles
cumulative_cost
overall_result
```

Use typed Pydantic models or dataclasses consistent with the rest of the project.

---

# 6. Clean Baseline

The simulation needs a canonical clean starting state.

All three warehouses should begin with:

```text
10 products
0 conflicts
healthy
```

The exact product values can come from the existing shared catalogue/default inventory.

The baseline should be deterministic.

Create one clear mechanism such as:

```text
simulation/reset
```

or an internal simulation controller operation that restores all three warehouse stores.

Avoid reconstructing baseline state in several unrelated functions.

---

# 7. Simulation-Only Warehouse Mutation Interface

The current reconciliation PUT endpoint represents writes performed by the reconciliation agent.

The simulator needs a separate mechanism for external business/system activity.

This distinction is critical.

The simulation must be able to introduce:

```text
external stock events
replication lag
materialised state corruption
history truncation
competing independent events
```

without recording them as reconciliation-agent actions.

---

## Recommended approach

Add simulation-only warehouse endpoints or store operations that are disabled unless a simulation mode flag is enabled.

For example:

```text
SIMULATION_MODE=true
```

Possible endpoints:

```text
POST /simulation/events
POST /simulation/replication-lag
POST /simulation/corrupt
POST /simulation/history
POST /simulation/reset
```

Exact endpoint design can be simplified if a smaller generic mutation interface is safer.

The key requirements are:

- unavailable during ordinary runtime unless simulation mode is explicitly enabled;
- clearly named as simulation-only;
- not used by the reconciliation agent;
- not counted as agent reconciliation API cost;
- validated so malformed simulation inputs cannot corrupt unrelated state accidentally.

---

# 8. Disturbance Definitions

Implement disturbances in:

```text
simulation/disturbances.py
```

Each disturbance should:

1. assume a clean starting state;
2. mutate warehouse state through the simulation-only interface;
3. return structured metadata describing what was injected;
4. never call reconciliation-agent decision functions;
5. never know or dictate how V3 should repair the problem.

---

## 8.1 Stale Replica Disturbance

Example operational sequence:

```text
Clean baseline

External business event:
SKU-001 receives +20 stock

Warehouse A processes event
Warehouse C processes event
Warehouse B misses event
```

Final pre-agent state:

```text
A = newer
B = stale
C = newer
```

Expected:

```text
RECONCILE B
RESOLVED
```

---

## 8.2 Legitimate Newer State Disturbance

Example:

```text
A/B/C begin equal

Warehouse C receives external stock adjustment
A/B have not processed it yet
```

Final pre-agent state:

```text
A = version N
B = version N
C = version N+1
```

C history must clearly extend the known event tip.

Expected:

```text
INVESTIGATE C
RECONCILE A/B
RESOLVED
```

---

## 8.3 Materialised Corruption Disturbance

Example:

```text
A/B/C begin equal
same version
same cursor
same causal history

corrupt C materialised on_hand only
```

Do not create an event explaining the corruption.

Expected:

```text
INVESTIGATE A/B/C
causal replay
shared repair revision
RESOLVED
```

---

## 8.4 Incomplete History Disturbance

Example:

```text
A/B remain at known state

C reports newer revision and newer materialised inventory
C's exposed history does not include the known anchor
```

Expected:

```text
INVESTIGATE C
INSUFFICIENT EVIDENCE
ESCALATED
0 PUTs
```

---

## 8.5 Competing Causal Branches Disturbance

Example:

```text
A remains at base revision

B independently applies -10
C independently applies +10

both B and C:
version = N+1
cursor = N+1

but different valid event IDs and resulting stock
```

Expected:

```text
INVESTIGATE B/C
both branches individually supported
branches contradict
ESCALATED
0 PUTs
```

---

# 9. Simulation Controller

Implement the lifecycle in:

```text
simulation/controller.py
```

The controller should coordinate the environment but must not perform reconciliation reasoning.

High-level lifecycle:

```text
INITIALISE
↓
restore clean baseline
↓
verify baseline clean
↓
shuffle required rounds
↓
for each round:
    inject disturbance
    verify disturbance exists
    run V3 agent
    evaluate terminal outcome
    validate round
    clean/reset environment when required
↓
aggregate results
↓
print final report
```

---

# 10. Round Gating

Each round must satisfy a strict gate.

Pseudo-flow:

```python
inject_disturbance(round)

while not terminal:
    report = run_agent_v3(...)
    terminal = round_has_terminal_outcome(report)

evaluate_round(report)

if round_passed:
    prepare_for_next_round()
else:
    stop_simulation_as_failed()
```

However, in the current V3 architecture most conflict processing should reach a terminal outcome during one agent run.

Do not add arbitrary retry loops merely to make simulation output look active.

If one V3 execution is designed to observe → investigate → reassess → execute/escalate → verify, call it once.

Only support multiple agent cycles if the existing architecture genuinely requires them.

---

# 11. Round Evaluation

Round evaluation belongs to the simulator, not the agent.

The simulator knows the expected safe outcome for the disturbance it injected.

---

## Resolvable round

Example:

```text
Expected:
RESOLVED

Actual:
RESOLVED

Verification:
passed

Round:
PASS
```

Failure cases include:

```text
actual ESCALATED
verification failed
unexpected remaining conflict
incorrect write target
unsafe write
agent crash
```

---

## Escalation round

Example:

```text
Expected:
ESCALATED

Actual:
ESCALATED

Writes:
0

Round:
PASS
```

Failure cases include:

```text
agent reports RESOLVED
one or more reconciliation PUTs occur
agent mutates the disputed warehouses
agent crashes
agent silently ignores the conflict
```

---

# 12. Post-Resolution Clean Check

After every `RESOLVED` round, perform a fresh observation before proceeding.

Expected:

```text
Products discovered: 10
Consistent: 10
Conflicts: 0
```

This clean check is separate from V3's internal targeted verification.

Purpose:

> Prove that the whole environment is clean before the next external disturbance is injected.

The clean check should not perform writes.

---

# 13. Post-Escalation Reset

After every expected escalation:

```text
record the unresolved state
↓
record all cost metrics
↓
validate zero unsafe writes
↓
reset A/B/C to baseline
↓
perform clean observation
↓
assert 10 consistent / 0 conflicts
↓
continue
```

If reset validation fails:

```text
Round result: FAIL
Simulation result: FAIL
```

Do not continue from an unknown environment.

---

# 14. Round Ordering

Required rounds:

```text
stale-replica
newer-legitimate-state
materialised-corruption
incomplete-history
competing-causal-branches
```

At simulation startup:

```python
rng = random.Random(seed)
rng.shuffle(rounds)
```

Do not randomly choose with replacement.

Every round must execute exactly once.

Example output:

```text
Simulation seed: 81724

Round order:
1. competing-causal-branches
2. stale-replica
3. materialised-corruption
4. incomplete-history
5. newer-legitimate-state
```

---

# 15. Cost Accounting

Keep **agent cost** separate from **simulation-control cost**.

The assessment specifically cares about reconciliation cost.

Therefore final reporting should distinguish:

```text
AGENT API COST
```

from:

```text
SIMULATION CONTROL CALLS
```

The agent metrics should continue using the existing V3 instrumentation:

```text
catalogue observation calls
event investigation calls
reconciliation writes
verification reads
request bytes
response bytes
total bytes transferred
API latency
wall-clock time
```

Simulation-only mutation/reset calls should not artificially inflate the reconciliation-agent cost.

They may be separately reported for transparency.

---

# 16. Cumulative Simulation Metrics

Aggregate V3 metrics across all five rounds.

Example:

```text
CUMULATIVE AGENT COST

Agent cycles: 5
API calls: 41
Catalogue reads: 15
Investigation reads: 7
Reconciliation writes: 6
Verification reads: 9
Request bytes: ...
Response bytes: ...
Transferred: ...
API latency: ...
Agent wall-clock time: ...
```

Do not hard-code expected totals unless they naturally follow from the actual implementation.

The final output must report actual measured values.

---

# 17. CLI

Primary command:

```bash
.venv/bin/python -m simulation.runner
```

Optional deterministic seed:

```bash
.venv/bin/python -m simulation.runner --seed 81724
```

Useful optional flags:

```text
--seed
--pause-between-rounds
--json-report
```

Do not overbuild the CLI.

The default command should work without additional arguments.

---

# 18. Terminal Output

The simulation should be easy to understand while being recorded for the assessment.

Suggested structure:

```text
====================================================
LIVE WAREHOUSE RECONCILIATION SIMULATION
====================================================

Seed: 81724

Required rounds: 5

Random order:
1. competing-causal-branches
2. stale-replica
3. materialised-corruption
4. incomplete-history
5. newer-legitimate-state
```

For each round:

```text
====================================================
ROUND 1/5 — COMPETING CAUSAL BRANCHES
====================================================

[BASELINE]
10 products
10 consistent
0 conflicts

[DISTURBANCE]
External warehouse activity injected.

[AGENT]
... existing V3 output ...

[ROUND EVALUATION]

Expected agent outcome: ESCALATED
Actual agent outcome:   ESCALATED

Reconciliation writes: 0
Unsafe writes:          0

ROUND RESULT: PASS

[RESET]

Warehouse baseline restored.
10 products
10 consistent
0 conflicts
```

---

# 19. Final Report

Suggested final output:

```text
====================================================
LIVE SIMULATION COMPLETE
====================================================

Seed: 81724

Required rounds: 5
Executed rounds: 5
Passed rounds: 5
Failed rounds: 0

Automatically resolved: 3
Safely escalated: 2

Unexpected writes: 0
Verification failures: 0
Reset failures: 0

----------------------------------------
CUMULATIVE AGENT COST
----------------------------------------

Agent runs: ...
API calls: ...
Catalogue observations: ...
Event investigations: ...
Reconciliation writes: ...
Verification reads: ...

Request bytes: ...
Response bytes: ...
Total transferred: ...

API latency: ...
Wall-clock time: ...

----------------------------------------
FINAL RESULT
----------------------------------------

SIMULATION: PASS
```

---

# 20. Overall Pass Criteria

The simulation only passes if all of the following are true:

1. All five required disturbance rounds execute exactly once.
2. No round is skipped.
3. No round is duplicated.
4. Every resolvable disturbance ends `RESOLVED`.
5. Every resolved disturbance passes post-write verification.
6. Every escalation disturbance ends `ESCALATED`.
7. Expected escalation rounds perform zero reconciliation writes.
8. No new disturbance begins before the previous round reaches its terminal state.
9. Every resolved environment passes the clean-state check.
10. Every escalated environment is successfully reset before the next round.
11. No simulation-only rule is used by the reconciliation agent.
12. Cost metrics are collected from the real V3 API instrumentation.
13. No unexpected agent crash occurs.
14. All five round evaluations return `PASS`.

Then:

```text
SIMULATION: PASS
```

Otherwise:

```text
SIMULATION: FAIL
```

---

# 21. Failure Behaviour

If a round fails, stop the simulation.

Do not continue executing later rounds because the environment can no longer be trusted.

Print:

```text
SIMULATION ABORTED

Failed round:
materialised-corruption

Expected:
RESOLVED

Actual:
ESCALATED

Reason:
Agent failed to establish supported canonical state.

Completed rounds:
2 / 5

SIMULATION: FAIL
```

Preserve all metrics and evidence collected up to the failure.

---

# 22. JSON Audit Report

Preferably generate a machine-readable report in addition to CLI output.

Example:

```text
simulation/results/<timestamp>-<seed>.json
```

Suggested structure:

```json
{
  "seed": 81724,
  "round_order": [],
  "rounds": [],
  "summary": {},
  "cost": {},
  "result": "PASS"
}
```

This is useful for:

- reproducibility;
- assessment evidence;
- debugging;
- comparing simulation runs.

Avoid introducing a database solely for simulation persistence.

---

# 23. Testing Strategy

## Unit tests

### Disturbance tests

For each disturbance:

- begin from clean baseline;
- inject disturbance;
- inspect warehouse stores/APIs;
- assert the intended conflict exists;
- assert no unrelated SKU changed.

---

### Round evaluator tests

Test:

```text
expected RESOLVED + actual RESOLVED + verification true → PASS
expected RESOLVED + actual ESCALATED → FAIL
expected ESCALATED + actual ESCALATED + zero writes → PASS
expected ESCALATED + actual RESOLVED → FAIL
expected ESCALATED + any PUT → FAIL
```

---

### Random-order tests

Verify:

- all five rounds appear;
- each appears once;
- same seed gives same ordering;
- different seeds can produce different ordering.

---

### Reset tests

Verify:

- dirty environment resets to baseline;
- missing/incomplete history is restored;
- all warehouses expose the same 10 products after reset;
- detector sees zero conflicts.

---

## Integration test

Add one full simulation integration test using a fixed seed.

Example:

```text
seed = 81724
```

Assert:

```text
required_rounds == 5
executed_rounds == 5
passed_rounds == 5
failed_rounds == 0

resolved_rounds == 3
escalated_rounds == 2

overall_result == PASS
```

Also assert no expected escalation generated reconciliation writes.

---

# 24. Docker Strategy

Prefer reusing the existing three warehouse containers.

Enable:

```text
SIMULATION_MODE=true
```

through a Compose override:

```text
compose.simulation.yaml
```

Example usage:

```bash
docker compose \
  -f compose.yaml \
  -f compose.simulation.yaml \
  up --build -d
```

Then:

```bash
.venv/bin/python -m simulation.runner
```

Then:

```bash
docker compose \
  -f compose.yaml \
  -f compose.simulation.yaml \
  down
```

Avoid restarting Docker between every resolvable round.

For escalation rounds, reset warehouse state through the controlled simulation reset mechanism rather than destroying all containers unless implementation simplicity strongly favours container recreation.

---

# 25. Documentation

Add:

```text
simulation/README.md
```

Document:

- why the simulation exists;
- difference between deterministic scenarios and live simulation;
- architecture;
- required rounds;
- random ordering;
- seed reproducibility;
- resolution vs escalation;
- reset behaviour;
- cost accounting;
- run commands;
- pass criteria.

Update the root README with a short:

```text
## Live simulation
```

section and one command to run it.

Do not allow the live-simulation documentation to overwhelm the core assessment explanation.

---

# 26. Implementation Phases

## Phase 1 — Simulation models

Implement:

```text
simulation/models.py
```

Define rounds, outcomes, results, seed, and aggregate report structures.

Tests first or alongside implementation.

---

## Phase 2 — Clean baseline/reset mechanism

Build one deterministic clean-state reset.

Test:

```text
reset
→ 10 products
→ 10 consistent
→ 0 conflicts
```

---

## Phase 3 — Simulation-only mutation interface

Add guarded simulation operations.

Confirm:

- disabled in normal mode;
- enabled only under `SIMULATION_MODE`;
- separate from reconciliation writes.

---

## Phase 4 — Disturbances

Implement the five disturbances individually.

For each:

```text
reset
→ inject
→ inspect warehouse state
→ confirm exact intended conflict
```

Do not involve V3 yet.

---

## Phase 5 — Round evaluator

Implement expected-vs-actual evaluation.

Make PASS/FAIL independent from the agent's RESOLVED/ESCALATED terminology.

---

## Phase 6 — Controller

Implement:

```text
baseline
shuffle
inject
run V3
evaluate
clean/reset
next
```

Ensure round gating is strict.

---

## Phase 7 — Cumulative metrics

Aggregate V3 costs across all rounds.

Keep simulator calls separate.

---

## Phase 8 — CLI/reporting

Implement the readable live output and optional JSON report.

---

## Phase 9 — Automated tests

Run:

```bash
pytest
```

All existing V1/V2/V3/scenario tests must continue to pass.

Then add a fixed-seed full-simulation integration test.

---

## Phase 10 — Real Docker manual run

Run the complete simulation against the real three-container warehouse stack.

Capture:

- seed;
- random round order;
- every disturbance;
- every V3 decision;
- writes;
- verification;
- escalation;
- resets;
- cumulative cost;
- final PASS.

Store the cleaned result in a manual-results file.

---

# 27. Non-Goals

Do not add the following as part of this milestone:

- an LLM;
- autonomous strategy generation;
- infinite daemon execution;
- background scheduling;
- Kafka or message queues;
- persistent SQL databases;
- distributed locking;
- rollback;
- generic retry loops;
- cloud infrastructure;
- Kubernetes;
- scenario-specific production branches;
- a new V4 reconciliation engine.

The purpose is to demonstrate the current V3 agent operating against an evolving environment.

---

# 28. Important Architectural Constraint

The strongest property of this simulation should be:

> The simulator controls **what happens to the warehouses**, but only the V3 agent controls **what to do about it**.

That boundary must remain obvious in both code and documentation.

---

# 29. Expected Final Project Story

After implementation, the repository should demonstrate three levels of confidence:

### Level 1 — Unit/integration tests

Prove individual components and safety behaviour.

### Level 2 — Seven deterministic scenarios

Prove the reconciliation decision space:

```text
direct reconciliation
newer minority investigation
same-version causal repair
multi-conflict orchestration
missing causal evidence
contradictory causal evidence
missing product state
```

### Level 3 — Live evolving simulation

Prove that the same generic V3 agent can repeatedly:

```text
observe
detect
reason
investigate
plan
safety-check
execute or escalate
verify
recover/reset
continue
```

while warehouse conditions change over time and round order is unknown in advance.

This should be the final major functionality milestone before repository polish,
fresh-clone validation, demo recording, and submission.
