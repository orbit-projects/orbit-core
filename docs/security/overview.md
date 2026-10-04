# Security model

Orbit Core defines security boundaries and enforcement contracts, and includes a small opt-in
static-user Basic Authenticator for local applications and simple administration. It is not a full
identity provider. Core turns verified credentials into a task-local `Principal`, applies
authorization policies, and records bounded audit information without retaining raw credentials.

The provider-neutral `TokenValidationPolicy` is a frozen Pydantic model. Issuer and audience values
are bounded, policy collections are capped at 1,024 entries, and clock skew is limited to one day,
so issuer, audience, clock-skew, and required-scope rules cannot be changed after composition or
made accidentally permissive through unbounded metadata. JWT signature verification through
PyJWT is available separately as `orbit-jwt`; it implements Core's `TokenVerifier` contract and
is not installed with Core.

## Trust boundaries

Treat every request header, path, cookie, query value, body, forwarded address, plugin package, and
admin transport as untrusted until validated. Trusted proxy networks must be configured explicitly
before forwarded headers affect client identity or scheme. A plugin entry point is executable code;
discovery therefore requires an explicit allowlist and deployment-level package trust.

## Authentication

The Basic authenticator is explicitly configured with users; Core creates salted PBKDF2-HMAC-SHA256
hashes (600,000 iterations by default) and never supplies default accounts or stores plaintext
passwords. Verification runs off the event loop with bounded concurrency; cancellation does not
release a verification slot until already-running hash work finishes. It uses a generic failure
for malformed, unknown, and incorrect credentials, and emits a `WWW-Authenticate` challenge for
protected anonymous requests. TLS is required by default. Terminate TLS at a trusted proxy and
configure forwarded-proxy trust correctly; do not send Basic credentials over plaintext HTTP.
`BasicCredential.create()` is synchronous bootstrap work and should receive passwords through a
secret-management mechanism, not source code or committed configuration.

This static credential list has no account lifecycle, password-reset flow, federation, rotation
workflow, or persistent credential backend. It is appropriate as a development/admin baseline, not
a substitute for a dedicated identity provider in a multi-user production system. Use a deployment
secret store, rotate credentials deliberately, and prefer a dedicated identity service for
professional or federated security requirements.

Optional provider-neutral request security policies are supplied by the separate `orbit-security`
package; its current implementation is per-process HTTP rate limiting, not a full professional
security suite. Concrete JWT and OAuth/OIDC integrations are separately installable adapters. Core
Basic Auth remains available without installing those packages.

Orbit has one optional `orbit-security` package for additional provider-neutral security policies;
its current implementation provides request-level rate limiting only. Core's built-in security
baseline remains available without installing that package. There are no separate Basic or
Professional security package variants. Do not interpret the current rate-limit middleware as a
complete security suite.

The bearer authenticator accepts only syntactically valid bounded credentials and delegates
verification to an injected `TokenVerifier`; duplicate `Authorization` fields are rejected rather
than selecting the first value. The separately installable `orbit-jwt` adapter verifies JWT
signatures through PyJWT. OAuth 2.0, OIDC, JWKS retrieval, external credential storage, key
rotation, refresh tokens, sessions, and provider-specific claims belong in separately installable
security integrations.
Core's token models contain verified lifecycle metadata and intentionally do not retain the original
credential string. Token identifiers, subjects, and policy issuer/audience/scope values reject
control characters before they can enter audit, logging, or trust decisions.
Verified claim mappings are bounded by key shape and cardinality, then detached
and recursively frozen so provider-owned mutable data cannot change an identity
or token after verification. When a policy checks audience, the token's audience claim is also
bounded to 1,024 entries and printable values of at most 255 characters before matching.
Token, JWKS, policy, health, and administrative audit timestamps must have a usable UTC offset;
a custom `tzinfo` object whose `utcoffset()` returns `None` is rejected as ambiguous.
Security expiry ordering compares exact UTC instants rather than local wall-clock fields, including
the repeated hour during daylight-saving fallback. The in-memory token revocation index expires
entries through an ordered heap, so each lookup does not scan every retained revocation.

