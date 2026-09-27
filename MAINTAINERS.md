# Maintainers

The maintainers listed by the repository's [CODEOWNERS](.github/CODEOWNERS) file are responsible for
the Core contract, project governance, and release process. The team reviews changes to lifecycle,
dependency injection, configuration, state, routing, security, hosting, and plugin boundaries.

## Maintainer directory

| Name | ID | Company / Organization |
| --- | --- | --- |
| Orbit Maintainers (interim) | `@orbit-projects/maintainers` | Orbit Project |

The repository currently uses a team identity while the project is being organized. Individual
maintainer names, public IDs, and organizational affiliations should be added here as people take
on continuing maintainer responsibilities. This directory is intentionally separate from the
CODEOWNERS mechanism so governance remains readable to contributors.

## Decision making

Maintainers evaluate changes against the documented architecture and public contract. A change that
introduces or revises an architectural convention requires an ADR. Backward-incompatible changes
must identify the affected API, migration path, deprecation period, and release impact.

## Responsibilities

- Review pull requests and keep the supported Python and dependency ranges current.
- Triage issues and security reports, including coordinated disclosure of vulnerabilities.
- Maintain CI, release automation, documentation, and the changelog.
- Keep operator-facing guarantees aligned with implementation and test evidence.

Maintainer access does not make local test results a production certification. Deployment owners
remain responsible for provider, infrastructure, load, and security evidence.
