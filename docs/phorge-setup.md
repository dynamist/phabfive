# Local Phorge/Phabricator Setup

This guide covers setting up a local Phorge or Phabricator instance for testing phabfive without affecting production systems.

## Set Up a Local Phorge Instance

The project includes a local Phorge development environment with automated setup, perfect for testing phabfive without affecting production systems.

### Initial Setup

**1. Configure your hosts file:**

Add the following to your `/etc/hosts` file:
```
127.0.0.1       phorge.domain.tld
127.0.0.1       phorge-files.domain.tld
```

**2. Start Phorge:**

```bash
make phorge-up
```

The Makefile automatically detects whether you have podman or docker installed (preferring podman). It will start mariadb in the background and phorge in the foreground, so you can see the logs and the admin password recovery link.

**To stop:** Press `Ctrl+C`, then run `make phorge-down`

### Automated Setup

The Phorge instance includes **automated setup** with everything you need for development:

**Admin Account:**

- **Username:** `admin`

- **Email:** `admin@example.com`

- **API Token:** `api-supersecr3tapikeyfordevelop1`

- **Password Setup Link:** Generated in container logs (see below)

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

- `GUNNAR-Core`

- `Architecture`

- `Infrastructure`

- `Development`

- `QA`

- `SharePoint`

- `Security`

Each project has 5 columns: Backlog → Up Next → In Progress → In Review → Done

### Getting Your Admin Password

The password recovery link will be displayed in the logs when phorge starts. If you miss it, view logs with:

```bash
# Using Makefile (recommended)
make phorge-logs

# Or manually
podman logs <container-name> | grep "one-time link"
docker logs <container-name> | grep "one-time link"
```

Visit the link to set your password, then log in at `http://phorge.domain.tld/`.

### Configure phabfive for Local Development

You can immediately use the pre-configured API token:

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

Now you can test phabfive against your local instance:
```bash
uv run phabfive user whoami
uv run phabfive paste list
```

### Useful Phorge Commands

```bash
make phorge-up      # Start Phorge (mariadb + phorge)
make phorge-down    # Stop containers
make phorge-logs    # View container logs
make phorge-shell   # Open shell in phorge container
```

### Data Persistence

**Note:** By default the instance won't persist data. If the mariadb container is shutdown, any data will be lost and you'll need to restart fresh.

### Customization

For advanced setup customization (changing admin credentials, test users, projects, etc.), see `phorge/AUTOMATED_SETUP.md` in the repository root.

## Set Up a Local Phabricator Instance (Legacy)

If you need to test against Phabricator instead of Phorge:

**1. Configure hosts file:**
```
127.0.0.1       phabricator.domain.tld
```

**2. Start services:**
```bash
# Start mysql (in first terminal)
docker compose -f docker-compose-phabricator.yml up mysql

# Start phabricator (in second terminal, after mysql is ready)
docker compose -f docker-compose-phabricator.yml up phabricator
```

**3. Access and configure:**
- Access at `http://phabricator.domain.tld/`
- Create admin account on first use
- Generate API token at `http://phabricator.domain.tld/settings/user/<username>/page/apitokens/`

**Note:** No data persistence - data will be lost when container stops.
