# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Fixed

- Service and provider dependency declarations now allow at most 1,024 edges per component, and
  route middleware stacks have the same bound, preventing one composition object from creating
  disproportionate graph-validation or dispatch work.
- Dependency string keys now reject control characters consistently across container registration,
  direct providers, and resolution diagnostics.
- ASGI shutdown now aggregates middleware `CancelledError` failures while still completing
  application cleanup, preventing a cancellation-signalling hook from leaking Core resources.
- Event-bus close ownership is now shared and shielded from caller cancellation, while preserving
  deferred durable-store cleanup retries after in-flight publication is released.
- Health-service close ownership is now shared and shielded from caller cancellation, so detached
  health checks cannot be abandoned by an interrupted cleanup caller.
- The local Tox gate now covers Python 3.14, enforces the same 90% coverage threshold as CI, and
  runs model-boundary, documentation, example, formatting, and license checks consistently.
- Secret references now require a parsed hostname and valid URI port authority, rejecting
  malformed authorities before provider adapters receive them.
- Secret references now reject empty authority ports, backslashes, and IPv6 zone identifiers so
  URI parser normalization cannot change the backend target.
- Configuration loading now preserves populated falsey mappings supplied explicitly for values or
  environment sources instead of treating them as omitted.
- The in-process TestClient now preserves populated falsey header sequences instead of treating
  them as omitted request headers.
- Dependency-graph validation now uses a linear iterative depth-first pass plus post-order scope
  analysis, so deep valid provider graphs avoid recursion-limit failures and repeated root walks.
- Plugin capability validation now builds the enabled capability index once per composition instead
  of recomputing it for every plugin.
- Plugin identity and lifecycle lookups now use bounded registry indexes instead of repeated
  full-registry scans during registration and activation cleanup.
- State namespace writes now prepare and detach entries before publishing revisions, so value-copy
  failures cannot advance revision state without storing the corresponding entry.
- PyJWT verifier configuration now bounds the number of accepted algorithms and audience values,
  keeping authentication policy metadata within Core's explicit collection limits.
- OAuth token-response scopes now use Core's shared bounded scope validator, and OIDC discovery
  capability lists now reject excessive, unsafe, or duplicate values before adapter use.
- JWKS optional algorithm and usage metadata now rejects empty values before key selection or
  provider diagnostics can treat them as meaningful metadata.
- Service and plugin version metadata now has an explicit 255-character ceiling, preventing
  syntactically valid but unbounded version labels from reaching inspection or registry state.
- Correlation, trace, and span context binders now validate bounded printable identifiers before
  malformed values can enter task-local logging or tracing state.
- The request-ID context binder now performs the runtime UUID check that the `RequestId` NewType
  expresses statically, preventing dynamic callers from inserting arbitrary identifiers.
- The security context binder now rejects arbitrary dynamic values before they can replace the
  authenticated `Principal` in task-local state.
- Service and plugin composition registries now enforce Core's shared one-million cardinality
  ceiling before allocating another long-lived registration.
- Route registration and middleware composition now enforce the same Core cardinality ceiling;
  middleware callability and lifecycle hooks are validated before startup.
- Dependency-provider registration now enforces the Core cardinality ceiling while still allowing
  an existing unresolved provider to be deliberately replaced.
- Lifecycle observer and telemetry sink registration now enforce the shared Core cardinality
  ceiling before retaining another long-lived callback.
- Configuration reload and provider-resolution observer registration now enforce the same shared
  Core cardinality ceiling before retaining another long-lived callback.
- Application child, configuration-watcher, state-namespace, and admin-contribution registries now
  enforce the shared Core cardinality ceiling before retaining another composition object.
- Typed configuration sections and supervised background-task specifications now enforce the shared
  Core cardinality ceiling before retaining another composition object.
- Nested application cycle validation now uses an iterative reachability walk, avoiding a leaked
  Python recursion failure for deeply nested but otherwise valid application graphs.
- CI and release workflows now verify the frozen lockfile explicitly and cancel superseded pull
  request validation while preserving manually triggered release runs.
- Application builders and state transactions now enforce Core's shared cardinality ceiling before
  retaining another service or distinct staged state change.
- Direct span, identity-provider, and token-type construction now rejects malformed non-text or
  control-character metadata with explicit Core validation errors.
- The opt-in Uvicorn/Gunicorn process suite now repeats bounded 64-request bursts across the
  initial and replacement workers, and bounded 16-request bursts on the direct development server,
  before lifecycle shutdown.
- The in-process ASGI test harness now explicitly covers structured lifespan failures, silent host
  termination, bounded shutdown cancellation, client disconnect frames, and missing responses.
- Reliability composition now validates retry policies and failure classifiers before work begins,
  and preserves an explicitly supplied falsey classifier instead of silently replacing it.
- GZip negotiation now applies the bounded HTTP qvalue grammar and treats case-insensitive quality
  parameters consistently, failing closed on malformed values instead of trusting `float()` syntax.
- CORS origin configuration now rejects empty authority ports, trailing query or fragment delimiters,
  IPv6 zone identifiers, and control characters that URL parsing could otherwise normalize away.
- CORS response headers now deduplicate `Vary` field names case-insensitively, preventing duplicate
  cache dimensions such as `origin, Origin`.
