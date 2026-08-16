# Live warehouse simulation

The live simulation demonstrates the existing V3 reconciliation agent against
warehouse state that changes between agent runs. The simulator creates external
warehouse disturbances through guarded HTTP endpoints; it never tells V3 which
disturbance occurred and never chooses a reconciliation action.

This differs from the seven deterministic scenarios. Static scenario files
start the services in a known conflict state. The live simulation starts all
three services clean, injects five disturbances in a seeded random order, runs
V3 once for each disturbance, and independently judges its terminal outcome.

## Architecture

```text
simulation controller
  -> simulation-only warehouse HTTP operations
  -> warehouse A / B / C
  -> unchanged V3 observation and reconciliation lifecycle
  -> typed RESOLVED or ESCALATED outcome
  -> independent simulation PASS or FAIL evaluation
```

The simulation package is separate from `agent/`. V3 continues to derive every
decision from catalogue observations, conflicts, evidence, event history,
policy, safety checks, execution results, and verification results.

The warehouse simulation routes exist only when `SIMULATION_MODE=true`:

- `POST /simulation/reset`
- `POST /simulation/inventory/{sku}/event`
- `POST /simulation/inventory/{sku}/corrupt`
- `POST /simulation/inventory/{sku}/history`

Normal warehouse mode does not register these routes, so they return `404`.
The simulator uses a separate HTTP client and separately reports its reset,
mutation, and clean-observation costs. These calls never enter V3's agent-cost
metrics.

## Required rounds

| Disturbance | Expected V3 outcome | Safety condition |
| --- | --- | --- |
| `stale-replica` | `RESOLVED` | stale warehouse is reconciled and verified |
| `newer-legitimate-state` | `RESOLVED` | newer causal extension wins over the older majority |
| `materialised-corruption` | `RESOLVED` | causal replay drives a shared repair revision |
| `incomplete-history` | `ESCALATED` | zero reconciliation writes |
| `competing-causal-branches` | `ESCALATED` | zero reconciliation writes |

The five rounds are shuffled once with `random.Random(seed).shuffle(...)`; no
round is selected with replacement. The seed and full order are printed.

Each round is strictly gated. After `RESOLVED`, a fresh read-only observation
must report 10 products, 10 consistent, and 0 conflicts. After `ESCALATED`, the
simulator first verifies that escalation was expected and made zero writes,
then resets every warehouse to the one canonical default catalogue and performs
the same clean observation. A failed outcome, verification, reset, or clean
check stops the simulation immediately.

`RESOLVED` and `ESCALATED` describe the agent outcome. `PASS` and `FAIL` describe
whether the simulator judged that outcome safe and correct for the injected
disturbance.

## Run with Docker

Start three clean warehouse APIs with simulation controls enabled:

```bash
docker compose \
  -f compose.yaml \
  -f compose.simulation.yaml \
  up --build -d
```

Run with an automatically generated seed:

```bash
.venv/bin/python -m simulation.runner
```

Reproduce an order with a printed seed:

```bash
.venv/bin/python -m simulation.runner --seed 81724
```

Optionally write the typed result as JSON:

```bash
.venv/bin/python -m simulation.runner \
  --seed 81724 \
  --json-report simulation/results/demo-81724.json
```

Stop the warehouse APIs:

```bash
docker compose \
  -f compose.yaml \
  -f compose.simulation.yaml \
  down
```

The final report includes every round outcome, reset/clean checks, cumulative
V3 calls, investigations, writes, verification reads, bytes, API latency, wall
time, and separately accounted simulation-control calls.
