# Phorge Quick Start Guide

Quick reference for working with the local Phorge development instance.

## TL;DR - Get Started in 30 Seconds

```bash
make phorge-setup
```

This single command:
- ✅ Starts MariaDB and Phorge containers
- ✅ Creates 3 Spaces (Public, Internal, Restricted)
- ✅ Creates 7 Projects with workboards
- ✅ Populates ~70 demo tasks with Space assignments
- ✅ Distributes tasks across workboard columns (Backlog → Done)
- ✅ Gives you a realistic development environment

**Access:** http://phorge.domain.tld  
**Login:** Use password recovery link from logs (`make phorge-logs`)

---

## Available Commands

### Main Commands

| Command | Description |
|---------|-------------|
| `make phorge-setup` | **All-in-one:** Start Phorge + populate demo data |
| `make phorge-up` | Start Phorge only (clean slate) |
| `make phorge-down` | Stop and remove containers |
| `make phorge-logs` | View container logs |
| `make phorge-shell` | Open bash shell in Phorge container |
| `make phorge-populate` | Just populate demo data (if already running) |

### Typical Workflows

**Full Setup (Recommended for Testing):**
```bash
make phorge-setup              # Start + populate demo data
# Visit http://phorge.domain.tld
```

**Clean Environment:**
```bash
make phorge-up                 # Start without demo data
# Add your own tasks manually
```

**Restart with Fresh Data:**
```bash
make phorge-down               # Stop
make phorge-setup              # Start fresh with new demo data
```

**Repopulate Data:**
```bash
# If Phorge is already running but you want to add demo tasks
make phorge-populate
```

---

## What Gets Created

### Spaces (3)
- **Public** (default) - Anyone can view, users can edit
- **Internal** - Users only, for team work
- **Restricted** - Users view, admin edit (for security tasks)

### Projects (7)
- GUNNAR-Core
- Architecture
- Infrastructure
- Development
- QA
- SharePoint
- Security

Each project has a workboard with 5 columns:
1. Backlog (default)
2. Up Next
3. In Progress
4. In Review
5. Done

### Tasks (~70)
From `test-files/mega-2024-simulation.yml`:
- **Q1 2024:** Infrastructure & Planning tasks
- **Q2 2024:** Core Development tasks
- **Q3 2024:** Testing & Validation tasks
- **Q4 2024:** Certification & Documentation tasks
- **Ongoing:** Year-round tasks

Tasks are automatically distributed:
- **Backlog:** wish and low priority
- **Up Next:** normal priority
- **In Progress:** high priority
- **In Review:** unbreak priority
- **Done:** Q1 tasks (simulates completed work)

Space assignments:
- Security/compliance → **Restricted**
- Development/infrastructure → **Internal**
- Documentation → **Public**

### Users (9)
- **admin** (administrator)
- mikael.wallin (Team Lead)
- ove.pettersson (Architect)
- viola.larsson (Sysadmin)
- daniel.lindgren (DevOps)
- sonja.bergstrom (SharePoint Dev)
- gabriel.blomqvist (C# Dev)
- sebastian.soderberg (C# Dev)
- tommy.svensson (QA)

---

## Configuration

### Login Credentials

Get password recovery link:
```bash
make phorge-logs | grep "one-time link"
# Or generate new one:
podman exec phorge-phorge-1 /app/phorge/bin/auth recover admin
```

### API Token

Pre-configured token: `api-supersecr3tapikeyfordevelop1`

Configure phabfive:
```bash
# Linux/macOS
mkdir -p ~/.config/phabfive
cat > ~/.config/phabfive/phabfive.yaml << 'CONFIG'
host: http://phorge.domain.tld
token: api-supersecr3tapikeyfordevelop1
CONFIG
```

Test it:
```bash
uv run phabfive user whoami
```

---

## Troubleshooting

### Phorge not accessible

Check if containers are running:
```bash
podman ps
```

View logs:
```bash
make phorge-logs
```

### Demo data not created

Ensure phabfive is configured:
```bash
cat ~/.config/phabfive/phabfive.yaml
```

Manually populate:
```bash
make phorge-populate
```

### Port already in use

Check what's using port 80:
```bash
sudo lsof -i :80
# or
sudo netstat -tulpn | grep :80
```

Stop conflicting service or modify `compose-phorge.yml` ports.

### Database issues

Reset everything:
```bash
make phorge-down
podman volume prune  # Remove old database data
make phorge-setup
```

---

## Advanced Usage

### Manual Task Creation

Without distribution:
```bash
uv run phabfive maniphest create test-files/mega-2024-simulation.yml
```

### Manual Task Distribution

After creating tasks:
```bash
uv run python phorge/lib/move-tasks-to-columns.py
```

### Customize Demo Data

Edit the YAML file:
```bash
vim test-files/mega-2024-simulation.yml
```

Then recreate:
```bash
make phorge-down
make phorge-setup
```

### Access Database Directly

```bash
podman exec -it phorge-mariadb-1 mysql -uroot -psomerootpassword
```

---

## Documentation

- **[Automated Setup Documentation](phorge/AUTOMATED_SETUP.md)** - Detailed setup info
- **[Spaces & Task Distribution](phorge/README-SPACES.md)** - How Spaces and task distribution work
- **[Phorge Setup Guide](docs/phorge-setup.md)** - Manual Phorge setup instructions
- **[Development Guide](docs/development.md)** - Full development workflow

---

## Quick Tips

1. **Fastest way to get started:** `make phorge-setup`
2. **View workboards:** Navigate to Projects → (any project) → Workboard
3. **Test Spaces:** Tasks in Restricted space should only be editable by admin
4. **Realistic testing:** Tasks are already distributed across workflow stages
5. **Fresh start:** `make phorge-down && make phorge-setup`

Happy coding! 🚀
