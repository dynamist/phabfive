# Local Phorge Setup

This guide covers setting up a local Phorge instance for **developing** and **testing** phabfive.

## Quick Start

**1. Configure your hosts file:**

Add the following to `/etc/hosts`:

```text
127.0.0.1       phorge.domain.tld
127.0.0.1       cdn.domain.tld
```

**2. Start Phorge:**

```bash
make phorge-up
```

The Makefile automatically detects whether you have podman or docker installed (preferring podman). It starts MariaDB in the background and Phorge in the foreground, so you can see the logs and the admin password recovery link.

**3. Stop Phorge::** When you are done, press `Ctrl+C`, then run `make phorge-down` to remove the containers.

## What Gets Created

The `init-phorge.sh` script automatically creates:

### Admin Account

- **Username:** `admin`
- **Password:** `supersecr3tpassw0rdfordevelop1` (if `PHORGE_ADMIN_PASS` is set)
- **Email:** `admin@domain.tld` (verified)
- **API Token:** `api-supersecr3tapikeyfordevelop1`

If `PHORGE_ADMIN_PASS` is not set, a one-time password recovery link is displayed in the logs instead.

### Test Users

Eight RMI GUNNAR team members for testing (all share the same password as admin if `PHORGE_ADMIN_PASS` is set):

- **daniel.lindgren** - Daniel Lindgren (daniel.lindgren@air.rmi.se)
- **gabriel.blomqvist** - Gabriel Blomqvist (gabriel.blomqvist@air.rmi.se)
- **mikael.wallin** - Mikael Wallin (mikael.wallin@air.rmi.se)
- **ove.pettersson** - Ove Pettersson (ove.pettersson@air.rmi.se)
- **sebastian.soderberg** - Sebastian Söderberg (sebastian.soderberg@air.rmi.se)
- **sonja.bergstrom** - Sonja Bergström (sonja.bergstrom@air.rmi.se)
- **tommy.svensson** - Tommy Svensson (tommy.svensson@air.rmi.se)
- **viola.larsson** - Viola Larsson (viola.larsson@air.rmi.se)

### Default Projects

Seven projects with 5-column workboards (Backlog → Up Next → In Progress → In Review → Done):

- **GUNNAR-Core** - Main chip blueprint development and secure design
- **Architecture** - System architecture and design specifications
- **Infrastructure** - Servers, virtualization, and network management
- **Development** - Development tools and environment setup
- **QA** - Testing, quality assurance, and compliance validation
- **SharePoint** - Windows SharePoint integration and document management
- **Security** - Security compliance, hardening, and vulnerability assessment

The admin user is automatically joined to all projects.

## Configuration

All settings can be customized via environment variables. Defaults are in `compose.yml` and can be overridden from the command line:

```bash
PHORGE_GIT_REF=master make phorge-up
PHORGE_ADMIN_PASS=mypassword PHORGE_GIT_REF=master make phorge-up
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PHORGE_URL` | `http://phorge.domain.tld` | Base URL for Phorge |
| `PHORGE_CDN_URL` | `http://cdn.domain.tld` | CDN URL for serving files |
| `PHORGE_TITLE` | `RMI` | Instance title shown in UI |
| `PHORGE_ADMIN_USER` | `admin` | Admin username |
| `PHORGE_ADMIN_EMAIL` | `admin@domain.tld` | Admin email address |
| `PHORGE_ADMIN_NAME` | `Administrator` | Admin display name |
| `PHORGE_ADMIN_PASS` | `supersecr3tpassw0rdfordevelop1` | Admin password (enables immediate login) |
| `PHORGE_ADMIN_TOKEN` | `api-supersecr3tapikeyfordevelop1` | Pre-configured API token |
| `PHORGE_GIT_REF` | `stable` | Git branch/tag/commit for Phorge |
| `ARCANIST_GIT_REF` | `stable` | Git branch/tag/commit for Arcanist |

## Configure phabfive

Use the pre-configured API token:

**Environment variables:**

```bash
export PHAB_TOKEN=api-supersecr3tapikeyfordevelop1
export PHAB_URL=http://phorge.domain.tld/api/
```

**Or configuration file:**

```bash
# Linux
echo "PHAB_TOKEN: api-supersecr3tapikeyfordevelop1" > ~/.config/phabfive.yaml
echo "PHAB_URL: http://phorge.domain.tld/api/" >> ~/.config/phabfive.yaml

# macOS
echo "PHAB_TOKEN: api-supersecr3tapikeyfordevelop1" > ~/Library/Application\ Support/phabfive.yaml
echo "PHAB_URL: http://phorge.domain.tld/api/" >> ~/Library/Application\ Support/phabfive.yaml
```

Test it:

```bash
uv run phabfive user whoami
uv run phabfive paste list
```

## Create Test Tasks

After configuring phabfive, you can populate Phorge with ~70 realistic test tasks:

```bash
uv run phabfive maniphest create test-files/mega-2024-simulation.yml
```

This creates a full year simulation of project work for the RMI GUNNAR team, including EPICs with subtasks, varied priorities, and assignments across the default projects.

Use `--dry-run` to preview without creating:

```bash
uv run phabfive maniphest create test-files/mega-2024-simulation.yml --dry-run
```

## Using the API Token

The API token works immediately without logging in:

```bash
curl "http://phorge.domain.tld/api/user.whoami" \
  -d "api.token=api-supersecr3tapikeyfordevelop1"
```

## How It Works

The script runs automatically during container startup and:

1. Enables username/password authentication
2. Creates admin and test user accounts with verified emails
3. Generates API token for immediate use
4. Creates default projects with workboard columns
5. Sets passwords for all users (if `PHORGE_ADMIN_PASS` is set) or generates a recovery link

All operations are idempotent - safe to run multiple times. Container restarts won't duplicate data.

## Useful Commands

```bash
make phorge-up      # Start Phorge (mariadb + phorge)
make phorge-down    # Stop containers
make phorge-logs    # View container logs
make phorge-shell   # Open shell in phorge container
```

## Troubleshooting

### Get admin password recovery link

The link is displayed in logs when Phorge starts. If you miss it:

```bash
make phorge-logs | grep "one-time link"
```

### Generate a new recovery link

```bash
# With podman
podman exec <container-name> /app/phorge/bin/auth recover admin

# With docker
docker exec <container-name> /app/phorge/bin/auth recover admin
```

### Data Persistence

By default the instance won't persist data. If the MariaDB container is shut down, data is lost and you'll need to restart fresh.

## Security Notes

**⚠️ IMPORTANT:**

These scripts are intended for development and testing only. For production, follow best practices for securing your Phorge instance.

## References

- [Phorge Documentation](https://we.phorge.it/book/phorge/)
- [Configuring Accounts and Registration](https://we.phorge.it/book/phorge/article/configuring_accounts_and_registration/)
- [Local Phorge API Documentation](http://phorge.domain.tld/conduit/)
