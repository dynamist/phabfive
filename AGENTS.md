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

# Local Phorge instance for testing
make up                              # start local Phorge
make down                            # stop containers

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
- Output formatting: supports `rich` (terminal), `yaml`, and `strict` (machine-readable) modes

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

Use `--format=yaml` or `--format=json` for machine-readable output:

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

**RC tags** (containing `-rc`) skip PyPI but still build executables and create GitHub releases.
