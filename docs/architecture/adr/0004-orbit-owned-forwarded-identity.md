# Orbit-owned forwarded identity

- Status: Accepted
- Date: 2026-09-28

## Problem

Reverse proxies commonly add `X-Forwarded-*` or `Forwarded` fields. Uvicorn can also
rewrite the ASGI client peer when its host-level proxy middleware trusts the connection.
If that rewrite occurs before Orbit receives the ASGI scope, Core cannot distinguish a
validated forwarded address from malformed or attacker-controlled input. This would make
the configured `trusted_proxies` policy non-authoritative.

## Decision

Orbit's supported Uvicorn and Gunicorn entry points disable host-level proxy identity
rewriting. Direct Uvicorn uses `proxy_headers=False` and an empty `forwarded_allow_ips`
allow-list. Gunicorn passes an empty `--forwarded-allow-ips` value to
`uvicorn_worker.UvicornWorker`. Orbit Core then owns forwarded identity validation using
the immediate ASGI peer, configured CIDR networks, and the typed forwarded-header rules.

Manual deployments must preserve this boundary by keeping the host proxy allow-list empty.

## Alternatives considered

- Trusting Uvicorn's default loopback allow-list: rejected because it can rewrite malformed
  forwarded values before Core validates them.
- Duplicating host-specific proxy parsing in Core: rejected because the host has already
  discarded the original peer and it would not be portable across ASGI servers.
- Always ignoring forwarded headers: rejected because explicitly configured reverse-proxy
  deployments need a typed, fail-closed client identity contract.

## Consequences

Applications configure proxy trust once in Orbit Core. A manually configured host that
re-enables proxy rewriting is outside the supported security boundary. Real-process tests
cover both direct Uvicorn and the Gunicorn/`uvicorn_worker` topology with valid and malformed
forwarded identity values.

## Compatibility

This is a pre-release hosting policy. Existing applications that relied on Uvicorn's implicit
loopback proxy trust must explicitly configure Orbit's `trust_forwarded_headers` and
`trusted_proxies`, or they will correctly observe the direct socket peer.
