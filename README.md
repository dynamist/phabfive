# phabfive

CLI for [Phabricator](https://www.phacility.com/phabricator/) and [Phorge](https://we.phorge.it/) - built for humans and AI agents.

![phabfive maniphest show](https://raw.githubusercontent.com/dynamist/phabfive/main/docs/maniphest-show.png)

## Features

- **Maniphest** - Full task management: create, show, edit, search, comment, parents/subtasks
- **Paste** - Create, show, edit, search, and comment on pastes
- **Diffusion** - Repository management, branches, and URI configuration
- **Passphrase** - Search, list, and retrieve secrets (passwords, tokens, SSH keys, notes)
- **User** - User info and interactive setup wizard

Cross-cutting features:

- **Monogram shortcuts** - `phabfive T123` expands to `phabfive maniphest show T123`
- **Batch editing** - Edit multiple objects at once: `phabfive edit T1,T2,T3 --status=resolved`
- **Shell completion** - Tab completion for commands, options, and values
- **Machine-readable output** - `--format=json` or `--format=yaml` for scripting and AI agents
- **Agent skill** - `phabfive --skill` prints a ready-to-use skill file for AI agents
- **Quiet by default** - `-v` reports which filters a search actually applied

For complete documentation, see [Read the Docs](https://phabfive.readthedocs.io/).

## Installation

[uv](https://docs.astral.sh/uv/) is a fast Python package installer (10-100x faster than pip):

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install from pypi.org
uv tool install phabfive

# Install from Github to get unreleased features and fixes
uv tool install git+https://github.com/dynamist/phabfive
```

If you prefer [mise-en-place](https://mise.jdx.dev/) the polyglot tool version manager:

```bash
# Install mise if you haven't already
curl https://mise.run | sh

# Install from pypi.org, needs a Python toolchain
mise use --global --pin pipx:phabfive

# Install the standalone executable instead, needs nothing
mise use --global github:dynamist/phabfive
```

The two differ in what they fetch. `pipx:` resolves the wheel from pypi.org and so needs Python
present, the same as uv above. `github:` downloads the release executable described below, which
bundles its own Python, so it is the one to reach for on a machine without a toolchain. Either way
mise handles pinning and upgrades.

### Standalone executable

Every release ships a single file executable that bundles its own Python, so it needs nothing
installed. Download it, make it executable and put it on your `PATH`:

```bash
# Linux amd64 - swap the asset name for your platform
curl -LO https://github.com/dynamist/phabfive/releases/latest/download/phabfive-linux-amd64
chmod +x phabfive-linux-amd64
sudo install phabfive-linux-amd64 /usr/local/bin/phabfive
```

The asset names are `phabfive-linux-amd64`, `phabfive-linux-arm64`, `phabfive-macos-amd64`,
`phabfive-macos-arm64`, `phabfive-windows-amd64.exe` and `phabfive-windows-arm64.exe`.
`latest/download/` always resolves to the newest release; pin a version by using
`download/v0.10.0/` instead. All builds are listed on the
[releases page](https://github.com/dynamist/phabfive/releases).

The executable unpacks itself on every run, so it starts in about two seconds against a fraction of
that for an installed phabfive. Where a Python toolchain is available, prefer an installed phabfive
via uv or `mise use pipx:phabfive`, and keep the executable for machines without one, such as a
bare CI image or a locked down workstation. `mise use github:dynamist/phabfive` installs this same
executable, and is the easier route to it if you already run mise.

**macOS:** the binaries are not notarized, so Gatekeeper refuses them after a browser download.
Clear the quarantine flag with `xattr -d com.apple.quarantine phabfive-macos-arm64`.

<details>
<summary>Verifying signatures</summary>

Every executable except `phabfive-windows-arm64.exe` is signed with
[Sigstore](https://www.sigstore.dev/) and ships a `.sigstore.json` bundle beside it. Download both
files and verify with [cosign](https://docs.sigstore.dev/):

```bash
cosign verify-blob phabfive-linux-amd64 \
  --bundle phabfive-linux-amd64.sigstore.json \
  --certificate-identity-regexp="https://github.com/dynamist/phabfive" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
```

</details>

### Container image

`ghcr.io/dynamist/phabfive` is a scratch image containing only phabfive and its own Python. It can't run by itself. Instead, copy phabfive from it into your own image, for example in CI:

```dockerfile
FROM debian:trixie-slim
COPY --from=ghcr.io/dynamist/phabfive:latest /opt/phabfive /opt/phabfive
ENV PATH=/opt/phabfive/bin:$PATH
```

Use the `-musl` tags (e.g. `latest-musl`) for Alpine based images. The files must stay at `/opt/phabfive`, because the install contains absolute paths.

## Quick Start

Run the interactive setup wizard:

```bash
phabfive user setup
```

The wizard will prompt for your Phabricator/Phorge URL and API token, then store them in `~/.arcrc` (Arcanist-compatible format). If you have multiple servers configured, phabfive will let you choose which one to use.

Then start using phabfive:

```bash
# Show tasks and pastes with monogram shortcuts
phabfive T123
phabfive P456

# Search and filter
phabfive maniphest search "migration tasks" --tag myproject
phabfive maniphest search --tag myproject --order updated --limit 10
phabfive maniphest search --author=@me          # tasks you created
phabfive maniphest search --assigned=@me        # tasks assigned to you
phabfive paste search "config"

# Create and edit
phabfive maniphest create "Fix the bug" --priority=high --tag myproject
phabfive maniphest edit T123 "New Title" --status=resolved

# Spaces - place a new task in one, or move a task between them
phabfive maniphest create "Quarterly cleanup" --space=Archive
phabfive maniphest edit T123 --space=S3

# Smart navigation - raise/lower priority, move columns forward/backward
phabfive edit T123 --priority=raise
phabfive edit T123 --tag=MyBoard --column=forward

# Batch operations
phabfive edit T1,T2,T3 --status=resolved
phabfive maniphest search --assigned=@me | phabfive edit --column=Done

# Fewer results than expected? -v shows which filters were applied
phabfive -v maniphest search --tag myproject
```

## Shell Completion

Enable tab completion for bash, zsh, or fish:

```bash
phabfive --install-completion bash
phabfive --install-completion zsh
phabfive --install-completion fish
```

After installation, restart your shell or source your profile.

Completions that come from the server, such as usernames, are cached on disk so
only the first TAB waits for a round trip. Run `phabfive cache clear` after
somebody joins, leaves or is renamed, or set `PHAB_CACHE=0` to switch caching
off. See [Caching](docs/caching.md) for what is cached and for how long;
secrets never are.

<details>
<summary>Manual configuration (advanced)</summary>

**Arcanist-compatible `~/.arcrc`** (recommended):

```json
{
  "hosts": {
    "https://yourserver.com/api/": {
      "token": "cli-ABC123"
    }
  }
}
```

**Or environment variables:**

```bash
export PHAB_TOKEN=cli-ABC123
export PHAB_URL=https://yourserver.com/api/
```

**Windows SSL certificates:** If you encounter certificate errors, install [pip-system-certs](https://pypi.org/project/pip-system-certs/) to use the Windows certificate store: `pip install pip-system-certs`

</details>

## Documentation

- **[Full CLI Reference](https://phabfive.readthedocs.io)** - Complete command documentation
- **[Development Guide](docs/development.md)** - Set up dev environment, run tests, local Phorge/Phabricator setup
- **[Release Process](docs/releasing.md)** - How to build and publish releases

## Contributing

See [docs/development.md](docs/development.md) for instructions on setting up your development environment.

## License

Copyright (c) 2017-2026 Dynamist AB

See the LICENSE file provided with the source distribution for full details.