- Forwarded proxy parsing now rejects IPv6 zone identifiers, preventing Python-specific interface-
  scoped literals from entering trusted client identity metadata.
- Request cookie parsing now rejects duplicate cookie names within one header instead of silently
  selecting the last value supplied by the standard-library parser.
- OAuth/OIDC URL validation now rejects raw whitespace, control characters, and backslashes before
  parsing, preventing normalization differences from changing a redirect or provider target.
- OAuth/OIDC URL validation also rejects empty authority ports and IPv6 zone identifiers, keeping
  provider and redirect authorities unambiguous.
- ASGI client and Host validation now reject interface-scoped IPv6 addresses before trusted proxy
  evaluation.
- Token policy clock-skew validation now converts `datetime.min`/`datetime.max` arithmetic
  overflow into a bounded validation error instead of leaking a runtime exception.
- OAuth HTTPS URL validation now fails closed for malformed or out-of-range authority ports and
  unmatched IPv6 brackets instead of leaking `urllib.parse` exceptions.
- Application request admission now caps `max_concurrent_requests` at Core's shared one-million
  capacity ceiling, preventing an unbounded request-task set from being configured accidentally.
- Repository governance documentation now matches the active `main-protection` ruleset name and
  records that required status-check contexts still need to be configured by an authenticated
  maintainer.
- The opt-in Gunicorn/Uvicorn hosting smoke test now sends concurrent request bursts before and
  after worker replacement and completes an in-flight request during graceful termination,
  covering request admission and draining across both worker generations.
- The hosting process suite now also starts a real direct Uvicorn development server, exercises
  concurrent requests, and verifies interactive shutdown reaches Orbit lifespan cleanup.
- Application shutdown now gives detached, cancellation-resistant health checks a bounded cleanup
  window instead of only requesting cancellation and returning immediately.
- Task supervisor shutdown now gives timed-out failure observers the remaining shutdown budget to
  finish cooperatively before returning, while still bounding cancellation-resistant observers.
- JWKS key lookup now rejects control-character identifiers consistently with validated key metadata.
- Public `Headers` and `Request` construction now enforces Core's aggregate header, body, and query
  ceilings, so adapters cannot bypass the corresponding ASGI safety limits.
- Structured Core mappings now reject cyclic, excessively deep, or excessively large nested
  containers while freezing them, preventing raw recursion failures and mutable nested adapters.
- `AdminClient` now bounds distinct asynchronous transport operations, retaining a concurrency slot
  until a timed-out adapter actually finishes instead of allowing unlimited late tasks by path.
- ASGI shutdown now reports middleware and application cleanup failures together instead of losing
  the application failure.
- Application configuration now validates default values and preserves declared strict float
  types for lifecycle, request, health, and administrative rate-limit timeouts.
- Plugin discovery now validates the execution allowlist before consulting entry points, rejecting
  scalar, malformed, duplicate, or non-text names and avoiding metadata access for an empty list.
- Plugin registry enablement, disablement, and capability lookup now reject malformed identifiers
  before using caller input as registry state or capability metadata.
- Every public Core Pydantic model now validates declared defaults, and the model-boundary audit
  enforces that setting so strict field constraints cannot be bypassed by future defaults.
- ASGI request and response boundaries now reject duplicate `Content-Type` fields instead of
  selecting the first conflicting value.
- Cookie parsing now rejects duplicate request fields instead of selecting one potentially
  conflicting session value.
- Admin service and task endpoints now validate target slugs before lookup, matching the remote
  AdminClient contract and preventing malformed operation targets from reaching lifecycle code.
- Distribution metadata now derives its package version from the single `orbit._version` source,
  preventing a release artifact from disagreeing with `orbit.__version__`.
- GZip negotiation now combines repeated `Accept-Encoding` fields and fails closed on duplicate
  coding or quality entries instead of selecting one ambiguous value.
- Token revocation stores now cap configured retained entries at one million, matching the bounded
  security-memory contract documented for Core rate limiting and revocation.
- Task supervision now caps retained failure history and restart budgets at one million, preventing
  extreme configuration from creating unbounded memory or recovery work.
- Configuration snapshot history and in-memory state namespace/provider capacities now reject
  requests above one million, keeping long-lived Core retention boundaries explicit.
- Event delivery, event-store retention, diagnostics/tracing history, metrics cardinality, and
  lease-key capacity now apply the same one-million upper boundary to in-memory resource controls.
- Admin synchronous transport slots and retry attempts now reject extreme budgets, and configuration
  file reads now have a documented 64 MiB maximum in addition to their default one MiB limit.
- Configuration watchers now enforce the same 64 MiB file-read ceiling at construction instead of
  accepting a value that would fail only when polling begins.
- Circuit-breaker failure thresholds and bulkhead concurrency limits now reject values above one
  million, keeping reliability policy inputs explicit and effective.
- Task supervision now truly disables retained failure history when `history_size=0`; failures still
  update the task's latest-failure state and observer/event paths without growing hidden storage.

- Public `StateEntry` snapshots now validate keys, revisions, and finite expiry deadlines and
  detach arbitrary values at construction, preventing caller-owned nested state from mutating a
  published snapshot.
- Public event `Subscription` and `Delivery` records now validate UUID identities, event names,
  counters, retry priorities, and boolean flags independently of `EventBus` construction.
