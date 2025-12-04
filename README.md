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

Configure in Linux:
# Install phabfive
uv tool install phabfive

# Or install in a virtual environment
uv venv
uv pip install phabfive
```

Configure in Windows:

Phabfive looks for configuration in the following sequence in Windows environment:

```Commnad prompt
%ALLUSERSPROFILE%\phabfive\phabfive.yaml
%ALLUSERSPROFILE%\phabfive\phabfive.d\*.yaml
%LOCALAPPDATA%\phabfive\phabfive.yaml
%LOCALAPPDATA%\phabfive\phabfive.d\*.yaml
```

Make sure there is a minimum phabfive.yaml store in one of the location
e.g. 
```Commnad prompt
echo "PHAB_TOKEN: cli-ABC123" > %LOCALAPPDATA%\phabfive\phabfive.yaml
```

Additionally, due to connection to Phabricator server on HTTPS requires certificate verification, it is also recommended to install [pip_system_certs](https://pypi.org/project/pip-system-certs/) to ensure system store are linked to python.

```Commnad prompt
pip install pip_system_certs
```

Usage:

```bash
phabfive passphrase K123
phabfive --log-level=DEBUG user whoami
**Install the latest development version:**
```bash
# Install from git to get unreleased features and fixes
uv tool install git+https://github.com/dynamist/phabfive@master
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
phabfive maniphest search "migration tasks" --tag myproject
phabfive maniphest search --tag myproject --updated-after=7
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
