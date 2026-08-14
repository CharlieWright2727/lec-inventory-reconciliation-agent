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
- focused warehouse API tests.

Not yet implemented:

- scenario selection and loading;
- the reconciliation agent and its decision logic;
- cost instrumentation;
- a CLI demonstration runner.

The three warehouse containers share one implementation and image while using separate identities and independent in-memory state.
