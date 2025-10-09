# Phabfive Documentation

Phabfive is a command line tool to interact with Phabricator/Phorge, providing a fast and efficient way to work with your Phabricator instance from the terminal.

## Features

Phabfive currently supports the following Phabricator/Phorge applications:

- **Passphrase** - Get specified secrets for credential management
- **Diffusion** - List repositories, get branches, clone URIs, add repositories, manage URIs
- **Paste** - List, get, and add code pastes
- **User** - Get information about the logged-in user
- **Maniphest** - Add comments, show task details, create tasks from templates

## Getting Started

### Quick Installation

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install phabfive
uv tool install phabfive
```

### Basic Usage

```bash
# Get a secret from Passphrase
phabfive passphrase K123

# List pastes
phabfive paste list

# Search Maniphest tasks
phabfive maniphest search myproject
```

For detailed setup instructions, see the [README](https://github.com/dynamist/phabfive/blob/master/README.md).

## Documentation Sections

### Development

- **[Development Guide](development.md)** - Set up your development environment, run tests, and contribute to phabfive
- **[Phorge Setup](phorge-setup.md)** - Instructions for setting up a local Phorge/Phabricator instance for testing

### Releasing

- **[Release Process](releasing.md)** - How to build and publish new releases to PyPI

## Command Reference

For the complete CLI command reference and API documentation, explore the sections in the navigation menu.

## Project Links

- **[GitHub Repository](https://github.com/dynamist/phabfive)** - Source code, issues, and pull requests
- **[PyPI Package](https://pypi.org/project/phabfive/)** - Official Python package
- **[License](https://github.com/dynamist/phabfive/blob/master/LICENSE)** - Open source license information

## Contributing

We welcome contributions! See the [Development Guide](development.md) for instructions on:

- Setting up your development environment
- Running tests
- Code style and linting guidelines
- Working with a local Phorge instance

## Support

For bug reports and feature requests, please open an issue on [GitHub](https://github.com/dynamist/phabfive/issues).
