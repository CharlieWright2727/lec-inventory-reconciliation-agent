# Inventory Reconciliation Agent

I built this project for the LEC AI Engineering Intern assessment.

It compares inventory from three warehouse APIs, finds stock that does not
match, works out which state is supported by the available evidence, and either
fixes the warehouses or safely escalates the conflict. After a fix, it reads the
warehouses again to check that they really agree.

The project includes:

- 3 independent warehouse APIs;
- 7 fixed test scenarios;
- a live simulation with 5 changing disturbances;
- V1, V2 and V3 versions of the agent;
- real API-call, byte and timing measurements.

The agent is deterministic. It does not use an LLM to make reconciliation
decisions, so the same input always produces the same decision and the reason
can be shown clearly.

## What it does

For each run, the agent:

1. Reads the catalogue from every warehouse.
2. Finds products where stock, version, event progress, identity or coverage
   does not match.
3. Builds evidence from what it observed.
4. Reads event history only when the catalogue is not enough to make a safe
   decision.
5. Chooses to reconcile or escalate each conflict.
6. Builds and checks a version-aware update plan.
7. Sends safe updates one at a time.
8. Reads every warehouse again to check that the update worked.
9. Prints the result and the measured cost of the run.

The command-line entry point runs V3, which is the complete version.

## Architecture

```text
Warehouse A ─┐
Warehouse B ─┼──> OBSERVE ──> DETECT ──> EVIDENCE
Warehouse C ─┘                              │
                                           ├── not enough ──> INVESTIGATE ─┐
                                           │                              │
                                           └── enough ────────────────────┤
                                                                          v
                                                            RECONCILE / ESCALATE
                                                                     │
                                                             if reconcile
                                                                     v
                                                      SAFETY ──> EXECUTE ──> VERIFY
                                                                     │
                                                                     v
                                                                COST REPORT
```

The warehouses all use the same FastAPI code, but Docker starts them as three
separate processes with separate in-memory stores. The agent can only see their
HTTP APIs. It does not read scenario files or simulation state.

Most of the decision logic is kept separate from network code:

```text
agent/client.py     HTTP calls and call measurements
agent/detector.py   factual differences between warehouses
agent/evidence.py   agreement, progress and event-history evidence
agent/policy.py     reconcile, investigate or escalate decision
agent/planner.py    explicit read and write plans
agent/safety.py     checks the whole SKU plan before writing
agent/executor.py   performs validated writes
agent/verifier.py   reads the warehouses again after writing
agent/runner.py     joins the stages together
```

## Reconciliation strategy

I did not use simple majority voting because two matching warehouses can both
be behind a newer warehouse. I also avoided averaging stock, because that could
create a number that never existed, and I did not use timestamps alone because
clocks can be wrong or out of sync.

The agent instead uses these rules:

- If two warehouses fully agree and the third is behind in both version and
  event progress, the third warehouse can be moved forward directly.
- If one warehouse is newer, it is not overwritten just because it is in the
  minority. The agent reads its event history and checks whether later events
  explain its stock.
- If stock differs at the same version, the agent replays the event histories.
  If one value is supported, all warehouses move to a new shared repair version.
  The old version is never silently given a different meaning.
- If a product is missing, identity does not match, history is incomplete, or
  two valid event branches disagree, the agent escalates without guessing.

This means some difficult conflicts are deliberately left for a person to
review. That is safer than writing a stock number that the evidence cannot
support. Event history is only requested when it can change the decision, which
keeps simple runs cheaper.

## Quick start

You need Python 3.11 or newer, Docker and Docker Compose.

```bash
git clone https://github.com/CharlieWright2727/lec-inventory-reconciliation-agent.git
cd lec-inventory-reconciliation-agent

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Run all automated tests:

```bash
pytest
```

Start the default warehouse scenario:

```bash
docker compose up --build -d
```

Wait for the three APIs if needed:

```bash
until curl -fsS http://localhost:8001/health >/dev/null \
  && curl -fsS http://localhost:8002/health >/dev/null \
  && curl -fsS http://localhost:8003/health >/dev/null; do
  sleep 1
done
```

Run the agent and then stop Docker:

```bash
python -m agent.runner
docker compose down
```

## Running V3

The default scenario has Warehouse A and C at version 42 while Warehouse B is
still at version 41. V3 should recognise that B is stale, update it, read all
three warehouses again, and report:

```text
SKU-001: RESOLVED
```

Before stopping Docker, run the agent a second time:

```bash
python -m agent.runner
```

The second run should find all 10 products consistent and perform no PUT
requests. This shows that the result is stable and the agent does not keep
writing the same state.

The three programmatic entry points are:

- `run_agent()` — V1 observation and conflict detection;
- `run_agent_v2()` — read-only evidence and decisions;
- `run_agent_v3()` — planning, writing and verification.

## Live warehouse simulation

The live simulation starts with clean warehouses and injects five different
problems in a seeded random order. It uses the real V3 agent without giving the
agent any information about which disturbance was injected.

Each round must:

1. start from a checked clean state;
2. create exactly one intended conflict;
3. produce the expected resolved or escalated result;
4. perform no writes when escalation is expected;
5. finish with a clean environment before the next round.

Run it with:

```bash
docker compose \
  -f compose.yaml \
  -f compose.simulation.yaml \
  up --build -d

