# phabfive

A command line tool to interact with Phabricator/Phorge.

## Features

A summary of the currently supported features:

- **Passphrase** - Get specified secret
- **Diffusion** - List repositories, get branches, clone URIs, add repositories, manage URIs
- **Paste** - List, get, and add pastes
- **User** - Get information about the logged-in user
- **Maniphest** - Add comments, show task details, create tasks from templates

For complete documentation, see [Read the Docs](https://phabfive.readthedocs.io/en/latest/).

## Installation

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

## Quick Start

### 1. Get an API token

Grab a Phabricator/Phorge token at `https://<yourserver.com>/settings/panel/apitokens/`

### 2. Configure credentials

**Environment variables:**
```bash
export PHAB_TOKEN=cli-ABC123
export PHAB_URL=https://yourserver.com/api/
```

**Or use a configuration file:**
```bash
# Linux/XDG
echo "PHAB_TOKEN: cli-ABC123" > ~/.config/phabfive.yaml
echo "PHAB_URL: https://yourserver.com/api/" >> ~/.config/phabfive.yaml

# macOS
echo "PHAB_TOKEN: cli-ABC123" > ~/Library/Application\ Support/phabfive.yaml
echo "PHAB_URL: https://yourserver.com/api/" >> ~/Library/Application\ Support/phabfive.yaml

# Windows - create at: %LOCALAPPDATA%\phabfive\phabfive.yaml
```

**Note:** On macOS, you can use `~/.config` by setting `export XDG_CONFIG_HOME=~/.config`

### 3. Use phabfive

```bash
phabfive passphrase K123
phabfive paste list
phabfive maniphest search myproject
```

## Documentation

- **[Full CLI Reference](https://phabfive.readthedocs.io)** - Complete command documentation
- **[Development Guide](docs/development.md)** - Set up dev environment, run tests, local Phorge/Phabricator setup
- **[Release Process](docs/releasing.md)** - How to build and publish releases

## Contributing

See [docs/development.md](docs/development.md) for instructions on setting up your development environment.

## License

Copyright (c) 2017-2025 Dynamist AB

See the LICENSE file provided with the source distribution for full details.
