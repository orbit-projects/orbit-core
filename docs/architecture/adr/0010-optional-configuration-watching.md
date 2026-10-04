# ADR 0010: Keep configuration file watching optional

- Status: Accepted
- Date: 2026-10-04

## Context

Core owns typed configuration composition, immutable snapshots, lifecycle coordination, and the
atomic observer-approved extension reload contract. It also contained a concrete polling watcher
that read TOML files and ran a background task. This contradicted the documented boundary that
file watching is a host/development responsibility and made filesystem polling part of Core's
public API.

## Decision

Move the concrete TOML polling watcher to the separately installable `orbit-devtools` package.
Core keeps the small `ConfigurationWatcher` lifecycle protocol and `Application.register_config_watcher`
ownership hook so optional tools participate in safe application startup and reverse cleanup. The
watcher uses Core's public configuration loader and `Config.reload_section` path.

## Consequences

- Core applications do not poll files unless they explicitly install and register a watcher.
- File reads and parsing in the optional watcher run in worker threads, not on the ASGI event loop.
- The Core `orbit.config.ConfigWatcher` implementation/export is removed; the new import is
  `orbit_devtools.ConfigWatcher`.
- The Core extension reload contract remains available to other configuration sources and plugins.
