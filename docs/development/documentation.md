# Documentation conventions

Orbit's documentation is part of its public API. It describes the guarantees that Core makes,
the responsibilities delegated to hosts and plugins, and the evidence required before a deployment
can make stronger claims. Keep prose precise enough that an operator can distinguish a contract from
an implementation detail or an unverified assumption.

The [project charter](../architecture/project-charter.md) is the normative source for Orbit's
identity and target architecture. Current guides must label planned integrations, ecosystem goals,
and deployment evidence separately from behavior implemented in this repository. The public
[roadmap](../../ROADMAP.md) records gaps instead of allowing aspirational architecture to read as
a shipped guarantee.

## Source documentation

Every maintained Python module has a module docstring stating its responsibility and architectural
boundary. Public classes, functions, and methods have concise docstrings that describe observable
behavior, important arguments, return values, and failure conditions. Use the vocabulary established
by the package: `Application`, `Runtime`, `Container`, `Plugin`, `Service`, and `State` are distinct
owners and should not be used interchangeably.

Comments are reserved for decisions that are not apparent from the code: ownership, ordering,
cancellation, security boundaries, compatibility behavior, or an invariant required by a race-free
implementation. A comment should answer why the code has a constraint. Remove stale comments when
the implementation changes. Do not leave TODO, FIXME, HACK, or WIP markers in maintained source.

Structured Core mappings are detached and recursively immutable. Their nested containers must stay
within the documented depth and work budget; cycles are invalid input rather than a supported data
model, because they cannot be serialized safely through diagnostics, administration, or events.

Examples must be executable in principle, use public APIs, and make lifecycle ownership explicit.
They must not embed credentials, provider payloads, or claims that a process-local reference
implementation is durable or suitable for every deployment.

## Markdown documentation

Each Markdown file starts with one H1 heading, uses headings to expose the document structure, and
keeps paragraphs focused on one contract or operational decision. Use fenced code blocks for commands
and complete examples. Prefer links to repository documents over duplicated policy text; local links
must remain valid when files move.

Document a public change in the guide closest to its owner. Update the architecture overview or add
an ADR when ownership, lifecycle, dependency direction, hosting, or security assumptions change.
Update the changelog for user-visible behavior and the release gate when validation evidence changes.

## Validation

Run `python scripts/check-documentation.py` to validate module and public API docstrings, unresolved
comment markers, Markdown headings, fenced blocks, and local links. Run the license-header checker
separately; documentation quality and licensing are independent repository requirements.
