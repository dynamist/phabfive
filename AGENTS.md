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
gh pr merge --rebase --delete-branch
```

## Architecture

### CLI Layer (`cli/`)
- Uses `typer` for argument parsing with built-in shell completion
- Modular structure: `__init__.py` (main app), `maniphest.py`, `diffusion.py`, `paste.py`, `user.py`, `passphrase.py`, `repl.py`
- Monogram shortcuts via `preprocess_monograms()`: `phabfive T123` expands to `phabfive maniphest show T123`
- Entry point: `cli_entrypoint()`
- Shell completion: `phabfive --install-completion bash|zsh|fish`

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

## AI Agent Usage

Use `--format=strict` for machine-readable YAML output:

```bash
# Get task details as YAML
phabfive --format=strict T123

# Search tasks with structured output
phabfive --format=strict maniphest search --tag=projectname

# Pipe to yq for JSON conversion
phabfive --format=strict T123 | yq -o json
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