- Public `MetricSnapshot` records now validate exporter-facing kinds, identifiers, finite values,
  counters, labels, and histogram buckets before recursively freezing their mappings.
- Configuration snapshot and change records now validate versions and UUID ownership and reject
  non-string mapping keys instead of silently converting ambiguous keys during freezing.
- Composition inspection snapshots now freeze nested redacted configuration and validate bounded
  provider metadata before it reaches Admin or CLI serialization.
- Public dependency `Provider` records now validate factories, scopes, dependency keys, resource
  ownership, and UUID identities independently of `Container` registration.
- Provider-resolution diagnostics and route bindings now validate their public metadata during direct
  construction as well as through container/router registration.
- Diagnostic snapshots now reject non-finite durations, invalid HTTP status-count mappings, and
  aggregate totals that do not describe the same request population.
- Verified token and identity metadata now rejects implicit numeric/boolean-to-string coercion at
  the security boundary.
- Application names, environment names, and trusted-proxy entries now reject implicit
  numeric/boolean-to-string coercion in `ApplicationConfig`.
- `PluginMetadata` now validates plugin names, dependency declarations, capability identifiers,
  API-version text, and strict field types before registry composition.
- `ServiceDescriptor` now validates strict names, versions, and bounded capability identifiers
  before service registration or inspection.
- The final ASGI response boundary now suppresses bodies for lowercase `head` methods as well as
  canonical `HEAD`, including overload and draining responses.
- `AdminHTTPResponse` now rejects non-string response keys before Pydantic can coerce malformed
  remote JSON objects.
- `AdminHTTPResponse` now validates and recursively detaches response bodies at the transport
  boundary, preventing adapter-owned or caller-owned nested mappings from mutating an admin result
  after it has been returned.
- Route metadata now rejects implicit coercion of route names and authorization role labels before
  OpenAPI generation or dispatch.
- Forward-compatible JWKS, OAuth, and OIDC provider metadata is now bounded and rejects control
  characters before it is retained or exposed through Core security contracts.
- The in-memory state provider now validates write keys, TTLs, and optimistic versions before
  allocating namespaces, preventing malformed writes from consuming namespace capacity.
- State transactions now preserve the namespace revision when they only delete missing keys,
  matching direct-delete no-op semantics and avoiding unnecessary optimistic conflicts.
- `TestResponse` now validates direct construction so the built-in ASGI harness cannot represent
  malformed status, header, or body data in assertions.
- `AdminHTTPResponse` now accepts only final `2xx–5xx` statuses, rejecting informational transport
  responses before remote admin handling.
- `AdminClientError` now accepts only final `4xx–5xx` statuses, matching the remote failure
  contract instead of accepting informational or redirect values when constructed directly.
- `HTTPError` now accepts only final `4xx–5xx` error statuses, preventing malformed application
  errors from reaching the ASGI response serializer.
- `Response.text()` now rejects non-string content with an explicit type error before encoding,
  keeping malformed handler output inside the public response contract.
- Route groups now normalize and validate role collections at composition time, preventing malformed
  nested-group inputs from failing later during role-set combination.
- Metric histogram snapshots now reject impossible bucket totals and non-cumulative bucket counts;
  the registry also reserves Prometheus's generated `le` label for histogram buckets.
- Shared role and scope validation now caps every security collection at 1,024 entries, including
  token scopes, principals, routes, OAuth requests, and authorization policies.
- The in-memory state provider now prevalidates read and delete keys/version conditions before
  missing-namespace lookup, preventing malformed requests from becoming silent no-ops.
- Configuration diffs now require an owned `ConfigSnapshot`, rejecting unrelated configuration
  identities instead of silently comparing similarly shaped settings from another application.
- The supported plugin contract label is now exposed as one `CORE_API_VERSION` constant shared by
  metadata defaults and registry negotiation, preventing extension-version drift.
- Configuration section reloads now require the owner to be frozen first, preserving the
  composition boundary and making the documented post-startup hot-reload contract explicit.
- JWKS key identity, type, algorithm, and usage metadata now reject byte-to-text coercion before
  provider key selection or security diagnostics.
- CodeQL and OpenSSF Scorecard jobs now retain explicit repository read permission alongside their
  analysis permissions, allowing checkout and security reporting to run under least privilege.
- Event, audit, error, and secret-reference text fields now reject byte-to-text coercion before
  identifiers or operator-facing security metadata are retained.
- CI and release validation now include a dependency-free model-boundary audit that prevents Core
  Pydantic models from reintroducing implicitly coercible primitive fields.
- OAuth/OIDC scopes, discovery metadata, verified token scopes, and principal roles now reject
  byte-to-text coercion before authorization or provider negotiation.
- Structured event, service, health, error, identity, token, and configuration mappings now reject
  non-string keys before Pydantic can coerce them at the public model boundary.
- The model-boundary audit now requires typed mapping fields to retain their explicit pre-validation
  hooks, protecting that key-coercion invariant during future model changes.

### Added

- The strict Pydantic model-boundary audit now runs in local pre-commit validation as well as CI,
  catching implicit primitive and mapping-key coercion before a change is committed.
- Distribution integrity checks now reject symlink-like archive members and stale or mismatched
  wheel/source filenames before release provenance is requested.
- Public API stability and deprecation policy documentation, including the separation between
  package release versions and the Core plugin contract version.
