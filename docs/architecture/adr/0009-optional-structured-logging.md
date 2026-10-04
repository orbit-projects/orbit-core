# ADR 0009: Keep structured log formatting optional

- Status: Accepted
- Date: 2026-10-04

## Context

Orbit Core owns request/application context and backend-neutral diagnostics. A JSON formatter is
useful, but it is an optional output convention for Python's standard logging system and is not
required for orchestration or runtime operation. Keeping it in Core made a presentation choice
appear mandatory and expanded Core's public API for a capability not every application needs.

## Decision

Move the JSON formatter into the separately installable `orbit-logging` distribution. The package
depends on Core's public runtime-context accessors and selects only stable, explicit log fields.
It does not configure the root logger or add a logging backend, exporter, or transport. Core
continues to own diagnostic snapshots, telemetry contracts, and task-local request/trace context.

## Consequences

- Applications that need structured JSON logs install `orbit-logging` and attach its formatter to
  an application-owned handler.
- Applications that do not need that output format carry no Orbit logging integration dependency.
- The Core `orbit.diagnostics.JSONFormatter` export is removed; the new import is
  `orbit_logging.JSONFormatter`.
- Provider-specific logging exporters and remote transports remain separate integrations.
