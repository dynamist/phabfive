#!/bin/bash
set -e

# One image, two containers in the phorge pod:
#
#   entrypoint.sh web   configure Phorge, upgrade storage, seed, run the daemons and Apache
#   entrypoint.sh sshd  serve git over SSH, once web has configured Phorge
#
# Phorge reads its configuration from conf/local/local.json, which web writes
# into a volume both containers mount there. web touches READY when the
# configuration is written and the database is upgraded, sshd waits for it.
MODE=${1:-web}
READY=/app/phorge/conf/local/.ready

if [ "$MODE" = sshd ]; then
  echo "Waiting for the web container to configure Phorge..."
  until [ -e "$READY" ]; do
    sleep 2
  done

  # On the volume, so clients' known_hosts survive a restart
  HOST_KEY=/var/lib/phabfive/ssh/ssh_host_ed25519_key
  if [ ! -s "$HOST_KEY" ]; then
    mkdir -p "$(dirname "$HOST_KEY")"
    ssh-keygen -q -t ed25519 -N '' -C phorge -f "$HOST_KEY"
  fi

  mkdir -p /run/sshd
  echo "Starting sshd..."
  exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config.phorge
elif [ "$MODE" != web ]; then
  echo "Usage: entrypoint.sh [web|sshd]" >&2
  exit 2
fi

# A restarted web container rewrites the configuration, a restarted sshd
# container should not start on the old one meanwhile
rm -f "$READY"

# Default values
MYSQL_HOST=${MYSQL_HOST:-mariadb}
MYSQL_PORT=${MYSQL_PORT:-3306}
MYSQL_USER=${MYSQL_USER:-root}
MYSQL_PASS=${MYSQL_PASS:-}

echo "Waiting for database to be ready..."
until mysql -h"$MYSQL_HOST" -P"$MYSQL_PORT" -u"$MYSQL_USER" -p"$MYSQL_PASS" -e "SELECT 1" >/dev/null 2>&1; do
  echo "Database not ready, waiting..."
  sleep 2
done
echo "Database is ready!"

# A database cannot go back to an older Phorge, and the schema of another
# branch is not the schema of this one. Remember the version the data was set up
# with, and refuse another one instead of failing somewhere in storage upgrade.
# The name is compared, so stable moving forward upgrades as usual.
VERSION_FILE=/var/lib/phabfive/phorge-version
if [ -n "$PHORGE_VERSION" ]; then
  if [ -s "$VERSION_FILE" ] && [ "$(cat "$VERSION_FILE")" != "$PHORGE_VERSION" ]; then
    echo "ERROR: the database was set up with Phorge $(cat "$VERSION_FILE"), this image runs Phorge ${PHORGE_VERSION}"
    echo "ERROR: run 'make reset' to delete the data, then 'make up VERSION=${PHORGE_VERSION}' again"
    exit 1
  fi
  mkdir -p "$(dirname "$VERSION_FILE")"
  echo "$PHORGE_VERSION" >"$VERSION_FILE"
fi

# Configure Phorge database connection
echo "Configuring Phorge database connection..."
cd /app/phorge

./bin/config set mysql.host "$MYSQL_HOST"
./bin/config set mysql.port "$MYSQL_PORT"
./bin/config set mysql.user "$MYSQL_USER"
./bin/config set mysql.pass "$MYSQL_PASS"

# Set base URI if provided
if [ ! -z "$PHORGE_URL" ]; then
  ./bin/config set phabricator.base-uri "$PHORGE_URL"
fi

# Further URIs Phorge answers to besides PHORGE_URL, space-separated. Phorge
# checks the Host header and answers "Site Not Found" for any other host, so
# this is what lets a client inside the cluster use the Service DNS name.
# Deleted when empty, a restarted container keeps its local config.
if [ -n "$PHORGE_ALLOWED_URIS" ]; then
  ./bin/config set phabricator.allowed-uris \
    "$(php -r 'echo json_encode(preg_split("/\s+/", trim(getenv("PHORGE_ALLOWED_URIS"))));')"