- The maintained minimal example is now an explicit package and is exercised through the real CLI
  target loader, keeping starter-project documentation aligned with the public composition API.
- Hosting configuration now rejects byte-to-text coercion for the bind host and selected server,
  keeping process and socket-boundary inputs strictly typed.
- OpenSSF Scorecard now runs for pull requests as well as the default branch and scheduled
  checks, making security results available for branch-protection review once hosted checks are
  configured.
- The release workflow now records and verifies SHA-256 checksums for every distribution and
  generated inventory before requesting GitHub build provenance and uploading the artifact.
- Release validation now repeats the real two-worker Gunicorn/Uvicorn hosting smoke test before
  generating a distributable artifact.
- Service lifecycle, dependency, health, and inspection paths now verify that a registered service's
  descriptor still matches its frozen composition snapshot, failing closed on post-registration
  identity mutation; rollback uses the frozen snapshot even if a failing hook mutates live metadata.
- Direct `Request` construction now bounds path-parameter cardinality and enforces safe identifier
  keys and canonical nonempty path-segment values, matching the ASGI/router boundary.
- Plugin activation, inspection, and cleanup bookkeeping now verifies frozen metadata identity and
  uses the registration snapshot during rollback when a hook mutates live metadata.
- Application health and runtime-state inspection now use the same frozen plugin identity, so a
  mutated live metadata attribute cannot rename or split operational diagnostics.
- Composition inspection and plugin health metrics now use the frozen registration identity, keeping
  Admin, CLI, and telemetry views consistent after a plugin metadata mutation.
- Route templates now enforce the same 128-parameter and bounded-identifier limits as direct
  `Request` construction, preventing a composed route from failing later during dispatch.
- Event, lifecycle-transition, and secret-resolution timestamps now require usable timezone offsets;
  event delivery keys also reject control characters before deduplication or adapter handoff.
- Secret reference and resolved-version metadata now reject control characters before redacted
  configuration or administrative inspection can expose ambiguous text.
- Event-store replay results now validate positive cursors, concrete event envelopes, and
  timezone-aware retention timestamps at the adapter boundary.
- Event delivery and subscription diagnostics now expose typed domain identifiers, and the in-memory
  event store detaches payloads at append time to prevent post-handoff mutation.
- Event-bus, event-store, task-supervisor, state, diagnostics, configuration, and reliability
  numeric limits reject boolean values rather than accepting Python's implicit `bool`-as-`int`
  coercion.
- Typed service ownership metadata for direct and grouped route registration, with composition-time
  validation for unknown route owners.
- Stable typed provider identities are now included in dependency inspection metadata.
- Shared CLI and Admin composition inspection now exposes the Core configuration identity.
- Request diagnostics and runtime context now use typed server-generated `RequestId` values while
  preserving UUID string serialization at HTTP and logging boundaries.
- API reference and repository-governance guides covering public import surfaces, docstring
  expectations, branch protection, required checks, and OpenSSF release evidence.
- CI now exercises the managed Gunicorn/Uvicorn worker smoke test across every supported Python
  matrix version instead of validating the worker topology only on Python 3.11.
- A normative project charter and public roadmap describing the single Core boundary, the Orbit
  ASGI hosting model, Admin Panel and CLI surfaces, plugin ecosystem direction, and open-source
  governance commitments.
- Repository documentation policy covering public API docstrings, Markdown structure, local links,
  and unresolved work markers in maintained comments.
- Distribution integrity validation for release wheels and source archives, including safe archive
  paths, importable package contents, PEP 561 typing metadata, license inclusion and package metadata.
- Distribution integrity validation now requires wheel and source metadata to match the project
  version declared in `src/orbit/_version.py`, preventing stale artifacts from passing release
  checks.
- Hosting reload policy now uses strict boolean validation, preventing direct configuration strings
  from silently enabling source reload behavior.
- Contributor documentation covering architecture boundaries, validation requirements, and
  contract-impact reporting for pull requests.
- Typed composition inspection shared by CLI and admin, with service, plugin, route,
  configuration and provider inspection commands.
- Request correlation context, opt-in JSON logging, cumulative latency buckets and
  explicit cancelled/disconnected/failed request outcomes.
- Structured JSON environment settings, bounded configuration file loading and
  deterministic nested overrides.
- Operational documentation, concurrency ownership ADR and regression suites covering
  resource races, startup/shutdown ownership, admin extensions and ASGI protocol failures.
- Concurrent ASGI admission coverage now exercises two accepted requests, overload rejection,
  failed handler cleanup, and capacity reuse through the public in-process client.
- Deterministic ASGI adversarial-scope coverage now exercises malformed methods, paths, client
  addresses, schemes, header shapes, and oversized header lists through the real error path.
- ASGI disconnect coverage now verifies that a streamed iterator closes before its request-scoped
  dependency resources exit, preventing transport failures from leaving request resources open.
- Added `orbit diagnostics-watch` for bounded newline-delimited diagnostics snapshots from one
  local application lifecycle session.
- The real Gunicorn/Uvicorn hosting smoke test now exercises SIGHUP worker replacement and
  verifies that every worker generation reaches service cleanup before master exit.

- Admin extension registration now validates the Core contribution contract explicitly and rejects
  non-awaitable inspection results without leaking incidental task-construction errors.
