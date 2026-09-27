# Architecture Decision Records

Architecture Decision Records (ADRs) capture decisions that change a Core convention, dependency
direction, public contract, or extension model. They are the durable explanation for why a design
looks the way it does, especially when a future implementation could appear simpler but violate
ownership or failure guarantees.

## When to add an ADR

Write an ADR before merging a change that introduces a new public subsystem, changes lifecycle or
concurrency semantics, adds a dependency, changes the plugin boundary, or alters hosting and
deployment behavior. Small bug fixes belong in code comments and changelog entries instead.

## Required sections

Each ADR should state the status and date, the problem, the decision, alternatives considered when
they materially affect the trade-off, and consequences. Include migration or compatibility notes
when existing plugins or applications could observe the change. Link the relevant tests and guides;
an ADR explains the decision, while the guide explains how to use it.

## Current decisions

- [Core runtime contracts](0001-core-runtime-contracts.md)
- [Runtime ownership and concurrency](0002-runtime-ownership.md)
- [Unified Uvicorn and Gunicorn hosting](0003-unified-hosting.md)

ADRs describe the pre-release Core policy and do not certify a provider plugin or a deployment.
