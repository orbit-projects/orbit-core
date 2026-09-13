# Orbit Core

Orbit is a type-safe, service-oriented framework for orchestrating cloud-native applications.

`orbit-core` contains the foundational runtime: application orchestration, services, lifecycle,
dependency injection, configuration, state, events, routing, and the contracts that plugins use to
extend it. Provider-specific integrations belong in separately versioned adapter and implementation
packages.

## Status

This repository establishes Orbit's first public architecture and a minimal, tested orchestration
vertical slice. It is pre-alpha and its APIs may change before the first stable release.

## Install for development

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Quick example

```python
from orbit import Application, ApplicationSettings, Service, ServiceDescriptor


class GreetingService(Service):
    descriptor = ServiceDescriptor(name="greeting")


app = Application(ApplicationSettings(name="example"))
app.register(GreetingService())
```

See [the architecture guide](docs/architecture/overview.md) and [ADRs](docs/architecture/adr/) before extending Core.