- Nested application and configuration-watcher registration now rejects malformed composition
  inputs with explicit contract errors before mutating ownership state.
- Background task failure and inspection snapshots now validate bounded names, states, attempts,
  exception identifiers, and finite timing before entering diagnostics or admin views.
- Rate-limit decisions and token-revocation lookups now enforce the same strict boolean, finite,
  nonnegative, and printable identifier contracts at their public boundaries.
- ASGI request parsing now caps the number of body frames in addition to byte and timeout limits,
  preventing zero-byte frame floods from consuming unbounded parser work.
- ASGI request headers now enforce a 1,000-field count in addition to aggregate byte limits,
  keeping header parsing and duplicate-field handling bounded.
- ASGI trace-context extraction now applies the request-header cardinality bound before scanning
  `traceparent`, preventing oversized scopes from bypassing the early parser budget.
- ASGI trace-context extraction now rejects duplicate `traceparent` fields instead of trusting the
  first value and ignoring later conflicting metadata.
- The Core authorization registry now caps policy cardinality at 10,000 while permitting explicit
  replacement of existing policies, preventing unbounded evaluator retention during composition.
- Stable focused-package exports now include the documented service, plugin, admin, health, lifecycle,
  container, and ASGI extension contracts, with import-surface regression coverage.
- Namespaced state writes now detach values before advancing revisions, and multi-key transactions
  prepare every copy before mutation, preventing failed deep copies from causing partial commits.
- In-memory event-store appends now detach events before allocating replay cursors, preventing
  failed payload copies from consuming sequences or leaving partially persisted records.
- In-memory event-store reads now select the requested bounded result window before deep-copying,
  preventing small replay requests from doing unnecessary work on unrelated retained events.
- Public tracing snapshots now validate IDs, statuses, and finite nonnegative timing before
  retaining diagnostic records.
- Request diagnostics now reject non-`RequestRecord` values and cumulative duration overflow before
  mutating history or metrics, keeping operational snapshots internally consistent.
- Diagnostic export now strictly validates its optional indentation value instead of leaking
  incidental comparison errors for booleans or strings.
- Security, JWKS, token-policy, and audit timestamp validation now rejects pseudo-aware datetimes
  whose `tzinfo` has no usable UTC offset, preventing ambiguous comparisons and leaked `TypeError`.
- Numeric policy validation now treats integers too large for platform floating-point conversion
  as invalid Core input, returning the documented boundary error instead of leaking `OverflowError`.
- Retry backoff now saturates at its configured maximum when exponential calculation would
  overflow, preserving bounded retry behavior for extreme finite multipliers.
- Event-bus deferred store cleanup now has its own bounded wait and can be retried after a
  cancellation-resistant publish releases the store, preventing an unbounded shutdown waiter.
- Metrics counters and histograms now reject overflow-sized inputs and non-finite cumulative totals
  before mutating registry state, preventing invalid `Infinity` exposition.
- Health aggregation now tracks detached cancellation-resistant checks by their own names, so a
  stuck provider cannot cause another component's readiness result to be reported as cancelling.
- Token validation policies now bound issuer and audience metadata, cap policy collections, and
  reject clock-skew values greater than one day before they can weaken a Core trust boundary.
- In-process rate limiters now bound capacity, period, and retained-key policies and reject
  non-finite refill arithmetic before token accounting begins.
- Optional PyJWT verification now bounds algorithm, issuer, and audience policy metadata and
  rejects boolean, non-finite, or text-coerced `iat`/`exp` claims before token construction.
- Lifecycle observers now use an explicit bounded deadline, detach cancellation-resistant work,
  suppress repeated invocation until retirement, and are cancelled with application cleanup.
- `AdminClient` now recognizes callable async transports and detaches cancellation-resistant
  operations at their deadline, retaining at most one late operation per request path.
- `orbit health-watch` now bounds finite iteration requests at 1,000,000 snapshots, preventing
  accidental unbounded CLI output while preserving explicit continuous mode with zero iterations.
- The opt-in Gunicorn hosting smoke test now verifies that SIGTERM reaches service cleanup in every
  managed worker, in addition to checking the HTTP route and clean master exit.
- ASGI request handling now validates a single bounded `Host` authority and rejects duplicate
  fields, malformed ports, unsafe delimiters, and unbracketed IPv6 before routing.
- Admin extension inspections now detach cancellation-resistant work at their deadline and retain
  at most one pending inspection per extension, preventing repeated requests from accumulating
  orphan tasks while preserving Core views.
- ASGI middleware lifecycle hooks are now bounded even when a hook suppresses cancellation;
  detached late hooks are tracked and consumed, and startup rollback continues through every
  started middleware.
- ASGI streamed-response cleanup now preserves the original send or disconnect failure when an
  asynchronous stream closer also fails, while still surfacing cleanup failures after successful
  response delivery.
- Route composition now rejects non-string grouped paths explicitly, and route metadata rejects
  invalid Unicode paths before OpenAPI generation or dispatch.
- Remote Admin synchronous transports now retain concurrency slots until timed-out worker calls
  actually finish, and slot acquisition is bounded, preventing stalled adapters from creating
  unbounded background threads.
- Health probes now detach cancellation-resistant provider checks at their deadline, fail repeated
  probes closed while one remains pending, and reuse one health coordinator per application to
  prevent readiness hangs and orphan-task accumulation.