python -m simulation.runner
python -m simulation.runner --seed 81724

docker compose \
  -f compose.yaml \
  -f compose.simulation.yaml \
  down
```

An optional JSON report can also be written:

```bash
python -m simulation.runner \
  --seed 81724 \
  --json-report simulation/results/demo-81724.json
```

More detail is in [simulation/README.md](simulation/README.md).

## Deterministic scenarios

There are seven fixed scenarios. These make it easy to reproduce the same
starting state and inspect the exact decision made by V3.

| Scenario | What it tests | Expected V3 result |
| --- | --- | --- |
| `one-stale-warehouse` | One replica is clearly behind | `RESOLVED` |
| `newer-singleton` | A newer minority may be correct | `RESOLVED` |
| `same-version-divergence` | Event replay is needed at the same version | `RESOLVED` |
| `mixed-conflicts` | Three different conflict types in one run | Three `RESOLVED` |
| `incomplete-event-history` | The causal link cannot be proved | `ESCALATED`, 0 writes |
| `competing-newer-states` | Two valid newer branches disagree | `ESCALATED`, 0 writes |
| `missing-sku` | Missing stock is unknown, not zero | `ESCALATED`, 0 writes |

The default scenario uses `compose.yaml`. Other scenarios use a small Compose
override. For example:

```bash
docker compose \
  -f compose.yaml \
  -f compose.newer-singleton.yaml \
  up --build -d

python -m agent.runner

docker compose \
  -f compose.yaml \
  -f compose.newer-singleton.yaml \
  down
```

## Testing and results

Run the complete suite with:

```bash
pytest
```

The 121 tests cover the warehouse APIs, input validation, conflict detection,
evidence extraction, policy decisions, investigation, planning, safety checks,
concurrent changes, write failures, verification, all seven scenarios, and the
complete live simulation.

The automated tests were generated with the help of OpenAI Codex as part of
each implementation stage. I did not treat generated tests as proof
on their own. I reviewed them against the written requirements and scenario
expectations, checked that they covered both successful and unsafe cases, and
then compared them with manual API, Docker and end-to-end runs. The saved manual
outputs below provide a separate record of the system actually running.

| Check | Result |
| --- | --- |
| Automated tests | 121 PASS |
| Deterministic scenarios | 7 covered |
| Mixed-conflict Docker run | PASS |
| Live simulation | 5 / 5 rounds PASS |
| Automatically resolved live rounds | 3 |
| Safely escalated live rounds | 2 |
| Unexpected writes during escalation | 0 |
| Verification failures | 0 |
| Reset failures | 0 |

The recorded manual runs are stored in [tests/manual](tests/manual). They include
API checks, V1, V2, the V3 scenarios and the full seeded simulation.

## Cost reporting

Every warehouse request made by the agent is measured. The output shows:

- the number of API calls;
- successful and failed calls;
- catalogue reads, event reads, writes and verification reads;
- request-body and response-body bytes;
- total API latency;
- total wall-clock time.

One recorded five-round simulation produced:

```text
Agent runs: 5
API calls: 37
Catalogue observations: 15
Event investigations: 7
Reconciliation writes: 6
Verification reads: 9

Request bytes: 1,875
Response bytes: 136,747
Total transferred: 138,622 bytes

API latency: 129.04 ms
Agent wall-clock time: 70.22 ms
```

These numbers are HTTP payload bytes rather than lower-level network overhead.
The simulator's reset, disturbance and observation calls are measured
separately, so they do not make the agent appear more expensive than it is. The
full output is in
[tests/manual/live_simulation_results.txt](tests/manual/live_simulation_results.txt).

## Safety and failure behaviour

- Every write says which version the agent originally observed. If that version
  has changed, the warehouse rejects the write.
- Versions cannot move backwards.
- Different stock cannot be written while keeping the same version.
- The full plan for a SKU is checked before its first write.
- Writes happen one at a time and stop after the first failure.
- There is no automatic rollback. A partial result is recorded and escalated.
- A successful PUT is not enough. The agent independently reads every warehouse
  again and checks the result.
- Missing or contradictory evidence causes escalation instead of a guess.
- `ESCALATED` means the agent finished safely. It does not mean the program
  crashed.

## How I built it

I used two AI-assisted tools during development. My usual private planning
workflow is built around Obsidian and a local Ollama model,
`lfm2.5:latest`. I used it to organise project notes, explore early ideas and
help shape the detailed implementation briefs. I then used OpenAI Codex as the
agentic coding service for the bounded implementation stages, automated tests,
repository reviews and command-line validation.

This distinction is important: the local model helped with private planning and
Codex helped implement and review the repository. Neither model is part of the
running reconciliation agent. Runtime reconciliation is deterministic and does
not send warehouse data to an LLM.

I used a staged workflow rather than asking Codex to build the complete project
from one large prompt.

```text
Understand the task
        ↓
