# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Typed composition inspection shared by CLI and admin, with service, plugin, route,
  configuration and provider inspection commands.
- Request correlation context, opt-in JSON logging, cumulative latency buckets and
  explicit cancelled/disconnected/failed request outcomes.
- Structured JSON environment settings, bounded configuration file loading and
  deterministic nested overrides.
- Operational documentation, concurrency ownership ADR and regression suites covering
  resource races, startup/shutdown ownership, admin extensions and ASGI protocol failures.

### Fixed

- Independent dependency scopes no longer serialize through the root construction lock.
  Inherited child-task context cannot bypass factory ownership.
- Resource closure waits for acquisition, shares concurrent closes and attempts every
  exit with individual deadlines and aggregated failures.
- Complete startup is atomic against shutdown; reentrant lifecycle calls fail promptly.
- Plugin setup can register routes through real ASGI lifespan startup.
- Overload and error-response disconnects are included in request diagnostics.
- Concurrent health probes share work and update per-service health snapshots.
- Admin extension failures are isolated; all runtime admin responses prohibit caching.
- ASGI test helpers detect lifespan crashes and invalid response framing.
- Installed CLI entry points load explicit application targets from the working directory;
  a subprocess regression exercises the console script independently of pytest imports.
- Incorrect CI action pins replaced with verified upstream commits; workflows use the
  lockfile, enforce coverage, audit dependencies and build release provenance artifacts.

### Initial foundation

- Initial Core architecture, contracts, lifecycle, service registry, container, events, routing,
  ASGI runtime, security primitives, plugin contracts, and project governance foundation.
