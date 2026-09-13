# Core completion criteria

The Core release gate covers:
- dependency graph validation, scope isolation, resource cleanup and overrides;
- application and component state, concurrent lifecycle calls, timeout and cancellation;
- plugin metadata validation, explicit discovery, dependency ordering and cleanup;
- typed configuration composition, secret redaction, state validation and event delivery;
- ASGI request/response framing, disconnects, size/time limits, route dispatch and security;
- current health/readiness, diagnostics and an authenticated administrative surface;
- working CLI, examples, integration helpers, documentation and package builds;
- lint, strict typing, behavioral tests, coverage and security automation.

Passing local checks is evidence about the implementation, not a production certification.
Provider packages are not part of this Core task. Hosted repository governance, actual
security scan results and release provenance remain deployment gates.
