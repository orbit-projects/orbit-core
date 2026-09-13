# Architecture overview

Orbit Core is a single foundational package. It owns the service model, application orchestration, typed configuration and state, lifecycle, dependency injection, event contracts, routing, minimal ASGI runtime, identity contracts, and plugin runtime contracts.

Provider technology never enters Core directly. A capability first receives an adapter package named `orbit-<capability>`; provider implementations then use `orbit-<capability>-<provider>`.

Maintained Python files begin with Orbit's full Apache-2.0 comment header: a `2026-present Orbit
Contributors` copyright line followed by the license notice. The repository checks this convention.
