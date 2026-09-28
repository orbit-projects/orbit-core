# Orbit Core documentation

Orbit Core is the orchestration layer. Read the architecture and plugin boundary before extending
it or adding a provider integration. The documentation distinguishes guarantees made by Core from
behavior that must be supplied and validated by plugins.

- [Architecture overview](architecture/overview.md)
- [Principles](architecture/principles.md)
- [Project charter](architecture/project-charter.md)
- [Application](concepts/application.md)
- [Services](concepts/services.md)
- [Lifecycle](concepts/lifecycle.md)
- [Configuration](concepts/configuration.md)
- [Dependency injection](concepts/dependency-injection.md)
- [Routing](concepts/routing.md)
- [Events](concepts/events.md)
- [State](concepts/state.md)
- [Reliability](concepts/reliability.md)
- [Health and readiness](health/README.md)
- [ASGI runtime](runtime/asgi.md)
- [Background tasks](runtime/tasks.md)
- [Production deployment](deployment/README.md)
- [Administrative surface](admin/README.md)
- [Operator CLI](cli/README.md)
- [Plugin authoring guide](plugins/authoring.md)
- [Observability contracts](observability/README.md)
- [Security contracts](security/overview.md)
- [Development guide](development/README.md)
- [Documentation conventions](development/documentation.md)
- [Public API stability](development/api-stability.md)
- [Repository governance](development/repository-governance.md)
- [Project governance](../GOVERNANCE.md)
- [Core release gate](development/completion.md)
- [API reference guide](reference.md)
- [Public roadmap](../ROADMAP.md)

## Reading order

Start with the [project charter](architecture/project-charter.md), then read the
[architecture overview](architecture/overview.md) and [principles](architecture/principles.md).
Read [application composition](concepts/application.md)
and [lifecycle](concepts/lifecycle.md) before writing a service. Read the
[plugin authoring guide](plugins/authoring.md) before adding a provider integration. Use the
runtime, security, admin, observability, and deployment guides to understand the operational
boundaries that Core expects a host or plugin to complete.

The guides describe the stable contract surface. API docstrings describe individual arguments,
return values, and failure behavior; the ADRs explain decisions that should not be changed without
architectural review. Core's in-memory implementations are reference implementations for local
operation and contract testing, not claims of distributed durability.
