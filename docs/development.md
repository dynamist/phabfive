# Development Guide

This guide covers everything you need to contribute to phabfive.

## Quick Start

Get from zero to working development environment in 2 steps:

```bash
# 1. Set up development environment
make install         # or: uv sync --group dev

# 2. Start local Phorge test instance
make up
```

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

## Running phabfive

The recommended way to run phabfive during development is with `uv run`:

```bash
# Run phabfive commands directly
uv run phabfive --help
uv run phabfive user whoami
uv run phabfive paste list
uv run phabfive maniphest search qa
```

### Running in Docker

If you need to run phabfive in a container (e.g., for testing the Docker image), use the wrapper script:

```bash
# Run against local Phorge instance (auto-configured)
./phorge/phabfive user whoami
./phorge/phabfive paste list

# Or build the image manually
make image
```

The wrapper script automatically uses the local Phorge credentials and sets up host routing.

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

## Local Phorge Instance

For instructions on setting up a local Phorge instance for testing, see [Phorge Setup Guide](phorge-setup.md).

## Building the Docs

For documentation updates:

```bash
# Install with docs dependencies
uv sync --extra docs

# Serve the docs
uv run mkdocs serve
```

Navigate to <http://127.0.0.1:8000> to view the rendered documentation.

The documentation is built using [mkdocs](https://www.mkdocs.org/).

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
