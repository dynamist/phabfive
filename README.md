# phabfive

A command line tool to interact with Phabricator.

The complete documentation for Phabfive can be found at [Read the Docs](https://phabfive.readthedocs.io/en/latest/).

## Features

A summary of the currently supported features:

- Passphrase
  - Get specified secret

- Diffusion
  - List repositories
  - Get branches for specified repository
  - Get clone URI for specified repository
  - Add repository
  - Edit URI
  - Create URI (observe repository)

- Paste
  - List pastes
  - Get specified paste
  - Add paste

- User
  - Who am I: information about the logged-in user

- Maniphest
  - Add comment to task
  - Show task summary or full details
  - Create multiple tasks via template config file

## Installation

### Using uv (recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer (10-100x faster than pip):

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install phabfive
uv tool install phabfive

# Or install in a virtual environment
uv venv
uv pip install phabfive
```

### Using pip

```bash
pip install phabfive
```

## Example usage

Grab a Phabricator/Phorge token at `https://<yourserver.com>/settings/panel/apitokens/`

Configure via environment variables:

```bash
export PHAB_TOKEN=cli-ABC123
export PHAB_URL=https://yourserver.com/api/
```

Or via configuration file:

```bash
# Linux/XDG
echo "PHAB_TOKEN: cli-ABC123" > ~/.config/phabfive.yaml
echo "PHAB_URL: https://yourserver.com/api/" >> ~/.config/phabfive.yaml

# macOS
cat > ~/Library/Application\ Support/phabfive.yaml << EOF
PHAB_TOKEN: cli-ABC123
PHAB_URL: https://yourserver.com/api/
EOF

# Windows
# Create file at: %LOCALAPPDATA%\phabfive\phabfive.yaml
```

**Note:** On macOS, you can use `~/.config` by setting `export XDG_CONFIG_HOME=~/.config`

Usage:

```bash
phabfive passphrase K123
```

## Using Docker/Podman

You can run phabfive in a container without installing Python or dependencies on your host machine.

### Build the image

```bash
make phabfive-build
```

This builds an optimized Alpine-based image using uv with cache mounts for fast rebuilds.

### Run phabfive

```bash
make phabfive-run ARGS="paste list"
make phabfive-run ARGS="maniphest search qa"
make phabfive-run ARGS="passphrase K123"
```

**Configuration:** The Makefile automatically handles your credentials:

- **Environment variables:** `PHAB_TOKEN` and `PHAB_URL` are passed through if set
- **Config file:** Automatically detected and mounted from OS-specific locations:
  - macOS: `~/Library/Application Support/phabfive.yaml`
  - Linux: `~/.config/phabfive.yaml`

You can use either method or both - the container will use whichever is available.

### Development with local Phorge instance

To test against a local Phorge instance (see "Run local development phorge instance" below):

```bash
# Build the image (same as production)
make phabfive-build

# Run against local Phorge
make phabfive-run-dev ARGS="paste list"
```

The dev run target adds `--add-host` flags to connect to `phorge.domain.tld` running on your host machine.

## Run local development phabricator instance

First add the following to your `/etc/hosts` file:

```text
127.0.0.1       phabricator.domain.tld
```

Next start the mysql instance separate (in the first terminal) from the phabricator instance with

```bash
docker compose -f docker-compose-phabricator.yml up mysql
```

Then after the database is up and running you can start the webserver with (in a second terminal)

```bash
docker compose -f docker-compose-phabricator.yml up phabricator
```

This startup will take some time to setup the demo instance. Once completed you can access your instance in your browser at `http://phabricator.domain.tld/`. On first use you need to setup your admin account. Most development for phabfive requires an API token, create one here `http://phabricator.domain.tld/settings/user/<username>/page/apitokens/`.

Note there is no persistence disks so if the container is shutdown any data will be lost and you have to restart.


## Run local development phorge instance

First add the following to your `/etc/hosts` file:

```text
127.0.0.1       phorge.domain.tld
127.0.0.1       phorge-files.domain.tld
```

Start the services (mariadb and phorge):

```bash
# This uses podman or docker automatically (Prefers podman)
make phorge-up
```

The Makefile automatically detects whether you have podman or docker installed (preferring podman). It will start mariadb in the background and phorge in the foreground, so you can see the logs and the password recovery link.

To stop: Press `Ctrl+C`, then run `make phorge-down`

This startup will take some time to setup the demo instance. Once completed you can access your instance in your browser at `http://phorge.domain.tld/`.

### Automated Admin Setup

The Phorge instance includes **automated setup** perfect for development containers. On first startup, the following are automatically created:

**Admin Account:**
- **Username:** `admin`
- **Email:** `admin@example.com`
- **API Token:** `api-supersecr3tapikeyfordevelop1`
- **Password Setup Link:** Generated in container logs

**Test Users (RMI GUNNAR Team):**
- `mikael.wallin` (Team Lead/Scrum Master)
- `ove.pettersson` (System Architect)
- `viola.larsson` (System Administrator)
- `daniel.lindgren` (DevOps Engineer)
- `sonja.bergstrom` (Windows SharePoint Developer)
- `gabriel.blomqvist` (Windows C# Developer)
- `sebastian.soderberg` (Windows C# Developer)
- `tommy.svensson` (QA Engineer)

**Default Projects with Workboards:**
- `GUNNAR-Core`, `Architecture`, `Infrastructure`, `Development`
- `QA`, `SharePoint`, `Security`

Each project has 5 columns: Backlog → Up Next → In Progress → In Review → Done

The password recovery link will be displayed in the logs when phorge starts. If you miss it, you can view logs with:

```bash
# Makefile (recommended)
make phorge-logs
```

Or manually with:
```bash
# Podman
podman logs <container-name> | grep "one-time link"
# Docker
docker logs <container-name> | grep "one-time link"
```

Visit the link to set your password, then log in at `http://phorge.domain.tld/`.

You can immediately use the API token for development:

```bash
export PHAB_TOKEN=api-supersecr3tapikeyfordevelop1
export PHAB_URL=http://phorge.domain.tld/api/
```
Or you can configure it in your configuration file mentioned in [Example usage](#example-usage)

For customizing the setup, see [phorge/AUTOMATED_SETUP.md](phorge/AUTOMATED_SETUP.md).

### Data Persistence

Note: By default the instance won't persist any data so if the container is shutdown any data will be lost and you have to restart.

## Development

### Setup with uv (recommended)

```bash
# Clone the repository
git clone https://github.com/dynamist/phabfive.git
cd phabfive

# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create a virtual environment and install dependencies
uv sync --group dev

# Run tests
uv run pytest

# Run linting
uv run flake8 phabfive/ tests/

# Run the CLI during development
uv run phabfive --help
```

### Testing across Python versions with tox

For comprehensive testing across all supported Python versions locally (requires Python 3.10-3.13 installed):

```bash
# Install with dev dependencies (includes tox and tox-uv)
uv sync --group dev

# Run tests across all Python versions (uses uv via tox-uv)
uv run tox

# Run specific environment
uv run tox -e py313

# Run linting
uv run tox -e flake8

# Run coverage report
uv run tox -e coverage
```

With `tox-uv`, tox automatically uses uv for dependency resolution, giving you fast, isolated testing environments.

## Building a release

For version schema we follow basic [SemVer](https://semver.org/) versioning schema system with the extensions that is defined by python in this [PEP 440](https://peps.python.org/pep-0440/). It allows for some post and dev releases if we need to, but in general we should only publish stable regular semver releases.

Instructions how to build a release and upload it to PyPi can be found in the official [packaging documentation at Python.org](https://packaging.python.org/en/latest/tutorials/packaging-projects/).

In order to be allowed to upload a new release for phabfive, you need to be a *owner* or *maintainer* for this project inside PyPi. Currently only Johan and Henrik are owners. Ask them for assistance if you need permissions to upload a new release.

## LICENSE

Copyright (c) 2017-2025 Dynamist AB

See the LICENSE file provided with the source distribution for full details.
