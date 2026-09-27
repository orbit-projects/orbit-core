# Reliability primitives

`orbit.reliability` contains provider-neutral building blocks used by Core and by plugins. These
primitives describe behavior; they do not make an unreliable provider reliable without a suitable
timeout, capacity, retry, and recovery policy.

## Deadlines and cancellation

`Deadline` composes a child operation's budget with its parent budget using a monotonic clock.
Its timeout and captured start time must both be finite numeric values.
Every adapter call should derive a child deadline instead of starting an unrelated wall-clock
timeout. When the budget expires, Core raises a timeout error and propagates cancellation. Cleanup
boundaries may shield their own finalization, but must still have a finite upper bound.

## Retry

`RetryPolicy` applies an attempt count from 1 through 1,000,000 and bounded exponential backoff. The policy object and
optional failure classifier are validated before the first attempt, and a supplied classifier remains authoritative
even if its Python truth value is false. The default failure
classifier is conservative: programming errors and explicit cancellation are not retried. Plugins
should classify provider-specific transient errors explicitly and attach an idempotency key before
retrying a write. Backoff is capped at `max_delay`, including when exponentiation for an extreme
finite multiplier would overflow. A retry policy must never exceed the parent deadline.

Retries do not guarantee exactly-once delivery. Event transports, state providers, and external APIs
must document whether an operation is idempotent, deduplicated, or merely at-least-once.

## Circuit breakers and bulkheads

`CircuitBreaker` prevents repeated calls to a failing dependency and permits a single half-open
probe after a recovery delay; its failure threshold is limited to 1,000,000. `Bulkhead` bounds
concurrent operations to at most 1,000,000 and can reject waiters after
a finite queue timeout. Their thresholds, delays, capacities, and queue policies are read-only
after construction, so the semaphore and breaker state cannot be desynchronized by assignment.
Breaker state changes also reject stale completions: a success or failure that began before newer
state changes cannot overwrite the current breaker epoch when it finishes later. A failed
half-open probe reopens the breaker immediately.
These controls are local to one process; distributed coordination belongs
to a plugin and requires an external backend when workers need a shared view.

## Failure policy

Classify failures before choosing a response:

| Class | Typical action |
| --- | --- |
| cancellation | propagate immediately |
| invalid input or programming error | fail without retry |
| transient provider failure | retry within the deadline |
| capacity exhaustion | reject or shed load |
| dependency outage | trip the circuit and report degraded health |
| cleanup failure | record, continue remaining cleanup, aggregate |

Core records bounded diagnostics and health transitions, but it does not hide failures or claim
recovery that a plugin has not confirmed.

Timeouts, attempt counts, concurrency limits, cleanup budgets, metric series and exporter values
are validated at their Core boundaries. Boolean, non-finite, unrepresentably large, and otherwise
mismatched values fail before an operation starts, rather than leaking a platform conversion error
or changing a policy through Python numeric coercion.

Security and audit timestamps use the same rule: a non-`None` `tzinfo` is insufficient when its
`utcoffset()` is `None`; such values are rejected before comparisons or trust decisions.
