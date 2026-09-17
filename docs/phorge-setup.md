# Local Phorge Setup

This guide covers setting up a local Phorge instance for **developing** and **testing** phabfive.

## Quick Start

**1. Start Phorge:**

```bash
mise trust && make tools     # k3d, kubectl and kubeconform, pinned in mise.toml
make up
```

`make up` creates the shared k3d cluster `dynamist-dev` (or reuses it), builds the `dynamist/phorge` image, imports it into the cluster, deploys `k8s/overlays/local` into the namespace `phorge` and follows the logs until Phorge is ready, including the admin password recovery link. Docker is required, the cluster runs in it. See [Kubernetes Setup](#kubernetes-setup).

**2. Stop Phorge:** `make down` stops Phorge and MariaDB and keeps the data. `make reset` deletes the `phorge` namespace with all its data.

`make reset` also clears phabfive's completion cache for this host. The cache is keyed by URL and token, neither of which changes, so a fresh instance would otherwise reuse the previous one's cached projects and users. Every account cached for this host is cleared, since a fresh instance invalidates all of them; no other host is touched. `make destroy` clears it too.

Phorge is served at <http://phorge.localhost> and uploaded files at <http://cdn.localhost>, both through the cluster's Traefik ingress on `127.0.0.1:80`. No `/etc/hosts` entry is needed: names under `.localhost` are reserved for the loopback address by [RFC 6761](https://www.rfc-editor.org/rfc/rfc6761#section-6.3), and resolvers such as systemd-resolved map them automatically.

If your resolver does not, add the entries yourself:

```bash
# Check first - this should print a loopback address
getent hosts phorge.localhost

# Only if it prints nothing, add to /etc/hosts
127.0.0.1       phorge.localhost
127.0.0.1       cdn.localhost
```

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

### Default Milestones

Two milestones with the same name and the same 5-column workboards:

- **Sprint 1** in **Development** - shown as "Sprint 1 (Development)" in the web UI
- **Sprint 1** in **QA** - shown as "Sprint 1 (QA)" in the web UI

Since they share a name, `--tag "Sprint 1"` only reaches one of them. Use the project ID (from `/project/view/<id>/`) or PHID to target a specific milestone, e.g. `--tag 9`.

### Default Spaces

Three spaces, at deliberately non-consecutive S numbers:

- **S1 Default** - the default space, where everything lands unless told otherwise
- **S3 Restricted**
- **S10 Archive**

The gaps are the point. Spaces have no Conduit search method, so they are found
by probing `S1`, `S2`, `S3`... and `phid.lookup` omits a space the viewer cannot
see exactly as though it did not exist. Visible numbers are therefore sparse on a
real instance, and anything listing them has to probe past a gap rather than stop
at the first miss. S10 sits more than five past S3, which is what an earlier
implementation gave up after, so a dev instance now reproduces that case instead
of the tidy one.

Filter by them with `maniphest search --space`, and note that phabfive defaults to
`PHAB_SPACE=S1`, so tasks in S3 and S10 are excluded until you ask for them:

```bash
phabfive maniphest search --space '*' --tag '*'
phabfive maniphest search --space S3 --tag '*'
```

They are also where `--space` on `create` and `edit` can be exercised, this
being an instance it is safe to write to:

```bash
phabfive maniphest create "Something to file away" --space=Archive
phabfive maniphest edit T1 --space=S3
```

## Configuration

The settings are in `k8s/base/config.env` and, for credentials, `k8s/base/secret.env`. To override settings locally, put them in the gitignored `k8s/overlays/local/config.local.env` and run `make up`:

```bash
echo PHORGE_GIT_REF=master >> k8s/overlays/local/config.local.env
make up
```

### Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `PHORGE_URL` | `http://phorge.localhost` | Base URL for Phorge |
| `PHORGE_CDN_URL` | `http://cdn.localhost` | CDN URL for serving files |
| `PHORGE_TITLE` | `RMI` | Instance title shown in UI |
| `PHORGE_ADMIN_USER` | `admin` | Admin username |
| `PHORGE_ADMIN_EMAIL` | `admin@domain.tld` | Admin email address |
| `PHORGE_ADMIN_NAME` | `Administrator` | Admin display name |
| `PHORGE_ADMIN_PASS` | `supersecr3tpassw0rdfordevelop1` | Admin password, enables immediate login (secret) |
| `PHORGE_ADMIN_TOKEN` | `api-supersecr3tapikeyfordevelop1` | Pre-configured API token (secret) |
| `PHORGE_GIT_REF` | `stable` | Git branch/tag/commit for Phorge |
| `ARCANIST_GIT_REF` | `stable` | Git branch/tag/commit for Arcanist |
| `MYSQL_PASS` / `MARIADB_ROOT_PASSWORD` | `supersecr3tpassw0rdfordatabase1` | MariaDB root password (secret) |

The Git refs are fetched and checked out when the pod starts. The `ci` overlay leaves them empty, which tests the Phorge and Arcanist baked into the image instead.

## Configure phabfive

Use the pre-configured API token:

**Environment variables:**

```bash
export PHAB_TOKEN=api-supersecr3tapikeyfordevelop1
export PHAB_URL=http://phorge.localhost/api/
```

**Or configuration file:**

```bash
# Linux
echo "PHAB_TOKEN: api-supersecr3tapikeyfordevelop1" > ~/.config/phabfive.yaml
echo "PHAB_URL: http://phorge.localhost/api/" >> ~/.config/phabfive.yaml

# macOS
echo "PHAB_TOKEN: api-supersecr3tapikeyfordevelop1" > ~/Library/Application\ Support/phabfive.yaml
echo "PHAB_URL: http://phorge.localhost/api/" >> ~/Library/Application\ Support/phabfive.yaml
```

Test it:

```bash
uv run phabfive user whoami
uv run phabfive diffusion repo list
uv run phabfive maniphest search --tag '*'
```

`user whoami` reports the host `PHAB_URL` points at. Without `PHAB_URL` it reports every host in `~/.arcrc` instead; `--all` forces that.

## Create Test Tasks

After configuring phabfive, you can populate Phorge with ~70 realistic test tasks:

```bash
uv run phabfive maniphest create templates/task-create/mega-2024-simulation.yml
```

This creates a full year simulation of project work for the RMI GUNNAR team, including EPICs with subtasks, varied priorities, and assignments across the default projects.

Use `--dry-run` to preview without creating:

```bash
uv run phabfive maniphest create templates/task-create/mega-2024-simulation.yml --dry-run
```

## Using the API Token

The API token works immediately without logging in:

```bash
curl "http://phorge.localhost/api/user.whoami" \
  -d "api.token=api-supersecr3tapikeyfordevelop1"
```

## How It Works

The script runs automatically when the Phorge pod starts and:

1. Enables username/password authentication
2. Creates admin and test user accounts with verified emails
3. Generates API token for immediate use
4. Creates default projects with workboard columns
5. Creates default milestones with workboard columns
6. Sets passwords for all users (if `PHORGE_ADMIN_PASS` is set) or generates a recovery link

All operations are idempotent - safe to run multiple times. Pod restarts won't duplicate data.

## Kubernetes Setup

Phorge runs in a local [k3d](https://k3d.io) cluster, which is k3s in Docker. The cluster can be shared with other Dynamist dev apps, and each app keeps to its own namespace:

- **Cluster:** `k8s/cluster/k3d.yaml`, identical in every repo that uses it. It pins the k3s version and publishes the bundled Traefik ingress on `127.0.0.1:80` and `:443`. Whichever app starts first creates the cluster, the others reuse it.
- **Routing:** each app has a standard `Ingress` with its own hostnames, here `phorge.localhost` and `cdn.localhost` to the `phorge` Service.
- **Phorge:** `k8s/base` holds the `phorge` namespace, MariaDB (StatefulSet `mariadb`), Phorge (Deployment `phorge`, `Recreate` so two pods never upgrade the same database), the Ingress, a ResourceQuota with default limits and NetworkPolicies. Only Traefik reaches Phorge and only Phorge reaches MariaDB, whose port is not published on the host. Overlays: `local` (with `config.local.env`) and `ci`.
- **Images:** `make phorge-image` builds `dynamist/phorge`, tags it by content and imports it with `k3d image import`, no registry is involved.

Every `make` target passes `--context k3d-dynamist-dev`, so it never acts on another cluster.

## Useful Commands

```bash
make up                  # Create/reuse the cluster, build and deploy Phorge, follow logs
make down                # Stop Phorge and MariaDB, keep data
make reset               # Delete the phorge namespace and its data, clear the completion cache
make destroy             # Delete the whole cluster (FORCE=1 if other apps run)
make logs / make ps      # Follow Phorge logs / show pods, ingress, volumes
make shell               # Open a shell in the Phorge pod
make validate            # Validate the rendered manifests with kubeconform
make test-k8s            # Smoke, seed data and isolation tests against the deployed Phorge
```

## Testing Against the Cluster

The tests only run when asked for, a plain `pytest` skips them:

- **`make test-k8s`** (`tests/k8s`): the home page, the file domain and the API token work through Traefik, unknown hosts get a 404, the users, projects, milestones and spaces from `phorge/lib/common.sh` exist, and pods in other namespaces cannot reach Phorge or MariaDB.

CI (`.github/workflows/k8s.yml`) validates the manifests, then creates a k3d cluster on the runner, deploys the `ci` overlay and runs `make test-k8s`. A coexistence job deploys the apps listed in the repository variable `COEXISTENCE_REPOS` (space separated `owner/name`) into the same cluster and runs every app's tests, which also checks that the apps cannot reach each other and that all repos pin the same `k8s/cluster/k3d.yaml`. Each of those repos must provide the make targets `ci-deploy` and `ci-test`.

## Troubleshooting

### Get admin password recovery link

The link is displayed in logs when Phorge starts. If you miss it:

```bash
make logs | grep "one-time link"
```

### Generate a new recovery link

```bash
make shell
/app/phorge/bin/auth recover admin
```

### Data Persistence

The database and Phorge's repositories and files are PersistentVolumeClaims in the `phorge` namespace, stored by k3s's `local-path` provisioner inside the cluster's Docker container. `make down` and restarting Docker keep them, `make reset` deletes them and `make destroy` deletes them along with the cluster.

## Security Notes

**⚠️ IMPORTANT:**

These scripts are intended for development and testing only. For production, follow best practices for securing your Phorge instance.

## References

- [Phorge Documentation](https://we.phorge.it/book/phorge/)
- [Configuring Accounts and Registration](https://we.phorge.it/book/phorge/article/configuring_accounts_and_registration/)
- [Local Phorge API Documentation](http://phorge.localhost/conduit/)
