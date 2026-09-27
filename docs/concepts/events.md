# Events

`Event` carries validated correlation and causation IDs, an optional printable delivery key for
idempotency, an aware occurrence timestamp, and a bounded priority value. `EventBus` delivers typed events to exact-name
subscribers by descending subscriber priority, preserving registration order for ties.
`max_concurrency` bounds handler execution across concurrent publishes and provides an in-process
backpressure limit.
`max_subscribers` also bounds registration and task allocation for one bus.
The bus's history, concurrency, subscriber, retry, and in-memory event-store capacity controls
accept values only through 1,000,000, keeping long-lived resource requests explicit.
Distributed event systems belong in adapters and plugins.

Subscriber callbacks, runtime payload types, predicates, dead-letter handlers, failure policies
and numeric retry or backoff settings are validated when registered. Event envelopes, subscription
handles, delivery records, replay arguments and predicate return values are validated at their
public boundaries, including when an integration constructs a diagnostic record directly.
Invalid values fail composition or invocation rather than appearing as incidental attribute or
adapter errors inside event delivery.

Subscribers may declare a bounded `max_retries` budget with exponential backoff using
`backoff_initial` and `backoff_multiplier`; each retry delay is capped by the bus timeout. A terminal failure normally contributes to the
published `ExceptionGroup`; `FailurePolicy.DEAD_LETTER` routes it to an explicit callback and
keeps the publication successful when that callback succeeds. Delivery history contains only
event identity and failure counts, never payloads. Handler and dead-letter callbacks share the
bus timeout, and cancellation remains immediate. A callback that suppresses cancellation is
detached at the deadline and tracked per subscription; later publications fail closed for that
subscription until the callback exits, preventing orphan-task accumulation.

When an event has a `delivery_key`, the bus retains a bounded `(event name, key)` index and
acknowledges replayed publications without invoking subscribers again. The bounded index follows
the delivery history size; `history_size=0` disables both local delivery-key retention and delivery
history, so durable cross-process idempotency belongs in a transport adapter.

`EventTransport` is the async adapter boundary for durable or cross-process delivery. It
defines publish, subscribe, unsubscribe, and close ownership while leaving persistence,
ordering, replay, and backpressure guarantees explicit to each adapter.

Subscribers can provide a synchronous `predicate` to select events by metadata or payload.
The predicate runs before the handler and filtered events are acknowledged without invoking
that subscriber. Predicate errors are aggregated like handler errors and do not prevent other
subscribers from receiving the event.

Subscribers may declare a strict integer priority from 0 to 100. Higher-priority subscribers run
first; equal priorities retain registration order. Event envelope priorities are strict integers as
well, so boolean or numeric-string values cannot silently alter delivery ordering.

`EventStore` defines the persistence boundary for durable delivery: append returns a monotonic
cursor, reads replay validated `StoredEvent` results after a cursor with optional name filtering, and pruning makes
retention explicit. Append detaches the event before allocating its cursor, so a failed retention
copy cannot consume a sequence or partially persist state; reads select the bounded result window
before detaching payloads. `InMemoryEventStore` is a bounded implementation for local development and
adapter contract tests; its retained event capacity is limited to 1,000,000. Production deployments should supply a store backed by their chosen
durable system and document its ordering, transaction, and backpressure guarantees.

`EventBus(store=...)` persists a detached event before subscriber delivery and exposes
`replay(after=..., event_name=..., limit=...)` for inspection or recovery. Replay is read-only and
does not invoke subscribers. The application that constructs a store owns its shutdown and
retention policy; the bus remains compatible with stores backed by durable or cross-process
transports. `aclose()` rejects new publications, waits for in-flight persistence and delivery, and
gives store cleanup the same finite timeout. The close operation is shared and shielded from
caller cancellation; a cancelled owner receives `CancelledError` only after the bus-owned cleanup
finishes. Deferred cleanup also has a finite wait for stubborn in-flight publishes; a later
`aclose()` retries store cleanup once ownership is released. A store that suppresses cancellation
remains in one detached cleanup operation rather than being invoked concurrently; its eventual
result is consumed without loop-level warnings.

The Core event envelope and its metadata mapping are recursively protected after validation.
Metadata keys are printable, nonempty, at most 255 characters, and limited to 2,048 entries;
this keeps operator-facing event inspection bounded while leaving metadata values provider-neutral.
Application payloads may still contain provider-defined mutable Python values, so the bus and
`InMemoryEventStore` deep-copy events at delivery and retention boundaries. This prevents a
subscriber or the original publisher from changing another component's view of an event after
handoff.
