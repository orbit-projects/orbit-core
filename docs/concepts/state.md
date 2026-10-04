# State

Application state is a frozen Pydantic snapshot shared by runtime, diagnostics, and administrative views. `StateStore.update` validates each replacement and supports optimistic `expected_revision` checks. `StateStore.replace` applies the same check using the supplied snapshot revision, so stale operator or admin snapshots cannot overwrite newer state. For multiple related changes, `with store.transaction() as tx:` stages updates and commits one revision atomically; a stale revision raises and leaves the store unchanged. Transactions can be rolled back by raising from the context block. The store owns identity and revision fields, and every read returns a detached snapshot. Service and plugin component names use the same bounded identifier contract as registration metadata, preventing malformed names from reaching operator snapshots. The state model also bounds its detached service and plugin collections by Core's shared composition capacity, so direct model construction cannot bypass registry limits. Structured mapping cardinality is enforced while copying, so a custom mapping cannot bypass a limit by reporting an inaccurate length.

`StateEntry` is also a validated public snapshot boundary: keys, positive revisions, and finite TTL deadlines are checked, and arbitrary values are detached before an entry is retained. This prevents a caller-owned nested mapping or list from changing an already-published state record.

`application.namespace("sessions")` creates an application-owned namespaced store during
composition. Namespaces provide deep-copy isolation, bounded capacity, per-entry TTL, and
optimistic compare-and-set versions. Their `snapshot()` output is detached and sorted for
inspection; the namespace identity is read-only after construction. Namespace capacities must be
integers from 1 through 1,000,000, and entry and lease TTLs must be
finite positive numbers; booleans, strings, zero, negative, NaN, and infinite values are
rejected at the Core boundary so an invalid expiry cannot create an effectively permanent entry
or lock. Durable or distributed state providers implement these semantics in adapters.

TTL deadlines are indexed by a min-heap: normal reads and writes inspect only deadlines that are
due instead of scanning every live key. Refreshes and deletes may leave stale index records, but
unique tokens prevent those records from expiring a newer value, and periodic compaction bounds
their retained count. Expiry is observable state mutation and advances the namespace revision once
when one or more entries are reclaimed.

`StateNamespace.transaction()` provides optimistic multi-key updates: sets and deletes commit
under one lock and one namespace revision. A stale base revision or capacity violation aborts
the entire transaction, including value-detachment failures. A staged entry's TTL countdown begins
at commit, so time spent preparing the transaction does not consume its live retention period.
State revisions advance only after every staged `StateEntry` has been constructed
successfully, and a failed commit closes the transaction so it cannot be retried against a
possibly changed snapshot. A transaction that only deletes missing keys is a revision-preserving
no-op. A transaction
also applies Core's one-million distinct-change ceiling while staging, so an oversized mutation
cannot consume unbounded memory before commit-time capacity validation.

The `StateProvider` protocol defines the async adapter boundary for durable backends: namespaced
get/set/delete operations accept TTL and expected-version checks, and `close()` releases backend
resources. The in-memory provider validates keys and expected versions before namespace lookup for
reads and deletes, and validates write keys, TTLs, and expected versions before creating a namespace,
so malformed operations cannot become silent missing-namespace no-ops or consume namespace
capacity. Core does not select a database or distributed coordination implementation.

`StateCoordinator` defines lease-based distributed coordination independently of storage. A
successful acquire returns an opaque ownership token and monotonic expiry; lease identity, token,
deadline and method inputs are validated before state is inspected, renew requires the exact
current token, and release cannot be forged. `InMemoryStateCoordinator` provides bounded
single-process semantics for local development and adapter contract tests; its distinct-key
cardinality is bounded and expired leases are reclaimed before acquire, renew, or release
capacity/ownership decisions. Distributed adapters must preserve these ownership and expiry
guarantees across failures.

`InMemoryStateProvider` implements the async `StateProvider` contract over isolated
`StateNamespace` instances. It is useful for local operation and adapter contract tests; values
are deep-copied, TTL is enforced on access, versions support optimistic writes/deletes, and
`close()` rejects further operations. Namespace count and per-namespace entry capacity are bounded
to at most 1,000,000 each,
and missing reads/deletes do not allocate a new namespace. Production adapters remain responsible
for durability and cross-process consistency.
