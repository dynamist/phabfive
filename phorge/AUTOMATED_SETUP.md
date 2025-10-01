# Phorge Automated Setup

This directory contains scripts for automatically setting up a Phorge instance with a deterministic admin account and API token. This is particularly useful for development containers where persistent storage is not available or desired.

## What Gets Automatically Created

The `init-phorge.sh` script automatically creates:

### Admin Account
- **Admin User Account** - Full administrator privileges
- **Username:** `admin`
- **Email:** `admin@example.com` (verified)
- **API Token:** `api-supersecr3tapikeyfordevelop1`
- **Password Recovery Link** - One-time link to set your password

### Test Users
Eight RMI GUNNAR team members for testing:
- **mikael.wallin** - Mikael Wallin, Team Lead/Scrum Master (mikael.wallin@air.rmi.se)
- **ove.pettersson** - Ove Pettersson, System Architect (ove.pettersson@air.rmi.se)
- **viola.larsson** - Viola Larsson, System Administrator (viola.larsson@air.rmi.se)
- **daniel.lindgren** - Daniel Lindgren, DevOps Engineer (daniel.lindgren@air.rmi.se)
- **sonja.bergstrom** - Sonja Bergström, Windows SharePoint Developer (sonja.bergstrom@air.rmi.se)
- **gabriel.blomqvist** - Gabriel Blomqvist, Windows C# Developer (gabriel.blomqvist@air.rmi.se)
- **sebastian.soderberg** - Sebastian Söderberg, Windows C# Developer (sebastian.soderberg@air.rmi.se)
- **tommy.svensson** - Tommy Svensson, QA Engineer (tommy.svensson@air.rmi.se)

### Default Projects with Workboards
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

Edit the following variables at the top of `init-phorge.sh`:

```bash
ADMIN_USERNAME="admin"              # Admin username (default: admin)
ADMIN_EMAIL="admin@example.com"     # Admin email address
ADMIN_REALNAME="Administrator"      # Full name for the admin user
API_TOKEN="api-supersecr3tapikeyfordevelop1"  # Deterministic API token
```

Note. You don't need to edit this file unless you want to change the default settings.

## How It Works

The script runs automatically during container startup and:
1. Enables username/password authentication
2. Creates admin and test user accounts with verified emails
3. Generates API token for immediate use
4. Creates default projects with workboard columns
5. Generates a one-time password recovery link

All operations are idempotent and safe to run multiple times.

## Idempotency

The script is designed to be idempotent - it checks if each component already exists before creating it. This means:
- Safe to run multiple times
- Container restarts won't duplicate data
- Can recover from partial failures

## Setting Up Your Password

After the automated setup completes, check the container logs for a one-time password setup link:

```bash
docker logs <container-name> | grep "one-time link"
# Output will show something like:
# 🔑 Use this one-time link to set your password:
#    http://phorge.domain.tld/login/once/recover/1/xxxxx/
```

### Steps to Log In

1. **Copy the recovery link** from the container logs
2. **Visit the link** in your browser
3. **Set a password** when prompted
4. **Log in** at `http://phorge.domain.tld/`
   - Username: `admin`
   - Password: (the one you just set)

### Generate a New Recovery Link

If you miss the link or need to reset, run:
```bash
docker exec <container-name> /app/phorge/bin/auth recover admin
```

## Using the API Token

The API token can be used immediately with the Conduit API:

```bash
curl "http://phorge.domain.tld/api/user.whoami" \
  -d "api.token=api-supersecr3tapikeyfordevelop1"
```

Or with the `phabfive` Python cli tool:
```python
uv run phabfive user whoami
```

Note. You need to have configured phabfive with your API token.

## Security Notes

**⚠️ IMPORTANT FOR PRODUCTION USE:**

These scripts are intended for development and testing purposes only. For production use, you should follow best practices for securing your Phorge instance and deploying it in a secure environment.

### Using Environment Variables

You can modify the script to use environment variables:

```bash
ADMIN_USERNAME="${PHORGE_ADMIN_USER:-admin}"
ADMIN_EMAIL="${PHORGE_ADMIN_EMAIL:-admin@example.com}"
ADMIN_REALNAME="${PHORGE_ADMIN_NAME:-Administrator}"
API_TOKEN="${PHORGE_API_TOKEN:-api-default-token}"
```

Then pass them in `compose-phorge.yml`:
```yaml
environment:
  - PHORGE_ADMIN_USER=myadmin
  - PHORGE_ADMIN_EMAIL=admin@mycompany.com
  - PHORGE_ADMIN_NAME=My Admin
  - PHORGE_API_TOKEN=api-your-secure-token-here
```

## Troubleshooting

### View setup logs
```bash
# Using Makefile (recommended)
make phorge-logs

# Or manually with podman
podman logs <container-name>

# Or with docker
docker logs <container-name>
```

### Generate a new password recovery link
```bash
# With podman (recommended)
podman exec <container-name> /app/phorge/bin/auth recover admin

# Or with docker
docker exec <container-name> /app/phorge/bin/auth recover admin
```

## References

- [Phorge Documentation](https://we.phorge.it/book/phorge/)
- [Configuring Accounts and Registration](https://we.phorge.it/book/phorge/article/configuring_accounts_and_registration/)
- [Local Phorge API Documentation](http://phorge.domain.tld/conduit/) - Explore your local Conduit API
