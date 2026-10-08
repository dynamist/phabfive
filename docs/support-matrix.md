# Support Matrix

Which Phorge and Phabricator versions phabfive works with, and what does not work where. Every row here comes from running phabfive's test suites against that version, see [How This Is Tested](#how-this-is-tested).

Last measured on 2026-09-28, [run 36415788571](https://github.com/dynamist/phabfive/actions/runs/36415788571).

## Versions

| Version | PHP it runs on | phabfive |
|---------|----------------|----------|
| Phorge `master` | 8.5 | Supported |
| Phorge `stable` | 8.5 | Supported |
| Phorge 2026.27 | 8.5 | Supported |
| Phorge 2025.51 | 8.5 | Supported, except creating milestones (a Phorge bug) |
| Phorge 2022.37 to 2025.18 | 8.0 to 8.4 | Partly, see [Known Gaps](#known-gaps) |
| Phabricator (`stable` and `master`, as Phacility left it in 2022) | 8.0 | Partly, see [Known Gaps](#known-gaps) |

Phacility still hosts Phabricator for some customers. The `phabricator-stable` version tested here is its public `stable` branch, and a hosted instance answers the same Conduit methods with the same constraints, but may carry patches that were never published.

## Known Gaps

What fails, on which versions, and why. Everything not listed works on every version above: tasks (search, show, create, edit, batch edit), pastes, users, passphrase, spaces, specs, and every output format.

| What | Fails on | Why |
|------|----------|-----|
| `diffusion repo list`, `diffusion repo show`, `project audit`, and search specs of projects or repositories | Phabricator, Phorge before 2025.51 | phabfive sends a `status` constraint to `project.search` and `diffusion.repository.search`, which these versions do not have |
| The `Hosted` field of a repository | Phabricator, Phorge before 2025.51 | There is no `isHosted` field, and phabfive reports such a repository as not hosted |
| A policy change that would lock you out is explained in one sentence | Phabricator, Phorge before 2025.51 | The change is refused either way, but these versions word the error differently, and phabfive shows it as it is |
| `project create --milestone-of` | Phabricator, Phorge before 2026.27 | Phorge answers HTTP 500, a bug fixed upstream in 2026.27 |
| A repository's link is `/source/<name>/` | Phabricator, Phorge before 2026.27 | There is no `browseUri` field, and phabfive links `/R<id>` instead, which works the same |

## How This Is Tested

The `Kubernetes` workflow deploys a Phorge, or Phabricator, built from upstream at a branch or tag, on the newest PHP it runs on, and runs the `tests/k8s` and `tests/e2e` suites against it. Each version uploads its JUnit results as the artifact `test-results-<version>`. [Phorge Setup](phorge-setup.md#phorge-versions) explains how the versions are built.

- Every pull request tests `stable`, or the versions of its `ci:phorge-*` and `ci:phabricator-*` labels.
- The weekly run tests `stable`, `master`, the two newest Phorge releases and `phabricator-stable`.
- A full measurement tests every version:

```bash
versions="stable master phabricator-stable phabricator-master $(git ls-remote --tags --refs https://github.com/phorgeit/phorge.git | sed -n 's|.*refs/tags/||p' | grep -E '^[0-9]{4}\.[0-9]+$' | sort -V | tr '\n' ' ')"
gh workflow run k8s.yml -f versions="$versions"
```

A test that fails on an older version because that version lacks something is expected to fail there, through `missing_before()` in `tests/phorge_versions.py`, rather than skipped, so a gap that closes shows up as an unexpected pass.