- Task failure observers now retain at most one cancellation-resistant timed-out invocation, skip
  later observer calls while it is pending, and request its cancellation during supervisor shutdown.
- Event subscriber and dead-letter callbacks now detach at the bus deadline and are tracked per
  subscription, preventing cancellation-resistant handlers from hanging publication or spawning
  unbounded orphan tasks on repeated events.
- Plugin activation and deactivation hooks now detach at lifecycle deadlines and are tracked per
  plugin phase, preventing cancellation-resistant provider hooks from hanging startup or shutdown
  or being invoked concurrently during rollback.
- Container resource exits now detach cancellation-resistant async cleanup at their individual
  deadlines and continue the remaining exit stack, preventing one provider from blocking cleanup
  of later resources while late results remain owned and consumed by Core.
- Frozen token and identity models now detach and recursively protect provider claims, preventing
  post-verification mutation through nested dictionaries, lists, or sets while preserving Pydantic
  serialization.
- Verified token and identity claims now share bounded, printable claim-key validation and a
  maximum cardinality before they enter the security model.
- Event metadata, service metadata, and structured error context now use the same bounded,
  printable mapping-key contract before they are retained by Core.
- Route groups and OpenAPI document metadata now enforce bounded canonical-path and printable-text
  policies at the direct routing API boundary.
- Application timeout, capacity, and security flag fields now reject direct string/boolean
  coercion; the application environment loader decodes primitive `ORBIT_` values before strict
  validation.
- Runtime invariants no longer rely on Python ``assert`` statements, so ASGI, middleware, admin,
  dependency-injection, metrics, and testing contracts remain enforced under optimized execution.
- Direct router dispatch now validates and normalizes HTTP methods consistently with ASGI request
  dispatch, rejecting unsupported method values before route lookup.
- Background task failure observers now have a bounded asynchronous execution timeout, so a stuck
  diagnostic or event observer cannot prevent restart-budget accounting or shutdown progress.
- Token validation policies are now frozen Pydantic models, preventing post-composition mutation of
  issuer, audience, clock-skew, or required-scope trust rules.
- `StateStore.replace()` now uses the supplied snapshot revision for optimistic concurrency,
  preventing stale administrative or runtime snapshots from overwriting newer state.
- Configuration change records now freeze nested before/after inspection values, preventing
  observers or operators from mutating published audit data after a reload.
- Namespace identities and rate-limiter capacity/refill policies are now read-only after
  construction, preventing operational metadata or token accounting from being desynchronized.
- Circuit-breaker, bulkhead, CORS, and gzip middleware policies are now read-only after
  construction, preventing live operational behavior from diverging from validated limits.
- Verified token and identity subjects/identifiers, along with token-policy issuer, audience, and
  scope values, now reject control characters at the security model boundary.
- Raw ASGI paths now enforce absolute canonical input, reject encoded separators, controls,
  delimiters, and dot-segments, and continue to allow safe encoded characters such as `%2E`.
- ASGI client peer addresses now reject empty, oversized, whitespace, and control-character hosts
  as bounded 400 request errors instead of leaking a model `ValueError` as a 500 response.
- Hosting bind hosts now reject DEL as a control character consistently with other Core network
  boundaries.
- Runtime-facing Pydantic counters, event priorities, diagnostic numbers, admin outcomes, and
  OAuth token lifetimes now reject boolean and string coercion at their typed model boundaries.
- Resilience state transitions now ignore stale in-flight completions, reopen immediately after a
  failed half-open probe, and validate deadline start times; concurrent metric registration is now
  safe during Prometheus rendering.
- ASGI requests now require supplied `raw_path` bytes to decode to the exact host-supplied
  `path`, rejecting mismatched or invalid-UTF-8 scope pairs before routing.
- ASGI response framing now removes handler-supplied runtime-owned correlation and browser-security
  headers before emitting canonical values, preventing ambiguous duplicate headers.
- Configuration loading now validates model, prefix, mapping, and environment-entry contracts
  before composition and converts malformed loader inputs into redacted configuration errors.
- Configuration watcher inspection now sanitizes stored load and observer failures, preventing
  file paths or provider messages from leaking through `last_error`.
- Configuration reloads now reject observer-initiated nested reloads, preserving one atomic
  version/history commit per accepted replacement and deterministic rollback on observer failure.
- OAuth authorization requests now require HTTPS redirect URIs, with HTTP callbacks limited to
  `localhost` development targets.
- Administrative audit records now reject control characters and unbounded error text, and require
  timezone-aware timestamps so operator history remains safe and unambiguous.
- Pull-request CI now validates built distribution archives, and CodeQL checkout no longer persists
  repository credentials on the runner.
- ASGI and direct `Request` boundaries now cap decoded and raw request paths at 16 KiB, returning a
  bounded 414 response before oversized paths reach routing or handlers.
- Public `HTTPError` construction now validates status, code, and message bounds before failures
  reach response serialization.
- CORS now rejects duplicate `Origin` and preflight policy headers as ambiguous instead of trusting
  the first value supplied by an untrusted client or proxy.
- Response streams with `aclose` now require an async cleanup callable, preventing synchronous
  cleanup methods from failing after response delivery has begun.
- `AdminClient` now sanitizes malformed remote error codes instead of copying arbitrary response
  text into local operator exceptions.
