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

# Checkout specified git refs if provided
if [ ! -z "$PHORGE_GIT_REF" ]; then
  cd /app/phorge
  echo "Fetching phorge..."
  timeout 30 git fetch --all || echo "Warning: fetch timed out or failed for phorge"
  echo "Checking out phorge ref: $PHORGE_GIT_REF"
  git checkout "$PHORGE_GIT_REF" || echo "Warning: checkout failed for phorge"
fi

if [ ! -z "$ARCANIST_GIT_REF" ]; then
  cd /app/arcanist
  echo "Fetching arcanist..."
  timeout 30 git fetch --all || echo "Warning: fetch timed out or failed for arcanist"
  echo "Checking out arcanist ref: $ARCANIST_GIT_REF"
  git checkout "$ARCANIST_GIT_REF" || echo "Warning: checkout failed for arcanist"
fi

cd /app/phorge

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

# Run custom initialization script
if [ -f /usr/local/bin/init-phorge.sh ]; then
  echo "Running custom initialization script..."
  bash /usr/local/bin/init-phorge.sh
fi

# Print git refs with history links
PHORGE_REF=$(cd /app/phorge && git rev-parse --abbrev-ref HEAD 2>/dev/null || git rev-parse --short HEAD)
ARCANIST_REF=$(cd /app/arcanist && git rev-parse --abbrev-ref HEAD 2>/dev/null || git rev-parse --short HEAD)
echo "phorge: https://we.phorge.it/source/phorge/history/${PHORGE_REF}/"
echo "arcanist: https://we.phorge.it/source/arcanist/history/${ARCANIST_REF}/"

# Start daemons in background
echo "Starting Phorge daemons..."
./bin/phd start

# Start Apache in foreground
exec apache2-foreground
