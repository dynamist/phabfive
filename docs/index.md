# Phabfive Documentation

Phabfive is a command line tool to interact with Phabricator/Phorge, providing a fast and efficient way to work with your Phabricator instance from the terminal.

## Features

Phabfive currently supports the following Phabricator/Phorge applications:

- **Passphrase** - Search, list, and retrieve secrets (passwords, tokens, SSH keys, notes)
- **Diffusion** - List and show repositories, their branches and tags, clone URIs, add repositories, manage URIs and policies
- **Paste** - List, get, and add code pastes
- **Project** - Show, search, create and edit projects, their members, subprojects, milestones and policies
- **User** - Get information about the logged-in user
- **Maniphest** - Add comments, show task details, create tasks, and search with advanced project filtering and transition filtering
- **Edit** - Unified editing interface with auto-detection, batch operations, and smart column/priority navigation
- **Specs** - Create or search whatever a single file describes, across applications, with `phabfive apply -f` and `phabfive search -f`

## Getting Started

### Quick Installation

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install phabfive
uv tool install phabfive
```

With [mise](https://mise.jdx.dev/) instead:

```bash
# From pypi.org, needs a Python toolchain
mise use --global --pin pipx:phabfive

# Or the standalone executable, which bundles its own Python
mise use --global github:dynamist/phabfive
```

See the [README](https://github.com/dynamist/phabfive#installation) for the standalone executables
and the container image.

### Basic Usage

```bash
# Get a secret from Passphrase (monogram shortcut)
phabfive K123

# Search and filter credentials
phabfive passphrase search "deploy"
phabfive passphrase search --type=password

# Show multiple secrets at once
phabfive passphrase show K1 K2 K3

# Pull one secret out of a structured record
phabfive --format=json passphrase show K4 | jq -r '.Credential.Secret'

# Search pastes
phabfive paste search "deploy"

# Search Maniphest tasks
phabfive maniphest search myproject

# Show and search projects
phabfive project show '#development'
phabfive project search --member=@me

# Run a whole file: create everything it describes, or run every search in it
phabfive apply -f specs/create/platform-bootstrap.yaml --dry-run
phabfive search -f specs/search/release-readiness.yaml
```

For detailed setup instructions, see the [README](https://github.com/dynamist/phabfive/blob/main/README.md).

## Documentation Sections

### CLI Reference

- **[Maniphest CLI](maniphest-cli.md)** - Complete guide to task management, including advanced transition filtering
- **[Project CLI](project-cli.md)** - Show, search, create and edit projects, subprojects and milestones
- **[Edit CLI](edit-cli.md)** - Unified editing with auto-detection, batch operations, and smart navigation
- **[Diffusion URIs](diffusion-uri.md)** - How a repository's URIs work: origin, I/O, display and disabled
- **[Policies](policies.md)** - Who can see, edit, push to or comment on repositories and tasks

### Specs

A spec is one file that says what should exist, or what to look for, across applications.

- **[The Phorge spec format](phorge-spec.md)** - The normative definition: the envelope, every key, the reference grammar, the variables, the serializations and the two validation layers
- **[Creating with Specs](create-specs.md)** - Writing a create spec, previewing it with `--dry-run`, and fixing what it reports
- **[Searching with Specs](search-specs.md)** - Writing a search spec, running several searches and several applications from one file

### Behaviour

- **[Caching](caching.md)** - What shell completion caches, where, for how long, and how to clear it
- **[Retries](retries.md)** - Which Conduit calls are retried, how long they wait, and what is never retried

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
- **[License](https://github.com/dynamist/phabfive/blob/main/LICENSE)** - Open source license information

## Contributing

We welcome contributions! See the [Development Guide](development.md) for instructions on:

- Setting up your development environment
- Running tests
- Code style and linting guidelines
- Working with a local Phorge instance

## Support

For bug reports and feature requests, please open an issue on [GitHub](https://github.com/dynamist/phabfive/issues).
