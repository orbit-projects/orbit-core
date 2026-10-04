# ADR 0013: Keep the ASGI test client in an optional package

- Status: Accepted
- Date: 2026-10-04

## Context

Core shipped an in-process ASGI test harness used by Orbit's tests and plugin suites. It does not
participate in application runtime, but it was included in every Core installation. Extracting it
also exposed that the harness relied on Core's internal ASGI message aliases and request limits,
which are useful stable contracts for middleware and tooling integrations.

## Decision

Move TestClient and TestResponse to the separately installable orbit-testing distribution, imported
as orbit_testing. Keep the harness based on Core's native ASGI pipeline; do not add a second web
framework or network client. Promote Headers, ASGI message/callable aliases, and the path-byte bound
to orbit.asgi so the external package can use public contracts rather than private Core modules.
Core tests use the sibling package as a development-only dependency; the Core wheel packages only
orbit.

## Consequences

- Applications and plugin authors that want the in-process harness install orbit-testing
  explicitly and import from orbit_testing.
- Core runtime users do not install test utilities unless selected; Core does not re-export a
  compatibility shim from orbit.testing.
- The Core ASGI public API gains typed protocol aliases and a validated header type used at the
  framework boundary.
- This is a pre-release public import relocation. CI checks out orbit-projects/orbit-testing
  alongside Core so the local workspace dependency resolves; hosted validation requires that
  sibling repository to exist and be readable.

## Evidence

- orbit-testing/tests/test_client_contract.py
- Core public API and packaging-boundary tests
- docs/runtime/asgi.md
- docs/development/README.md
