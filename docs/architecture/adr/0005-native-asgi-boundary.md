# Native ASGI boundary

- Status: Accepted
- Date: 2026-10-01

## Problem

The project charter originally named FastAPI as Orbit's web foundation. The current Core instead
owns its ASGI application and routing contracts. The project needs one unambiguous direction so
that implementation, documentation, and future API decisions do not drift between those models.

## Decision

Orbit Core keeps its native, provider-neutral ASGI layer and owns the routing boundary used by Orbit
applications. Core will not add FastAPI, Starlette, Litestar, or another web framework as a runtime
dependency or wrap one as its primary application model. This decision supersedes the earlier
FastAPI wording in the project prompt.

Uvicorn remains the development server and supported direct single-worker host. Production uses
Gunicorn as the process manager with `uvicorn_worker.UvicornWorker` hosting the same Orbit ASGI
application. Hosting processes and sockets remain the server's responsibility; application
composition, request orchestration, and lifecycle ownership remain Orbit's.

## Alternatives considered

- **Adopt FastAPI.** Rejected because the project owner explicitly chose to keep Orbit's native ASGI
  layer; adopting it would replace that boundary and add a separate framework's contracts.
- **Wrap FastAPI while keeping Orbit's router.** Rejected because maintaining two application and
  routing models would duplicate the Core boundary without a stated need.
- **Defer the choice.** Rejected because the current implementation and charter already use native
  ASGI, and leaving the original prompt's FastAPI wording unresolved invites accidental dependency
  and API drift.

## Consequences

- Orbit owns the ongoing correctness, security, protocol compatibility, and maintenance burden of
  its ASGI and routing implementation. Tests and guides must make those responsibilities explicit.
- Applications use Orbit's route and ASGI APIs; FastAPI-specific extensions are not part of the Core
  compatibility contract.
- This is a Core architecture decision, not a plugin implementation or a restriction on unrelated
  optional capabilities.
- The supported process topology and commands are documented in the
  [hosting ADR](0003-unified-hosting.md), [ASGI guide](../../runtime/asgi.md), and
  [deployment runbook](../../deployment/README.md).
