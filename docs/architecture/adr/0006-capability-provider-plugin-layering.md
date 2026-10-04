# Capability and provider-plugin layering

- Status: Accepted as an ecosystem direction; detailed capability contracts remain future work.
- Date: 2026-10-03

## Context

Orbit Core must stay small and provider-neutral, while application developers should not have to
learn a different public API for every database, broker, telemetry system, or other integration.
Some capabilities can achieve that consistency with a single optional plugin. Others may benefit
from a reusable capability package plus provider-specific adapter/plugin implementations.

Core's existing plugin runtime provides generic metadata, dependency ordering, capability
declarations, setup, and lifecycle hooks. It does not itself define every capability API or a
universal plugin-to-plugin adapter protocol.

## Decision

Keep Core as the shared orchestration and extension-contract foundation. Allow the ecosystem to
organize optional functionality either as a direct plugin or as layers. The architectural
extension direction and the package dependency direction are different:

```text
Extension layers:   Orbit Core -> capability package -> provider adapter/plugin
Dependencies:       provider adapter/plugin -> capability API and Core contracts
```

When a capability uses layers, the capability package is a distinct optional distribution. It owns
the stable user-facing API and the contract its provider adapters must implement. A provider
adapter/plugin is another optional distribution; it owns SDK use, credentials and configuration,
provider transport and lifecycle, and translation of provider-specific failures. Applications
should get consistent capability-level configuration and behavior across implementations, subject
to each provider's documented differences. A provider package must not replace or fork the
capability contract it claims to implement.

This is an architectural rule, not a universal adapter protocol. The project catalog supplies
planned package names, but a name does not establish that a package, repository, or capability
contract exists. Define and version each capability contract, provider selection and conflict rules,
dependency relationship, and contract test suite before shipping plugins that depend on it. Those
details belong to the capability ecosystem unless a demonstrated requirement calls for a new Core
contract.

## Consequences

- No provider SDK or capability implementation is added to Core by this decision.
- Core plugin dependencies and capability declarations can support generic composition, but do not
  by themselves guarantee compatibility between a provider plugin and a capability package.
- Plugin documentation must distinguish Core guarantees from future capability-level guarantees.
- A direct plugin remains appropriate when another abstraction layer would add no meaningful
  uniformity or reuse.
- Compatibility and adapter-contract tests must live with the capability/provider ecosystem and
  run against supported implementations.

## Compatibility notes

This decision does not change the public Python API or `CORE_API_VERSION`. Any future change to
Core's plugin contract or metadata needed to support layered composition requires a separate
compatibility review and migration guidance.