- The in-memory state coordinator now bounds distinct lease keys and reclaims expired leases before
  rejecting new coordination work, preventing unbounded process-local coordination state.
- The in-memory state provider now bounds namespace count and avoids allocating namespaces for
  missing reads or deletes, preventing cache misses from consuming provider capacity.
- Trusted proxy handling now fails closed on repeated `Forwarded` or `X-Forwarded-*` fields instead
  of selecting the first conflicting value, while preserving validated comma-separated chains.
- Trusted proxy handling now also fails closed on duplicate `for` or `proto` parameters within one
  `Forwarded` element, preventing last-value overwrite of client identity or scheme.
- Health aggregation now validates check mappings, bounds check cardinality and names before task
  creation, and limits health messages to printable text so operator responses remain bounded.
- Diagnostic and metric snapshots now freeze nested label, bucket, and status-count mappings so
  exporters and operators cannot mutate published observation state after collection.
- Bearer authentication and Admin mutations now reject ambiguous or empty authorization headers,
  preventing duplicate-field first-value selection at privileged security boundaries.
- JWKS, OAuth token, and OIDC discovery contracts now freeze provider-specific extras, reject unsafe
  key/token text, and bound JWKS key-set cardinality before security metadata is reused.
- Configuration watchers now include a bounded content digest in their file fingerprint, detecting
  same-size in-place edits even when mtime and inode metadata are unchanged.
- Request diagnostics now validate method tokens and record malformed ASGI methods as `INVALID`,
  preventing rejected wire data from reaching operator logs or inspection exports.
- Application construction now rejects non-`ApplicationConfig` objects before allocating Core
  resources, replacing incidental attribute failures with an explicit contract error.
- ASGI now rejects non-mapping scopes explicitly, and the in-process test client reports
  non-Latin-1 request headers as a typed input error instead of leaking an encoding exception.
- Operator state and application summaries now validate component and service names as bounded
  identifier tokens before they are rendered by Admin or diagnostics surfaces.
- Structured Orbit problems and HTTP error responses now bound printable message, cause, code, and
  request-ID text before errors cross wire, Admin, logging, or telemetry boundaries.
- Route metadata now bounds paths, summaries, and API-version labels and rejects control characters
  in summaries before OpenAPI or Admin inspection generation.
- Metrics registries now bound metric definitions, label-name cardinality, and histogram bucket
  counts, rejecting malformed definitions before exporter memory can grow without a limit.
- In-memory tracing now bounds printable span metadata, caps attributes per span, recursively freezes
  retained attribute mappings, and restores context even when attribute retention fails.
- Runtime inspection now validates application identity and aggregate service/task/child counts as
  bounded, non-negative strict values before they reach operator-facing diagnostics.
- Event metadata, service descriptor metadata, health details, and structured error context are
  recursively protected after validation; the immutable mapping helper now supports safe deep
  copies for lifecycle and inspection snapshots.
- Role and token-scope identifiers now reject whitespace and control characters before authorization,
  OpenAPI, audit, or diagnostic output.
- Metrics now reject non-string label values instead of silently coercing them into label text.
- The documented `orbit.security.Authenticator` contract is now exported from the focused security
  package, keeping the public import surface aligned with the API reference.
- Runtime and ASGI constructors now validate application, hosting, router, authenticator, and
  tracer contracts before host startup or lifecycle work begins.
- Explicit task restarts now distinguish intentional cancellation from task failure, preventing
  synthetic `CancelledError` history entries and observer callbacks during operator actions.
- Event-store cleanup now uses one detached, bounded close operation for both idle and in-flight
  shutdown paths, preventing a cancellation-suppressing store from blocking Core shutdown.
- ASGI `root_path` values are now canonicalized and required to contain the request path before
  route dispatch; the documented WebSocket rejection policy has a direct protocol regression test.
- Direct router dispatch now applies the same canonical path checks as the ASGI boundary, and
  admin no-store headers remain enforced when an application is mounted at root path `/`.
- Direct router dispatch now shares the 16 KiB UTF-8 path bound, and all Core header construction
  rejects non-Latin-1 values before they can reach an ASGI wire boundary.
- Mounted ASGI root paths and direct `Request.root_path` values now share the 16 KiB UTF-8 bound,
  preventing oversized mount metadata from bypassing request path limits.
- `Config` now validates its `ApplicationConfig` owner and bounds extension section names before
  they enter snapshots, inspection, or reload records.
- Health reports now bound printable detail keys and top-level detail cardinality before provider
  observations reach readiness, Admin, or diagnostics surfaces.
- Supervised task names now use bounded lowercase identifiers, preventing ambiguous Admin paths and
  health keys during registration and operator restarts.
- OAuth/OIDC contracts now reject coerced or control-bearing authorization text, constrain PKCE
  challenges to base64url text, and reject insecure, credential-bearing, or fragment URLs.
- Policy evaluation now validates operation names before they can enter authorization lookups or
  structured security errors.
- Hosting limits now use strict integer validation, preventing string and float coercion from
  changing worker, timeout, keep-alive, or recycling policies.
- Response construction now rejects non-integer statuses, invalid stream objects, and bodies on
  204 or 304 responses before ASGI response framing begins.
- Direct `Request` construction now enforces the ASGI-compatible method, path, body, query,
  client, scheme, request-ID and path-parameter contracts, including a bounded query parser.
