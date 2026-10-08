# Support Matrix

Which Phorge and Phabricator versions phabfive works with, and what does not work where. Every row here comes from running phabfive's test suites against that version, see [How This Is Tested](#how-this-is-tested).

Last measured on 2026-10-08, [run 37768364218](https://github.com/dynamist/phabfive/actions/runs/37768364218).

## Versions

| Version | PHP it runs on | phabfive |
|---------|----------------|----------|
| Phorge `master` | 8.5 | Supported |
| Phorge `stable` | 8.5 | Supported |
| Phorge 2026.27 | 8.5 | Supported |
| Phorge 2025.51 | 8.5 | Supported, except creating milestones ([T16605](https://we.phorge.it/T16605)) |
| Phorge 2025.18 | 8.4 | Supported, except creating milestones ([T16605](https://we.phorge.it/T16605)) |
| Phorge 2023.32 to 2024.35 | 8.3 | Supported, except creating milestones ([T16605](https://we.phorge.it/T16605)) |
| Phorge 2022.37 to 2023.23 | 8.0 | Supported, except creating milestones ([T16605](https://we.phorge.it/T16605)) |
| Phabricator (`stable` and `master`, as Phacility left it in 2022) | 8.0 | Supported, except creating milestones ([T16605](https://we.phorge.it/T16605)) |

Phacility still hosts Phabricator for some customers. The `phabricator-stable` version tested here is its public `stable` branch, and a hosted instance answers the same Conduit methods with the same constraints, but may carry patches that were never published.

## Known Gaps

What fails, on which versions, and why. Everything not listed works on every version above: tasks (search, show, create, edit, batch edit), projects (search, show, audit), repositories (list, show), pastes, users, passphrase, spaces, specs, and every output format.

Phabricator and Phorge before 2025.51 have no `status` constraint on `project.search` or `diffusion.repository.search`, and report no status in a `project.search` result. For projects, phabfive sends the constraint all the same, and when it is refused remembers that for the rest of the run and filters on the client instead, reading which projects are archived from `project.query`. A project search that asks for one status may then read every project rather than stop at its limit. Repositories are always filtered on the client, on the `status` every version reports, so they never send the constraint.

| What | Fails on | Why |
|------|----------|-----|
| `project create --milestone-of` | Phabricator, Phorge before 2026.27 | Phorge answers HTTP 500, [T16605](https://we.phorge.it/T16605), fixed upstream in 2026.27 |
| A repository's link is `/source/<name>/` | Phabricator, Phorge before 2026.27 | There is no `browseUri` field, and phabfive links `/R<id>` instead, which works the same |
| A task title over 255 characters is refused | Phabricator, Phorge before 2024.35 | These versions accept it, so a create spec that relies on the refusal creates the task |

A repository's `Hosted` works everywhere, by another route before 2025.51: those versions have no `isHosted` in `diffusion.repository.search`, so phabfive asks the frozen `repository.query`, which reports the same stored flag. Were that ever unavailable, `Hosted` is empty (`null` in JSON) rather than `false`.

## How This Is Tested

The `Kubernetes` workflow deploys a Phorge, or Phabricator, built from upstream at a branch or tag, on the newest PHP it runs on, and runs the `tests/k8s` and `tests/e2e` suites against it. Each version uploads its JUnit results as the artifact `test-results-<version>`. [Phorge Setup](phorge-setup.md#phorge-versions) explains how the versions are built.

- Every pull request tests `stable`, or the versions of its `ci:phorge-*` and `ci:phabricator-*` labels.
- The weekly run tests `stable`, `master`, the two newest Phorge releases and `phabricator-stable`.
- A full measurement tests every version:

```bash
versions="stable master phabricator-stable phabricator-master $(git ls-remote --tags --refs https://github.com/phorgeit/phorge.git | sed -n 's|.*refs/tags/||p' | grep -E '^[0-9]{4}\.[0-9]+$' | sort -V | tr '\n' ' ')"
gh workflow run k8s.yml -f versions="$versions"
```

When the run is done, `scripts/support_matrix.py` turns it into a grid of the tests that do not pass on every version, and shows the end of the log of any version that did not deploy. Write this page from that grid:

```bash
python3 scripts/support_matrix.py <run id>
```

A test that fails on an older version because that version lacks something is expected to fail there, through `missing_before()` in `tests/phorge_versions.py`, rather than skipped, so a gap that closes shows up as an unexpected pass.
