# Repository governance and branch protection

Orbit uses pull requests, review, automated checks, and protected default branches as part of its
open-source supply-chain boundary. The GitHub ruleset is configured in repository settings; this
document records the intended policy so it can be reviewed and reproduced by maintainers.

## Recommended ruleset

Create an active ruleset named `main-protection` targeting the `main` branch. Keep the bypass list
empty during normal operation. Emergency bypass access, if needed, should be limited to a small
maintainer role and documented in the incident or release record.

| Rule | Recommendation | Reason |
| --- | --- | --- |
| Restrict creations | Disabled | Maintainers may create the protected default branch during repository setup. |
| Restrict updates | Disabled | Updates should be possible through approved pull requests and the merge mechanism. |
| Restrict deletions | Enabled | The default branch and its history must not be casually removed. |
| Require linear history | Enabled if squash/rebase merging is the project norm | Keeps the public history easy to audit. |
| Require a pull request | Enabled | Direct pushes bypass review and automated evidence. |
| Require status checks | Enabled | Every change needs the full quality matrix before merge. |
| Block force pushes | Enabled | Prevents rewriting reviewed public history. |
| Require signed commits | Adopt progressively | Useful supply-chain evidence, but contributor onboarding must be documented first. |
| Require deployments | Disabled until a real deployment environment exists | Do not configure a check that has no reproducible environment. |
| Require code scanning | Enable after hosted CodeQL results are verified | CodeQL must have results for both the commit and target branch. |
| Require code quality | Enable when a hosted quality provider is configured | Keep severity thresholds explicit and reviewable. |
| Restrict code coverage | Enable after coverage upload is configured | The repository's local coverage threshold is 90%; the ruleset needs uploaded PR data. |
| Merge queue | Optional after contributor volume justifies it | Add only when the queue is actively maintained and required checks are stable. |

Do not select a status check by memory. Open a successful pull request after CI is installed and
select the exact check names GitHub reports. At minimum, the matrix should cover Python 3.11,
3.12, 3.13, and 3.14. CodeQL and Scorecard should be required only after their hosted workflows
have produced reliable pull-request results; scheduled-only evidence is not enough for a merge
gate.

As of 2026-09-27, the public repository has an active `main-protection` ruleset, but its required
status-check list is empty. After the current Core changes are pushed, a maintainer must open a
successful pull request and add the exact matrix check names reported by GitHub (currently expected
to be `quality (3.11)`, `quality (3.12)`, `quality (3.13)`, and `quality (3.14)`). This is a
repository-settings action, not something the local workflow files can enforce by themselves.

## Required repository workflows

The pull-request quality workflow is expected to run:

- Ruff lint and formatting;
- strict Mypy type checking;
- the full test suite and coverage threshold;
- documentation and local-link checks;
- license-header checks;
- package build and distribution-integrity validation;
- dependency vulnerability auditing.
- explicit lockfile synchronization validation before the quality matrix runs.

Security workflows add CodeQL and OpenSSF Scorecard; Scorecard runs on pull requests, pushes to
`main`, and its scheduled audit. Release workflows additionally build a CycloneDX SBOM, record
SHA-256 checksums, and request GitHub build provenance for the uploaded artifact bundle. A green
ruleset check proves only that the configured workflow passed; it does not certify a provider
plugin, cloud deployment, or production workload.

## Contributor expectations

Every pull request should identify the owning Core boundary and include the corresponding tests,
documentation, public API docstrings, and explanatory comments for non-obvious invariants. Comments
should explain ownership, ordering, cancellation, security, compatibility, or concurrency rules;
they should not narrate obvious syntax. Architectural changes require an ADR, and changes to a
ruleset should be recorded in the repository governance documentation and maintainer notes.
