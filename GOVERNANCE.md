# Orbit governance

Orbit Core is developed in public as a reusable, provider-neutral orchestration framework. This
document describes how technical decisions are made while the project is pre-alpha. It complements
the maintainer directory, contribution guide, code of conduct, security policy, public roadmap,
and repository-protection policy; it does not replace GitHub's repository controls.

## Participation

Anyone may open an issue, propose a design, submit a pull request, review documentation, or
participate in public technical discussion. Contributions must follow the
[Code of Conduct](CODE_OF_CONDUCT.md) and the [contribution guide](CONTRIBUTING.md). Security
reports follow [SECURITY.md](SECURITY.md) and must not be filed as public issues.

## Maintainers

The maintainers listed in [MAINTAINERS.md](MAINTAINERS.md) own release decisions, repository
settings, Core API compatibility, and final pull-request approval. The current directory uses an
interim team identity while individual continuing maintainers are established. It must be updated
with each maintainer's public name, GitHub ID, and organization before that person receives
maintainer responsibilities.

Maintainers are expected to act in the project's interest, disclose material conflicts affecting a
decision, and recuse themselves when impartial review is not practical. A maintainer may step down
by requesting removal from the directory and CODEOWNERS. Adding or removing a maintainer requires
recording the corresponding directory and ownership change in a reviewed pull request.

## Technical decisions

Routine changes are decided through a reviewed pull request with required automated checks. A
maintainer may merge after resolving relevant review feedback and confirming that the change is
within Core's documented boundary.

The following require an Architecture Decision Record (ADR) and at least one maintainer approval:

- a new or materially changed Core convention, public contract, lifecycle owner, or dependency
  direction;
- a compatibility break, deprecation schedule, or supported-Python change;
- a change to the Uvicorn development or Gunicorn-with-Uvicorn-worker production model;
- a security, repository-protection, release-signing, or supply-chain policy change; or
- a decision that moves a provider-specific capability into or out of Core.

When reviewers disagree, maintainers should first seek a written, evidence-backed consensus in the
issue or pull request. If consensus is not reached, the designated Core maintainer makes and records
the decision, including the alternatives considered. Decisions remain open to revision through a
new ADR when operational evidence changes.

## Releases and accountability

Only maintainers may publish releases or alter protected-branch and release settings. A release
requires the repository's documented local and hosted evidence; an open mandatory release gate is
not waived by this policy. Release metadata, changelog entries, SBOMs, checksums, signatures, and
provenance are reviewed as part of the release record.

The [public roadmap](ROADMAP.md) tracks planned work and the
[completion scorecard](docs/development/completion.md) records current Core evidence. Neither is a
promise of a provider integration, production certification, or CNCF status.

## Amendments

Changes to this policy require a public pull request, an ADR when they change a Core convention or
security/release responsibility, and review by a maintainer not solely proposing the change when
one is available.
