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

# Lint
uv run flake8 phabfive/ tests/

# Local Phorge instance for testing
make up                              # start local Phorge
make down                            # stop containers

# Merge PRs (rebase only - merge and squash are disabled)
gh pr merge --rebase
```

## Architecture

### CLI Layer (`cli.py`)
- Uses `docopt-ng` for argument parsing with docstring-based CLI definitions
- Monogram shortcuts allow direct access: `phabfive T123` expands to `phabfive maniphest show T123`
- Entry point: `cli_entrypoint()` → `parse_cli()`

### Core Layer (`core.py`)
- `Phabfive` class: central configuration and API client management
- Loads config from environment variables or `~/.config/phabfive.yaml`
- Uses the `phabricator` library for Conduit API calls
- Output formatting: supports `rich` (terminal), `yaml`, and `strict` (machine-readable) modes

### Feature Modules
Complex features use a consistent subpackage structure:
- `diffusion/` and `maniphest/` follow this pattern:
  - `core.py` - main class inheriting from `Phabfive`
  - `fetchers.py` - API data fetching
  - `resolvers.py` - ID/name resolution
  - `formatters.py` - output formatting
  - `validators.py` - input validation

### Simpler Modules
- `passphrase.py`, `paste.py`, `user.py` - single-file implementations
- `transitions/` - state machine for task status/priority/column changes

### Configuration
Required: `PHAB_TOKEN` and `PHAB_URL` (via environment or config file)

## Version Management

Version is defined only in `pyproject.toml`. Access it via:
```python
from importlib.metadata import version
version("phabfive")
```