else
  ./bin/config delete phabricator.allowed-uris >/dev/null 2>&1 || true
fi

# git over SSH, served by the sshd container. phd.user names the user the
# repositories belong to: ssh-exec runs as PHORGE_SSH_USER and runs git as
# phd.user through sudo. Apache and the daemons already run as www-data, so
# they never need sudo. A non-empty phd.user is also what makes Phorge
# advertise built-in ssh:// URIs, on PHORGE_SSH_HOST and PHORGE_SSH_PORT, the
# address a client reaches sshd at (`make ssh-forward`), not the pod's.
if [ -n "$PHORGE_SSH_USER" ]; then
  ./bin/config set phd.user www-data
  ./bin/config set diffusion.ssh-user "$PHORGE_SSH_USER"
  for option in "diffusion.ssh-host $PHORGE_SSH_HOST" "diffusion.ssh-port $PHORGE_SSH_PORT"; do
    # shellcheck disable=SC2086
    set -- $option
    if [ -n "$2" ]; then
      ./bin/config set "$1" "$2"
    else
      ./bin/config delete "$1" >/dev/null 2>&1 || true
    fi
  done
else
  for option in phd.user diffusion.ssh-user diffusion.ssh-host diffusion.ssh-port; do
    ./bin/config delete "$option" >/dev/null 2>&1 || true
  done
fi

# Set title if provided - using ui.logo instead of phabricator.title which doesn't exist in Phorge
if [ ! -z "$PHORGE_TITLE" ]; then
  ./bin/config set cluster.instance "$PHORGE_TITLE"
fi

# Set alternate file domain if provided
if [ ! -z "$PHORGE_CDN_URL" ]; then
  ./bin/config set security.alternate-file-domain "$PHORGE_CDN_URL"
fi

# Configure repository local path
echo "Configuring repository local path..."
mkdir -p /app/repo
./bin/config set repository.default-local-path /app/repo

# Configure large file storage
echo "Configuring large file storage..."
mkdir -p /app/files
# Volumes are mounted owned by root, Apache and the daemons write as www-data.
# Recursive because the daemons used to run as root and left root-owned
# repositories behind on the volume, and git refuses to read a repository
# owned by another user ("detected dubious ownership").
chown -R www-data:www-data /app/repo /app/files
./bin/config set storage.local-disk.path /app/files

# Configure PHP settings
echo "Configuring PHP settings..."
./bin/config set phabricator.timezone "UTC"

# Enable Pygments for syntax highlighting
echo "Enabling Pygments..."
./bin/config set pygments.enabled true

# Build static resource map
echo "Building static resource map..."
./bin/celerity map

# Initialize storage and upgrade database schema
echo "Initializing Phorge storage..."
./bin/storage upgrade --force

# Seed sample data: every module, or only those PHORGE_SEED names (and what
# they depend on). Safe to rerun, so a container restart creates nothing new.
echo "Seeding sample data..."
# shellcheck disable=SC2086
php /usr/local/share/phabfive-seed/seed.php $PHORGE_SEED

# Without a password there is no way in except a one-time link
if [ -z "$PHORGE_ADMIN_PASS" ]; then
  RECOVERY_LINK=$(./bin/auth recover "${PHORGE_ADMIN_USER:-admin}" 2>&1 |
    grep -o 'http[s]*://[^[:space:]]*' || true)
  export RECOVERY_LINK
fi

# The banner reads the users, projects and Spaces back from the database, so it
# describes this instance rather than what the seed data would have created
source /usr/local/bin/lib/banner.sh
print_banner

# Start daemons in background, as the user Apache serves Conduit with. As root
# they create /app/repo/<id> owned by root, and every git-backed ref query then
# fails with "detected dubious ownership". Started as phd.user rather than
# letting phd switch to it, which would take a sudo rule for root as well.
echo "Starting Phorge daemons..."
mkdir -p /var/tmp/phd
chown -R www-data:www-data /var/tmp/phd
runuser -u www-data -- ./bin/phd start

touch "$READY"

# Start Apache in foreground
exec apache2-foreground