- Early trace-context inspection now tolerates malformed ASGI header scopes without bypassing the
  normal bounded request error path, and rejects zero trace or parent-span IDs.
- Task and event registration now rejects non-typed limits, policies, and callback values during
  composition instead of deferring failures to background execution.
- `AdminClient` now validates credential, timeout, worker, transport, service, and task operation
  inputs before invoking a remote adapter, and admin response statuses use strict integer typing.
- Application numeric resource limits now reject boolean values before Pydantic numeric coercion,
  preventing `True` from silently becoming a one-second, one-byte, or one-request policy.
- Reliability, health, container, testing, diagnostics, and metrics limits now reject malformed
  runtime types before invoking timeout, semaphore, cleanup, or exporter machinery.
- Header construction now rejects non-string names and values, invalid field names, and control
  characters with explicit typed-boundary errors instead of leaking incidental failures.
- The in-process ASGI test client now prioritizes same-turn lifespan exceptions over acknowledgements,
  and validates request methods, paths, bodies, headers, and timeout types before dispatch.
- Security boundaries now reject boolean rate-limit/revocation capacities, bound bearer provider
  identifiers at construction, report malformed audience claims as uniform validation errors, and
  reject invalid runtime types in token-validation and rate-limiter configuration.
- State namespaces, providers, leases, configuration history, and configuration watchers now
  reject malformed numeric, key, model, and path inputs at their public boundaries instead of
  leaking incidental Python type errors.
- Tracing and JWT verification boundaries now validate history, metadata, algorithm, issuer,
  audience, and clock-leeway types before creating spans or processing credentials.
- Lifespan startup and shutdown frames now receive explicit ASGI protocol validation, and
  malformed post-startup frames still trigger application cleanup before propagation.
- Lifecycle observers and transition targets now validate their public types before state-machine
  lookup, returning explicit contract errors instead of incidental Python exceptions.
- Dependency-container registration and resolution now validate keys, factories, scopes,
  dependency declarations, and replacement flags before changing or reading the provider graph.
- Router registration now validates route metadata, handlers, Pydantic model classes, and
  middleware members before accepting routes into the application graph.
- Service and plugin registries now validate lifecycle hooks and wrap malformed Pydantic metadata
  in structured Core errors before composition or activation.
- Authorization now requires boolean policy decisions and validates role collections, while token,
  JWKS, OAuth URL, and bearer-verifier boundaries reject malformed runtime values explicitly.
- Task restart names and diagnostics telemetry sinks now validate their public contracts before
  performing lookup or registering callbacks.
- Cookie construction now validates names, values, attributes, and security flags before emitting
  `Set-Cookie`, preventing malformed or implicitly truthy policy values from reaching the wire.
- Cookie `samesite` validation now rejects non-string values with an explicit API error before
  normalization.
- Configuration hot reload now restores the prior section when an observer is cancelled or
  interrupted by another `BaseException`, preventing an uncommitted replacement from leaking.
- Configuration snapshots and reload records now reject arbitrary objects and non-finite numbers,
  keeping their documented JSON-safe immutable boundary true for direct construction as well.
- Configuration snapshots now reject cyclic, excessively deep, or excessively large JSON-safe
  structures before retaining them in history or exposing them through inspection.
- CORS policy construction now rejects scalar, non-string and malformed method/header entries, and
  requires a real boolean credential flag before exposing browser-facing policy headers.
- Rate-limit middleware now validates key providers during composition and preserves falsey callable
  providers instead of silently replacing them with the anonymous-client default.
- Response streams now validate the complete asynchronous-iterator and optional cleanup contract
  before response delivery, preventing malformed streams from failing after headers are sent.
- State coordination now validates lease identity, opaque tokens, finite deadlines and lease method
  inputs before inspecting or mutating ownership state.
- Event delivery now validates event envelopes, subscription handles, runtime payload types, replay
  arguments and boolean predicate decisions before invoking subscribers or storage adapters.
- AdminClient now gives each transport call detached request headers and rejects malformed token,
  inspection-section and operation-action inputs before adapter execution.
- CLI target loading now validates non-text targets and normalizes ordinary module import failures
  without echoing arbitrary application exception messages.
- Independent dependency scopes no longer serialize through the root construction lock.
  Inherited child-task context cannot bypass factory ownership.
- Resource closure waits for acquisition, shares concurrent closes and attempts every
  exit with individual deadlines and aggregated failures.
- Complete startup is atomic against shutdown; reentrant lifecycle calls fail promptly.
- Plugin setup can register routes through real ASGI lifespan startup.
- Overload and error-response disconnects are included in request diagnostics.
- Concurrent health probes share work and update per-service health snapshots.
- Admin extension failures are isolated; all runtime admin responses prohibit caching.
- ASGI test helpers detect lifespan crashes and invalid response framing.
- Installed CLI entry points load explicit application targets from the working directory;
  a subprocess regression exercises the console script independently of pytest imports.
- Incorrect CI action pins replaced with verified upstream commits; workflows use the
  lockfile, enforce coverage, audit dependencies and build release provenance artifacts.

### Initial foundation

- Initial Core architecture, contracts, lifecycle, service registry, container, events, routing,
  ASGI runtime, security primitives, plugin contracts, and project governance foundation.
