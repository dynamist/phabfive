# Development Guide

This guide covers everything you need to contribute to phabfive.

## Quick Start

Get from zero to working development environment in 2 steps:

```bash
# 1. Set up development environment
make install         # or: uv sync --group dev

# 2. Start local Phorge with demo data (recommended)
make phorge-setup    # Starts Phorge + creates ~70 demo tasks

# OR just start Phorge without demo data
make phorge-up       # Clean slate
```

The `phorge-setup` target gives you a fully populated Phorge instance with:
- 3 Spaces (Public, Internal, Restricted)
- 7 Projects with workboards
- ~70 tasks distributed across columns
- Realistic task history for testing

## Installation in a Development Environment

This project uses [uv](https://github.com/astral-sh/uv) for dependency management:

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repository
git clone https://github.com/dynamist/phabfive.git
cd phabfive

# Create virtual environment and install dependencies
uv sync --group dev

# Run the CLI during development
uv run phabfive --help
```

## Build and Run in a Docker/Podman Image

The project includes Makefile targets for Docker/Podman builds (automatically detects which is available):

```bash
# Build the image
make phabfive-build

# Run commands
make phabfive-run ARGS="--help"
make phabfive-run ARGS="paste list"

# Run against local Phorge instance
make phabfive-run-dev ARGS="maniphest search qa"
```

**Configuration:** The Makefile automatically handles your credentials:
- **Environment variables:** `PHAB_TOKEN` and `PHAB_URL` are passed through if set
- **Config file:** Automatically detected and mounted from OS-specific locations:
  - macOS: `~/Library/Application Support/phabfive.yaml`
  - Linux: `~/.config/phabfive.yaml`

## Run Unit Tests

This repo uses `pytest` as the test runner and `tox` to orchestrate tests for various Python versions (3.10-3.13).

**Quick test run:**
```bash
# Run tests with current Python version
uv run pytest

# Run linting
uv run flake8 phabfive/ tests/
```

**Testing across all Python versions:**
```bash
# Run all Python versions
make test
# or
uv run tox

# Run specific environment
uv run tox -e py313
uv run tox -e py310

# Run linting
uv run tox -e flake8

# Run coverage report
uv run tox -e coverage
```

With `tox-uv`, tox automatically uses uv for fast dependency resolution and isolated testing environments.

## Testing Against a Local Phorge/Phabricator Instance

For instructions on setting up a local Phorge or Phabricator instance for testing, see [Phorge Setup Guide](phorge-setup.md).

## Run mkdocs Locally

For documentation updates:

```bash
# Install with docs dependencies
uv sync --extra docs

# Serve the docs
uv run mkdocs serve
```

Navigate to `http://127.0.0.1:8000` to view the rendered documentation.

For more about mkdocs, see the [Mkdocs homepage](https://www.mkdocs.org/).

## Code Style and Linting

This project uses:
- **flake8** for linting
- **pytest** for testing

Before submitting changes:
```bash
# Run linting
uv run flake8 phabfive/ tests/

# Run tests
uv run pytest

# Or run everything with tox
uv run tox
```

The CI will run these checks automatically, but running locally first saves time!
