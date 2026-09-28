# Public API stability and compatibility

Orbit Core treats its public Python API as an orchestration contract. The package is currently
pre-release, so the project may still make breaking changes before a stable `1.0` release, but
changes must be deliberate, documented, tested, and visible to adopters.

## What is public

The supported surface is the root package and the names exported by the focused packages listed in
the [API reference](../reference.md). Exported classes, functions, protocols, Pydantic models,
enums, exception types, identifiers, and documented behavior are public contracts. Their type
signatures, validation rules, lifecycle ownership, error categories, redaction guarantees, and
cancellation behavior are part of compatibility—not merely implementation details.

`tests/unit/test_public_api.py` contains the explicit export manifest for those focused packages.
A public export addition or removal must update that manifest, the API reference, and the owning
docstrings in the same change; the test intentionally fails when the live `__all__` drifts.

Underscore-prefixed modules and members, test helpers, in-memory implementation details, and
unexported names are private. They may change without a compatibility promise. An application or
plugin must not depend on private names to avoid a missing public contract; missing capability is
a reason to propose a contract change.

## Versioning

The package release version follows semantic-versioning intent:

- pre-`1.0` releases may contain documented breaking changes in minor releases, but patch releases
  should remain compatible whenever practical;
- after `1.0`, breaking public behavior requires a major release and migration notes;
- security fixes may require an exceptional compatibility change when retaining the old behavior
  would leave users unsafe;
- `orbit.plugins.CORE_API_VERSION` versions the extension contract independently from the package
  release. An incompatible plugin contract must change that value and document the migration.

The package version and Core extension-contract version must not be used interchangeably. A plugin
should declare the Core API label it supports and its own semantic version separately.

## Deprecation process

When a public name or behavior must be replaced:

1. document the replacement and reason in the owning guide;
2. add a changelog entry and migration note;
3. retain the old API when technically safe and emit a targeted `DeprecationWarning` from the
   deprecated path;
4. add a regression test for both the replacement and the warning;
5. remove the deprecated API only at the next release boundary allowed by the versioning policy.

Deprecations must not expose credentials, request data, provider payloads, or unbounded values in
warning text. If a safe deprecation period is impossible because of a security issue, explain the
exception in the changelog and release notes.

## Change review requirements

A public contract change requires:

- a focused regression test through the public API;
- updated module/class/function docstrings and the nearest concept guide;
- migration notes when callers, adapters, or operators can observe the change;
- an ADR when ownership, lifecycle, concurrency, hosting, dependency direction, or security
  assumptions change;
- compatibility checks across every supported Python version and the documented Uvicorn or
  Gunicorn hosting path when the change affects runtime behavior.

Core must remain provider-neutral. Adding a database, broker, cloud SDK, identity provider, or
telemetry backend is not a Core API stabilization change; those belong in separately versioned
plugins or adapters.
