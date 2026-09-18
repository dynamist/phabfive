# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

phabfive is a CLI for Phabricator and Phorge. It provides commands for interacting with Passphrase, Diffusion, Paste, User, and Maniphest applications.

## Common Commands

```bash
# Install dependencies
uv sync --group dev

# Run the CLI
uv run phabfive --help

# Run tests
uv run pytest                        # quick test with current Python
uv run pytest tests/test_foo.py      # run single test file
uv run pytest -k test_name           # run specific test
uv run tox                           # test all Python versions (3.10-3.14)

# Lint and format
uv run ruff check phabfive/ tests/
uv run ruff format phabfive/ tests/

# Local Phorge instance for testing, in the shared k3d cluster (see docs/phorge-setup.md)
make up                              # create/reuse the cluster, build and deploy Phorge
make down                            # stop, keep data
make reset                           # delete the phorge namespace and its data
make test-k8s                        # smoke, seed data and isolation tests against the cluster
make test-e2e                        # end-to-end tests of the CLI against the deployed Phorge

# Test against local Phorge (safe to run data-altering operations)
PHAB_URL=http://phorge.localhost/api/ PHAB_TOKEN=api-supersecr3tapikeyfordevelop1 uv run phabfive ...

# Merge PRs (rebase only - merge and squash are disabled)
gh pr merge --rebase --delete-branch
```

## Pre-commit Checks

**IMPORTANT: Always run before committing and pushing:**

```bash
uv run ruff check phabfive/ tests/
uv run ruff format phabfive/ tests/
```

CI will fail if files are not properly formatted. Run these commands before every commit to avoid CI failures.

## The Kubernetes Check Is Gated

`Kubernetes / Deploy and test in k3d` builds a k3d cluster and a Phorge image, so it costs about
four minutes, and `coexistence` far more. The `decide` job in `.github/workflows/k8s.yml` decides
whether a pull request pays for it:

- a push to `main` or a `workflow_dispatch` run always deploys
- the `ci:k8s` label always deploys, draft or not
- a draft pull request otherwise never deploys
- otherwise it deploys only when the pull request touches `k8s/`, `phorge/`, `tests/k8s/`,
  `tests/e2e/`, `phabfive/`, `Makefile`, `mise.toml`, `pyproject.toml`, `uv.lock` or
  `.github/workflows/k8s.yml`

The job summary always states which rule fired. So **a green pull request does not mean the CLI was
exercised against a real Phorge** - check whether `Deploy and test in k3d` ran, and add `ci:k8s` if
you need it:

```bash
gh pr edit <number> --add-label ci:k8s
```

The label is the manual override rather than an `/e2e-test` comment on purpose: it is sticky, so it
survives later pushes and "Re-run failed jobs", and it keeps the safe `pull_request` event. An
`issue_comment` trigger would run the default branch's copy of the workflow with a writable token
and report no check on the pull request. If a comment command is ever wanted, it should only add
this label, not run the job.

`Validate manifests` is never gated - it is five seconds, and it is what catches a broken overlay
on a pull request that skips the deployment.

## Architecture

### CLI Layer (`cli/`)
- Uses `typer` for argument parsing with built-in shell completion
- Modular structure: `__init__.py` (main app), `maniphest.py`, `diffusion.py`, `paste.py`, `user.py`, `passphrase.py`, `repl.py`
- Monogram shortcuts via `preprocess_monograms()`: `phabfive T123` expands to `phabfive maniphest show T123`
- Entry point: `cli_entrypoint()`
- Shell completion: `phabfive --install-completion bash|zsh|fish`

### Core Layer (`core.py`)
- `Phabfive` class: central configuration and API client management
- Loads config from `.arcconfig`, `~/.arcrc`, `~/.config/phabfive.yaml`, and environment variables
- Uses the `phabricator` library for Conduit API calls
- Output formatting: `rich` and `tree` (terminal), `yaml`, `json` and `jsonl`
  (machine-readable), `simple` (bare value). `strict` is an alias for `yaml` and
  `ndjson` for `jsonl`, both rewritten in argv by `preprocess_format_alias()`
- Every JSON emitter goes through `phabfive/json_output.py`, which is what keeps
  `json` and `jsonl` emitting the same records from the same builders

### Feature Modules
Complex features use a consistent subpackage structure:
- `diffusion/`, `maniphest/`, and `passphrase/` follow this pattern:
  - `core.py` - main class inheriting from `Phabfive`
  - `display.py` - output formatting (passphrase)
  - `fetchers.py` - API data fetching (diffusion, maniphest)
  - `resolvers.py` - ID/name resolution (diffusion, maniphest)
  - `formatters.py` - output formatting (diffusion, maniphest)
  - `validators.py` - input validation (diffusion, maniphest)

### Simpler Modules
- `paste.py`, `user.py` - single-file implementations
- `transitions/` - state machine for task status/priority/column changes