Write schemas, rules and API notes
        ↓
Build a small hand-written warehouse demo
        ↓
Run it manually and record the output
        ↓
Write a detailed plan for the next agent version
        ↓
Use Codex to implement that bounded stage
        ↓
Review the diff, code paths, tests and Docker behaviour
        ↓
Fix or clarify anything found
        ↓
Commit only after the stage works end to end
```

### 1. Plan the data and rules first

I started by writing down the warehouse record shape, API behaviour and update
rules. This made the expected behaviour clear before the reconciliation agent
existed.

The main documents were:

- [warehouse_schema.md](md/warehouse/warehouse_schema.md);
- [warehouse_rules.md](md/warehouse/warehouse_rules.md);
- [warehouse_api_queries.md](md/warehouse/warehouse_api_queries.md);
- [warehouse_scenario_framework.md](md/warehouse/warehouse_scenario_framework.md).

### 2. Use small hand-written demos

I first built and ran small pieces manually: one warehouse API, then three
services, then one known stale-warehouse conflict. I used direct API calls and
short agent runs to check the data before adding more reasoning.

The outputs were kept instead of discarded. They are now under
[tests/manual](tests/manual), so the development steps and real Docker results
can still be inspected.

### 3. Build the agent in stages

The agent was split into three versions:

- V1 only observed the catalogues and reported facts.
- V2 added evidence, selective investigation and read-only decisions.
- V3 added safe update plans, execution and independent verification.

Before each stage, I wrote a detailed Markdown implementation brief describing
the boundaries, data models, expected behaviour, failure cases, costs and tests.
Those documents gave Codex a smaller and more precise task instead
of leaving it to invent the design.

Codex also generated the automated tests for each stage from those briefs and
acceptance criteria. I reviewed the tests alongside the implementation to make
sure they checked required behaviour rather than simply matching the generated
code.

The stage documents are:

- [agent_plan.md](md/agent/agent_plan.md);
- [agent_v1.md](md/agent/agent_v1.md);
- [agent_v2.md](md/agent/agent_v2.md);
- [agent_v3.md](md/agent/agent_v3.md);
- [cost_tracking_plan.md](md/agent/cost_tracking_plan.md).

The large [warehouse_simulation_plan.md](md/warehouse/warehouse_simulation_plan.md)
was used in the same way for the live simulation. It is kept as implementation
history; it is not required to run the project.

### 4. Review before committing

After each implementation stage, I reviewed more than whether the happy path
ran. The review included:

- reading the generated diff and tracing the full runtime path;
- checking that the agent did not contain scenario names or SKU-specific rules;
- running the complete automated test suite;
- running the relevant scenario through Docker;
- checking failure and escalation paths as well as successful writes;
- comparing the planned API calls with the measured cost output;
- running the agent a second time to prove convergence and no repeated writes;
- updating the plan or documentation when the implementation had changed.

This review-and-test step happened before commits so each version represented a
working stage rather than a large unverified code drop.

## Limitations

- The warehouses are local in-memory services rather than real warehouse
  providers.
- Event replay currently proves `on_hand`. Reservation changes do not yet have
  the same detailed event meaning.
- There is no distributed lock for multiple agent processes running at once.
- A canonical source is not read again immediately before target writes. If it
  changes during a run, verification catches the mismatch afterward, but partial
  writes may need operator review.
- Run evidence and cost records are not stored in an external durable audit
  database.
- Escalated conflicts require a person or another business process to decide
  what should happen next.
- The assessment uses three warehouses, although most agent functions accept a
  collection of endpoints.

## What I would do next with more time

- Store decisions, evidence, writes, verification and cost in durable audit
  storage.
- Revalidate the canonical source immediately before executing a plan.
- Add distributed coordination for multiple reconciliation workers.
- Add richer events for reservations, allocations, returns and orders.
- Connect to authenticated external warehouse APIs.
- Add production dashboards and alerts for escalated conflicts.
- Add an operator workflow for reviewing and resolving escalations.

## Project structure

```text
agent/       V1, V2 and V3 reconciliation code
warehouse/   FastAPI warehouse service and in-memory state
scenarios/   seven fixed warehouse scenarios
simulation/  seeded live disturbance runner and independent checks
tests/       automated tests, support code and saved manual output
md/          planning, implementation briefs and design notes
```

For more detail, the best places to continue are:

- [V3 design](md/agent/agent_v3.md);
- [live simulation guide](simulation/README.md);
- [warehouse scenario framework](md/warehouse/warehouse_scenario_framework.md).