JWKS snapshots cap key count, validate key metadata text, and freeze provider-specific extra
fields after validation. OAuth token responses and OIDC discovery documents retain forward-
compatible provider fields but bound their top-level metadata to 128 printable keys and freeze
them before adapters consume or expose the models. Nested mapping keys use the same bounded,
printable text rule; token text is bounded as well.

OAuth authorization requests require strict text, an explicit S256 PKCE method whenever a
challenge is present, and HTTPS redirect URIs; the weaker RFC 7636 ``plain`` default is not
accepted. Raw whitespace, control characters, and backslashes are rejected before URL parsing to
avoid parser or browser normalization differences.
HTTP callbacks are accepted only for `localhost` development targets; URLs containing userinfo or
fragments, empty ports, or IPv6 zone identifiers are rejected. OIDC discovery endpoints follow the
same HTTPS/localhost rule. Provider
adapters remain responsible for checking that redirect URIs are registered with the identity
provider and match the application's deployment policy.

## Authorization

Route roles and `PolicyEngine` decisions are deny-by-default. Policy names are bounded lowercase
identifiers, the registry accepts at most 10,000 policies, anonymous callers do not satisfy a
required role, unknown policies fail closed, and resource-level policies receive an explicit
security context. Public authorization helpers require an actual Core `Principal` instance (or
`None`); a caller-supplied object that merely exposes a matching `roles` attribute is rejected.
Policy evaluators must return an actual boolean; truthy non-boolean values are denied.
Authorization is evaluated after
authentication and before handler execution.
Role and token-scope identifiers are bounded printable text without whitespace or control
characters before they reach authorization, OpenAPI, audit, or diagnostic output. JWT scope
claims are normalized and bounded before verified token construction. Every
role/scope collection is capped at 1,024 entries across principals, tokens, routes, OAuth
requests, and authorization policies, preventing one security surface from accepting an
unbounded identity payload. Verified token
and identity subjects, providers, and token types are strict printable strings, so numeric or
boolean values and control characters cannot be silently converted into security metadata.
Core's bounded in-process rate limiter protects the built-in Admin Panel; it is not a distributed
quota service. General request-level HTTP rate limiting is optional middleware in
`orbit-security`, built on Core's rate-limiter and ASGI contracts. Separate workers maintain
separate buckets. Shared/distributed quotas and provider-backed policies belong in optional
security or resilience capability packages and adapters. The in-process rate limiter exposes its
capacity and refill period as read-only policy properties,
rejects capacities above 10 million, periods above 365 days, retained-key counts above one million,
and refill arithmetic that would become non-finite. At capacity it evicts the least recently used
identity with constant-time bookkeeping, avoiding a full retained-key scan for every new identity.
Rate-limit identity keys are nonempty, bounded to 255 characters, and reject Unicode control
characters, including C1 controls outside the ASCII range.
Its returned decisions also validate remaining tokens and retry delays before they reach an HTTP
or administrative response. Revocation lookups
apply the same bounded printable token-ID contract as token construction, and the revocation store
limits retained entries to one million. These bounds prevent
post-composition mutation or extreme configuration from desynchronizing token accounting.
Plugins may add policy evaluators, but they must document whether decisions are local, cached, or
dependent on a remote policy service.

## Administrative access

The admin surface is disabled by default. Read operations require the admin-read permission;
mutations require admin-write permission and exactly one nonempty non-cookie request authorization
mechanism. CSRF and origin checks protect browser mutations, and rate limiting applies to the admin
boundary. Admin responses expose structured state and bounded diagnostics, never secrets or provider
payloads.

## Operational requirements for plugins

Plugins must document credential sources, rotation, revocation, clock skew, failure behavior,
logging redaction, and audit events. Do not log authorization headers, tokens, cookies, private keys,
or complete provider responses. Security tests should cover malformed input, replay, expiry,
revocation, key rotation, proxy spoofing, CSRF, rate-limit exhaustion, and denied-by-default paths.

Core's local checks demonstrate contract behavior; they do not certify a provider's cryptography,
identity policy, secret management, or deployment configuration.