### Completion Cache (`cache.py`)

- Server-backed shell completions are cached under `appdirs.user_cache_dir("phabfive")`,
  keyed by instance URL and token, with per-namespace TTLs from `CACHE_TTLS`
- Caching is **opt-in per call site**: `phabfive/cache.py` is called only from
  `cli/completers.py` and `cli/cache.py`. Nothing wraps the API client, which is
  what keeps passphrase values out of the cache by construction. Do not add a
  transparent wrapper.
- `Phabfive.read_config()` is a classmethod so the cache can key entries by
  `PHAB_URL` without constructing `Phabfive()`, which would cost two round trips
  (`update_interfaces` + `verify_connection`) on every completion
- Every cache operation is best effort — a miss must never raise, or completion
  breaks
- A cached call site must use a fetch that **fails** on error. Helpers that
  answer a failure with invented defaults (`get_api_status_map`) must not be
  cached through, or the defaults get stored as if the server had said them —
  which is why completion has its own `_fetch_status_keys`
- `_get_board_columns` is the one API-backed completion that is deliberately
  not cached: it builds its own `Phabfive()` instead of going through
  `_get_values_with_api_fallback`, so it needs that refactor first
- `tests/conftest.py` disables the cache for the whole suite and provides the
  `enabled_cache` fixture that tests wanting it opt in with

### Configuration

Required: `PHAB_TOKEN` and `PHAB_URL`

Config precedence (later overrides earlier):
1. Hard-coded defaults
2. `/etc/phabfive.yaml`
3. `/etc/phabfive.d/*.yaml`
4. `~/.config/phabfive.yaml` (PHAB_URL/PHAB_TOKEN deprecated here, use for PHAB_SPACE/PHAB_FALLBACK/PHAB_CACHE/PHAB_CACHE_TTL/PHAB_CACHE_DIR)
5. `~/.config/phabfive.d/*.yaml`
6. `.arcconfig` in git root (provides PHAB_URL from `phabricator.uri`)
7. `~/.arcrc` (provides PHAB_TOKEN for matched URL)
8. Environment variables

## AI Agent Usage

### UX Consistency Between Phorge Apps

When implementing new Phorge apps (countdown, paste, maniphest, etc.), **verify UX consistency** with existing apps:

