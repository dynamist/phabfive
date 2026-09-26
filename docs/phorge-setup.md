# Local Phorge Setup

This guide covers setting up a local Phorge instance for **developing** and **testing** phabfive.

## Quick Start

**1. Start Phorge:**

```bash
mise trust && make tools     # k3d, kubectl and kubeconform, pinned in mise.toml
make up
```

`make up` creates the shared k3d cluster `dynamist` (or reuses it), builds the `dynamist/phorge` image, imports it into the cluster, deploys `k8s/overlays/local` into the namespace `phorge` and follows the logs until Phorge is ready, including the admin password recovery link. Docker is required, the cluster runs in it. See [Kubernetes Setup](#kubernetes-setup).

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

On every start the container seeds sample data from [`phorge/seed/`](#sample-data), creating:

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

Two more accounts exist so that `user search --role` has something to find. Neither
has a password:

- **deploy.bot** - Deploy Bot, a bot account (role `bot`)
- **former.employee** - Former Employee, a disabled account (role `disabled`)

### Default Projects

Seven projects with 5-column workboards (Backlog → Up Next → In Progress → In Review → Done):

- **GUNNAR-Core** - Main chip blueprint development and secure design
- **Architecture** - System architecture and design specifications
- **Infrastructure** - Servers, virtualization, and network management
- **Development** - Development tools and environment setup
- **QA** - Testing, quality assurance, and compliance validation
- **SharePoint** - Windows SharePoint integration and document management
- **Security** - Security compliance, hardening, and vulnerability assessment

The admin user is a member of every project, and so are a few of the test users.

### Default Milestones

Two milestones with the same name and the same 5-column workboards:

- **Sprint 1** in **Development** - shown as "Sprint 1 (Development)" in the web UI
- **Sprint 1** in **QA** - shown as "Sprint 1 (QA)" in the web UI

Since they share a name, `--tag "Sprint 1"` only reaches one of them. Use the project ID (from `/project/view/<id>/`) or PHID to target a specific milestone, e.g. `--tag 9`.

### Teams

Eight projects used as groups rather than for work: Management Team, Sales Team,
Marketing Team, Recruitment Team, SOC, Human Resources, Onboarding Team and
Offboarding Team. They have no workboards, and each one owns a space, below.

### Sample Tasks

A handful of tasks, one of them in the SOC space where the admin cannot see it.
For a fuller set, see [Create Test Tasks](#create-test-tasks).

### Default Spaces

Ten spaces, S1 to S10. Most are restricted to a team, a project whose members are
the only ones who can see and edit that space:

| Space | Visible to | Admin sees it |
|-------|------------|---------------|
| **S1 Default** | all users; where everything lands unless told otherwise | yes |
| **S2 Sales Team** | ove.pettersson, sonja.bergstrom | no |
| **S3 Management Team** | admin, mikael.wallin | yes |
| **S4 Marketing Team** | sonja.bergstrom | no |
| **S5 Recruitment Team** | viola.larsson | no |
| **S6 SOC** | tommy.svensson, sebastian.soderberg | no |
| **S7 Human Resources** | viola.larsson, gabriel.blomqvist | no |
| **S8 Onboarding Team** | gabriel.blomqvist | no |
| **S9 Offboarding Team** | daniel.lindgren | no |
| **S10 Archive** | all users | yes |

The teams are ordinary projects, so they also show up in project searches and
completion.

Which spaces the admin cannot see is the point. Spaces have no Conduit search
method, so they are found by probing `S1`, `S2`, `S3`... and `phid.lookup` omits
a space the viewer cannot see exactly as though it did not exist. Visible numbers
are therefore sparse on a real instance, and anything listing them has to probe
past a gap rather than stop at the first miss. Through the admin's API token the
spaces are S1, S3 and S10: S10 sits more than five past S3, which is what an
earlier implementation gave up after, so a dev instance reproduces that case
instead of the tidy one. S3 covers the other side, a restricted space the viewer
is allowed into.

To see the instance as a team member instead, log in to the web UI as one (they
share the admin password) and create an API token under **Settings → Conduit API
Tokens**. With tommy.svensson's token, S6 SOC is listed and its tasks are found.

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

### Repositories

Two hosted git repositories, so nothing about them needs the network:

| Repository | Holds |
|------------|-------|
| **rGUNNAR** GUNNAR Firmware | three commits on `main` and `feature/telemetry`, and the tag `v1.0.0` |
| **rSPIKE** GUNNAR Spike | nothing, for the empty-repository path |

A hosted repository's working copy is normally created by the daemons, whenever
they next look. The seeder creates it itself instead, before Apache starts, and
writes the history in, so the refs are there as soon as the instance answers at
all and nothing has to wait for an import:

```bash
phabfive diffusion repo show GUNNAR --show-branches --show-tags
```

Every commit has a fixed author and date in the data file, so two instances
seeded from the same data have the same commit hashes.

### Credentials

Five Passphrase credentials, created in data file order so the K numbers follow
it. Between them they cover what Diffusion asks of a credential:

| | Type | Conduit access | Covers |
|---|------|----------------|--------|
| **K1** Deployment notes | `note` | yes | readable, but the wrong type for a repository URI |
| **K2** Mirror key, no Conduit access | `ssh-generated-key` | **no** | a credential phabfive cannot read the secret of |
| **K3** Observe key | `ssh-generated-key` | yes | the happy path |
| **K4** Artifact registry login | `password` | yes | a username and password pair |
| **K5** Build agent token | `token` | yes | an API token |

**K2 has Conduit access turned off on purpose.** `passphrase show K2` answers
"Access denied", while attaching it to a repository URI still works, because
that needs only the credential's PHID and type. It is the difference between a
code path that reads the secret and one that does not, so leave it as it is.

The SSH keys are generated by Phorge when the credential is created, and the
password and token are obviously-fake sample values.

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
| `PHORGE_SEED` | `all` | Seed modules to run, space-separated, or `all` / `none` |
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

After configuring phabfive, you can populate Phorge with a year of realistic test tasks:

```bash
uv run phabfive apply -f specs/create/large-programme.yaml
```

This creates a full year simulation of project work, including epics with subtasks, varied priorities, and assignments across the default projects. It is the largest spec that ships - about seventy objects - which is what makes it the one worth running against a disposable instance.

Use `--dry-run` to plan it without creating anything:

```bash
uv run phabfive apply -f specs/create/large-programme.yaml --dry-run
```

`specs/` holds smaller ones too: `specs/create/sprint-tasks.yaml` is three tasks, and `specs/search/blocked-tasks.yaml` is a search to run afterwards with `phabfive search -f`.

## Using the API Token

The API token works immediately without logging in:

```bash
curl "http://phorge.localhost/api/user.whoami" \
  -d "api.token=api-supersecr3tapikeyfordevelop1"
```

## Sample Data

The Phorge pod runs `phorge/seed/seed.php` on every start, after upgrading the
database. It writes through Phorge's own editors and in-process Conduit calls
rather than SQL, so objects get the PHIDs, slugs, memberships, search index and
transaction history the web UI would have given them.

The seeder logs only what it creates. What the instance actually holds is what
the banner prints afterwards, and `make creds` again later, both read back from
the database - the data files below say what a fresh instance would get, which
is a different thing as soon as another branch seeds one that keeps its volume.

```text
phorge/seed/
  seed.php                    runner: orders modules by dependency, logs what it creates
  src/PhabfiveSeedModule.php  base class with the shared helpers
  modules/<key>.php           one module per kind of data
  data/<key>.json             the records that module creates
```

| Module | Needs | Creates |
|--------|-------|---------|
| `auth` | | password login, admin account, API token (from `PHORGE_ADMIN_*`) |
| `users` | auth | test users |
| `teams` | users | team projects |
| `spaces` | auth, teams | spaces, in data file order |
| `projects` | users, spaces | projects, milestones, workboards |
| `tasks` | users, projects, spaces | tasks, through `maniphest.edit` |
| `credentials` | auth | Passphrase credentials, through Phorge's editors |
| `repositories` | auth, users | hosted git repositories and their history, through `diffusion.repository.edit` |

Every module is idempotent - it creates only what is missing - so restarting the
pod duplicates nothing. `PHORGE_SEED` is `all` by default; to seed only part of
it, name the modules and their dependencies come along, or `none` for an empty
instance:

```bash
echo 'PHORGE_SEED=users projects' >> k8s/overlays/local/config.local.env
make up
```

`PHORGE_SEED` reaches the pod through the ConfigMap, like every other setting,
so setting it only in the shell that runs `make up` has no effect. An unset or
empty value seeds everything, the same as `all`.

From a shell in the pod (`make shell`) the runner can be used directly:

```bash
php /usr/local/share/phabfive-seed/seed.php --list
php /usr/local/share/phabfive-seed/seed.php tasks
```

### Adding Sample Data

To add records of a kind that is already seeded, edit its `data/<key>.json`.

To seed something new, add `data/<key>.json` and a `modules/<key>.php` holding a
class that extends `PhabfiveSeedModule`, with `getKey()`, `getDependencies()`
and `seed()`. The runner picks it up without being told about it. `seed()` has
to check for what already exists before creating it. Rebuild the image to use
it (`make up` does).

Two things Phorge enforces that are easy to trip over:

- The editor refuses a policy that would lock out whoever applies it. An object
  the admin must not see has to be created as someone who can, which is why a
  team's space is created by a member of the team.
- A seed run is a single request, and Phorge caches some lookups for the length
  of one. The runner clears that cache between modules; within a module, load
  what you create rather than trusting an earlier query.

## Kubernetes Setup

Phorge runs in a local [k3d](https://k3d.io) cluster, which is k3s in Docker. The cluster can be shared with other Dynamist dev apps, and each app keeps to its own namespace:

- **Cluster:** `k8s/cluster/k3d.yaml`, identical in every repo that uses it. It pins the k3s version and publishes the bundled Traefik ingress on `127.0.0.1:80` and `:443`. Whichever app starts first creates the cluster, the others reuse it.
- **Routing:** each app has a standard `Ingress` with its own hostnames, here `phorge.localhost` and `cdn.localhost` to the `phorge` Service.
- **Phorge:** `k8s/base` holds the `phorge` namespace, MariaDB (StatefulSet `mariadb`), Phorge (Deployment `phorge`, `Recreate` so two pods never upgrade the same database), the Ingress, a ResourceQuota with default limits and NetworkPolicies. Only Traefik reaches Phorge and only Phorge reaches MariaDB, whose port is not published on the host. Overlays: `local` (with `config.local.env`) and `ci`.
- **Images:** `make phorge-image` builds `dynamist/phorge`, tags it by content and imports it with `k3d image import`, no registry is involved.

Every `make` target passes `--context k3d-dynamist`, so it never acts on another cluster.

## Useful Commands

```bash
make up                  # Create/reuse the cluster, build and deploy Phorge, follow logs
make down                # Stop Phorge and MariaDB, keep data
make reset               # Delete the phorge namespace and its data, clear the completion cache
make destroy             # Delete the whole cluster (FORCE=1 if other apps run)
make logs / make ps      # Follow Phorge logs / show pods, ingress, volumes
make creds               # Print the credentials of the running Phorge
make shell               # Open a shell in the Phorge pod
make validate            # Validate the rendered manifests with kubeconform
make test-k8s            # Smoke, seed data and isolation tests against the deployed Phorge
make test-e2e            # End-to-end tests of the phabfive CLI against the deployed Phorge
```

## Testing Against the Cluster

Both test suites only run when asked for, a plain `pytest` skips them:

- **`make test-k8s`** (`tests/k8s`): the home page, the file domain and the API token work through Traefik, unknown hosts get a 404, the users, teams, projects, milestones, spaces, credentials and repositories from `phorge/seed/data/` exist (including the seeded repositories' branches and tags), and pods in other namespaces cannot reach Phorge or MariaDB.
- **`make test-e2e`** (`tests/e2e`): end-to-end tests of phabfive itself, running the CLI against the instance and creating and editing real tasks.

CI (`.github/workflows/k8s.yml`) validates the manifests, then creates a k3d cluster on the runner, deploys the `ci` overlay and runs both suites. A coexistence job deploys the apps listed in the repository variable `COEXISTENCE_REPOS` (space separated `owner/name`) into the same cluster and runs every app's tests, which also checks that the apps cannot reach each other and that all repos pin the same `k8s/cluster/k3d.yaml`. Each of those repos must provide the make targets `ci-deploy` and `ci-test`.

## Troubleshooting

### Forgot the credentials

```bash
make creds
```

It runs `phorge/lib/banner.sh` in the pod, the same summary the logs print at the
end of the setup. The credentials come from the deployment's own environment and
the user, project and Space lists are read back from the database, so both
describe the instance that is running rather than what this branch would seed
into an empty one.

### Get admin password recovery link

The link is one-time and only exists while the setup runs, so `make creds` cannot
show it. It is displayed in logs when Phorge starts:

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
