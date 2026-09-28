# Security model

Orbit Core defines security boundaries and enforcement contracts; it does not ship a complete
identity provider. Authentication plugins verify credentials and produce an `Identity`. Core turns
that verified identity into a task-local `Principal`, applies authorization policies, and records
bounded audit information without retaining raw credentials.

The optional PyJWT verifier validates bounded algorithm, issuer, audience, and clock-leeway
configuration before any credential is processed. Algorithms must be explicit and cannot include
`none`; malformed configuration and non-finite numeric date claims are rejected consistently at
construction or verification time. The provider-neutral `TokenValidationPolicy` is a frozen
Pydantic model. Issuer and audience values are bounded, policy collections are capped at 1,024
entries, and clock skew is limited to one day, so issuer, audience, clock-skew, and required-scope
rules cannot be changed after composition or made accidentally permissive through unbounded
metadata.

## Trust boundaries

Treat every request header, path, cookie, query value, body, forwarded address, plugin package, and
admin transport as untrusted until validated. Trusted proxy networks must be configured explicitly
before forwarded headers affect client identity or scheme. A plugin entry point is executable code;
discovery therefore requires an explicit allowlist and deployment-level package trust.

## Authentication

The bearer authenticator accepts only syntactically valid bounded credentials and delegates
verification to an injected `TokenVerifier`; duplicate `Authorization` fields are rejected rather
than selecting the first value. JWT, OAuth 2.0, OIDC, JWKS retrieval, credential storage, key
rotation, refresh tokens, sessions, and provider-specific claims belong to plugins.
Core's token models contain verified lifecycle metadata and intentionally do not retain the original
credential string. Token identifiers, subjects, and policy issuer/audience/scope values reject
control characters before they can enter audit, logging, or trust decisions.
Verified claim mappings are bounded by key shape and cardinality, then detached
and recursively frozen so provider-owned mutable data cannot change an identity
or token after verification. When a policy checks audience, the token's audience claim is also
bounded to 1,024 entries and printable values of at most 255 characters before matching.
Token, JWKS, policy, health, and administrative audit timestamps must have a usable UTC offset;
a custom `tzinfo` object whose `utcoffset()` returns `None` is rejected as ambiguous.

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
security context. Policy evaluators must return an actual boolean; truthy non-boolean values are
denied. Authorization is evaluated after
authentication and before handler execution.
Role and token-scope identifiers are bounded printable text without whitespace or control
characters before they reach authorization, OpenAPI, audit, or diagnostic output. JWT scope
claims are normalized and bounded before verified token construction. Every
role/scope collection is capped at 1,024 entries across principals, tokens, routes, OAuth
requests, and authorization policies, preventing one security surface from accepting an
unbounded identity payload. Verified token
and identity subjects, providers, and token types are strict printable strings, so numeric or
boolean values and control characters cannot be silently converted into security metadata.
The in-process rate limiter exposes its capacity and refill period as read-only policy properties,
rejects capacities above 10 million, periods above 365 days, retained-key counts above one million,
and refill arithmetic that would become non-finite. Its returned decisions also validate remaining
tokens and retry delays before they reach an HTTP or administrative response. Revocation lookups
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
