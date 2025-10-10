## Installation in a development environment

This project uses [uv](https://github.com/astral-sh/uv) for dependency management:

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repository
git clone https://github.com/dynamist/phabfive.git
cd phabfive

# Create virtual environment and install dependencies
uv sync --group dev

# Run the CLI
uv run phabfive --help
```



## Build and run in a Docker/Podman image

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

The Makefile automatically handles configuration by passing through environment variables and mounting your config file from OS-specific locations.



## Run unittests

This repo uses `pytest` as the test runner and `tox` to orchestrate tests for various Python versions (3.10-3.13).

To run the tests locally on your machine for all supported and installed versions of Python:
```bash
make test
```

Or run tox directly:
```bash
# Run all Python versions (requires Python 3.10-3.13 installed)
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



## Set up a local Phorge instance for development

The project includes a local Phorge development environment with automated setup.

First add the following to your `/etc/hosts` file:
```
127.0.0.1       phorge.domain.tld
127.0.0.1       phorge-files.domain.tld
```

Start the Phorge instance (uses podman or docker automatically):
```bash
make phorge-up
```

The Phorge instance includes **automated setup** with:

**Admin Account:**
- **Username:** `admin`
- **Email:** `admin@example.com`
- **API Token:** `api-supersecr3tapikeyfordevelop1`
- **Password Setup Link:** Generated in container logs

Configure phabfive to use the local instance:
```bash
export PHAB_TOKEN=api-supersecr3tapikeyfordevelop1
export PHAB_URL=http://phorge.domain.tld/api/
```

Or add to your config file (`~/.config/phabfive.yaml` on Linux, `~/Library/Application Support/phabfive.yaml` on macOS):
```yaml
PHAB_TOKEN: api-supersecr3tapikeyfordevelop1
PHAB_URL: http://phorge.domain.tld/api/
```

You should now be able to run phabfive against your local Phorge instance!

Useful commands:
```bash
make phorge-logs    # View logs
make phorge-down    # Stop containers
make phorge-reset   # Rebuild and restart
make phorge-shell   # Open shell in container
```



## Run mkdocs locally for documentation updates

Overall documentation for mkdocs can be found at [Mkdocs homepage](https://www.mkdocs.org/#installation)

The docs dependencies are in the `[project.optional-dependencies]` section. Install and run:

```bash
# Install with docs dependencies
uv sync --extra docs

# Serve the docs
uv run mkdocs serve
```

Output:
```
INFO    -  Building documentation...
INFO    -  Cleaning site directory
[I 160402 15:50:43 server:271] Serving on http://127.0.0.1:8000
[I 160402 15:50:43 handlers:58] Start watching changes
[I 160402 15:50:43 handlers:60] Start detecting changes
```

Navigate to `http://127.0.0.1:8000` to view the rendered docs.
