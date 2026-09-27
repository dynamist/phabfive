#!/bin/bash
set -e

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
# fails with "detected dubious ownership". Phorge's own phd.user is not the way
# to do this: it switches with sudo, which is not installed, and a non-empty
# phd.user also makes Phorge advertise built-in SSH clone URIs this instance has
# no sshd to serve.
echo "Starting Phorge daemons..."
mkdir -p /var/tmp/phd
chown -R www-data:www-data /var/tmp/phd
runuser -u www-data -- ./bin/phd start

# Start Apache in foreground
exec apache2-foreground