1. **Compare CLI options**: Check that similar commands have similar options
   - Example: `maniphest show` vs `paste show` should have similar flags
   - Don't add options that don't exist in similar commands (e.g., `--show-description` when maniphest doesn't have it)

2. **Match output format structure**: Use the same nested structure as maniphest
   - Use `"Paste":` section like maniphest uses `"Task":` section
   - Fields should be capitalized (`Name`, `Status`, not `name`, `status`)
   - Author goes in the nested section, not at the top level

3. **Verify by comparing help output**:
   ```bash
   phabfive maniphest show --help
   phabfive paste show --help
   # These should have similar structure and options
   ```

4. **Reference maniphest as the canonical UX pattern** for Phorge apps - it's the most complete implementation

5. **Verify API field names**: Different Phorge apps use different API field names
   - maniphest: `fields.name`, transaction type `name`
   - paste: `fields.title`, transaction type `title`
   - description is often `fields.description.raw` (a dict, not a string)

6. **Verify search constraint names per endpoint**: `*.search` constraints are
   named per application and are not interchangeable
   - author filter: `paste.search` uses `authors`, `maniphest.search` uses `authorPHIDs`
   - assignee filter: `maniphest.search` uses `assigned`; `paste.search` has none
   - a wrong key fails with `ERR-INVALID-CONSTRAINT`, so check it against the
     instance before assuming another app's name carries over:
     ```bash
     curl -s "$PHAB_URL/paste.search" -d "api.token=$PHAB_TOKEN" \
       -d 'constraints[authors][0]=PHID-USER-...'
     ```

7. **Display labels should match the Phorge web UI**, not the API field names
   - The web UI uses "Name" across all apps for the title/name field
   - So display `"Name": fields.get("title", "")` for paste (API field is "title", display label is "Name")

### Data-Altering Operations

**NEVER run create, edit, or delete operations on production instances.** Only run data-altering commands when explicitly overriding credentials with environment variables pointing to a test instance.

When using the local Phorge development instance, it is **safe to run data-altering operations** (create, edit, delete) without `--dry-run`:

```bash
PHAB_URL=http://phorge.localhost/api/ PHAB_TOKEN=api-supersecr3tapikeyfordevelop1 uv run phabfive maniphest create "Test task"
PHAB_URL=http://phorge.localhost/api/ PHAB_TOKEN=api-supersecr3tapikeyfordevelop1 uv run phabfive maniphest edit T123 --status=resolved
```

This is a disposable test environment. These specific credentials indicate a safe-to-modify development instance.

### Machine-Readable Output

Use `--format=yaml`, `--format=json` or `--format=jsonl` for machine-readable output:

```bash
# Get task details as YAML
phabfive --format=yaml T123

# Get task details as JSON
phabfive --format=json T123

# Search tasks with structured output
phabfive --format=json maniphest search --tag=projectname

# Pipe JSON to jq
phabfive --format=json T123 | jq '.Task.Title'
```

`jsonl` emits the same objects as `json` but one per line with no wrapping array
(https://jsonlines.org/), flushed as each is written. Use it when a reader consumes
records one at a time, or when appending to a file:

```bash
# One object per line, straight into jq -c
phabfive --format=jsonl maniphest show T123 T456 | jq -c '.Task.Name'

# Counting lines counts records - nothing is ever split across lines
phabfive --format=jsonl maniphest search --tag=projectname | wc -l

# Append a run to a log
phabfive --format=jsonl maniphest search --assigned=@me >> tasks.jsonl
```

`--format=ndjson` is accepted as a spelling of `jsonl`. `PHAB_FALLBACK` sets the format
used when stdout is not a TTY and accepts `yaml`, `json` and `jsonl`.

## Dependency Updates

Renovate (Mend app, `renovate.json`) is the only bot, batching everything into one Monday window.

- Runtime deps under `[project.dependencies]` keep loose `>=` floors: phabfive ships as a wheel and
  must not over-constrain consumers. Renovate never bumps them; `lockFileMaintenance` (weekly
  `uv lock --upgrade`) is what keeps the resolved versions and transitive deps current.
- Dev/test/docs tooling lives only in `[dependency-groups]` (`uv sync --group dev`, and tox installs
  the `test` and `docs` groups), never as a published extra - `repl` stays an extra because
  ptpython is a runtime opt-in a user installs with `phabfive[repl]`, and uses `rangeStrategy: bump` so the floors track the
  revs pinned in `.pre-commit-config.yaml`. That is what makes the `ruff` and `uv` groups update
  both files in one PR.
- `minimumReleaseAge: "5 days"` exists to stay behind `[tool.uv] exclude-newer = "4 days"` in
  `pyproject.toml`. uv resolves as if four days ago, so a fresher version would be proposed but
  could not be locked. Change the two together or lock file updates start failing.
- `k8s/cluster/k3d.yaml` is shared verbatim with every repo in the `dynamist-dev` cluster, and the
  `coexistence` job diffs it, so `kubectl` (mise) and the k3s image move together in the
  `kubernetes toolchain` group - and the PR has to be merged in every such repo in the same window.
- `ruff` is capped below 0.16 in both `pyproject.toml` and the `ruff` group's `allowedVersions`
  until #322. 0.16 widened the default rule set and there is no explicit `[tool.ruff] select`, so
  it reports 385 errors against a tree that 0.15.x calls clean. Lift both at the same time.
- `dynamist/phorge` is built from this repo and tagged at deploy time, so it is disabled.
- `.github/workflows/drift.yml` covers what the lock file structurally cannot: it installs
  unlocked with plain `pip` on a weekly schedule and runs `scripts/smoke.py`, so a release too
  new for `minimumReleaseAge`/`exclude-newer` is still seen. It opens an issue rather than just
  failing. If those two floors are ever removed, this job's remaining value is only that it does
  not depend on Renovate running at all.
- Leave `osvVulnerabilityAlerts` off. The hosted app cannot download the OSV database, so it only
  logs "Unable to read vulnerability information" as a repository problem
  (renovatebot/renovate#22502).

## Version Management

Version is defined only in `pyproject.toml`. Access it via:
```python
from importlib.metadata import version
version("phabfive")
```

## Release Workflow

Releases are triggered by pushing a git tag matching `v*`:

```bash
git tag -a v0.7.0 -m "Release v0.7.0"
git push origin v0.7.0
```

**Artifacts produced:**
- Python wheel and sdist → PyPI
- Standalone executables for 6 platforms → GitHub Releases:
  - `phabfive-linux-amd64`, `phabfive-linux-arm64`
  - `phabfive-macos-amd64`, `phabfive-macos-arm64`
  - `phabfive-windows-amd64.exe`, `phabfive-windows-arm64.exe`
- Sigstore signatures (`.sigstore.json`) for all executables except Windows ARM64
- Scratch container image `ghcr.io/dynamist/phabfive` (`linux/amd64`, `linux/arm64`) holding
  phabfive and a uv-managed Python under `/opt/phabfive`, meant to be copied into other images:
  - glibc tags: `X.Y.Z`, `X.Y`, `latest`
  - musl tags: `X.Y.Z-musl`, `X.Y-musl`, `latest-musl`
  - signed with cosign; build locally with `make image` or `make image LIBC=musl`

**RC tags** (containing `-rc`) skip PyPI and the `X.Y`/`latest` image tags, but still build executables, push the image and create GitHub releases marked as prereleases.
