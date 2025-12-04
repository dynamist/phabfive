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

# Configure Phorge database connection
echo "Configuring Phorge database connection..."
cd /app/phorge

./bin/config set mysql.host "$MYSQL_HOST"
./bin/config set mysql.port "$MYSQL_PORT"
./bin/config set mysql.user "$MYSQL_USER"
./bin/config set mysql.pass "$MYSQL_PASS"

# Set base URI if provided
if [ ! -z "$PHABRICATOR_URL" ]; then
  ./bin/config set phabricator.base-uri "$PHABRICATOR_URL"
fi

# Set title if provided - using ui.logo instead of phabricator.title which doesn't exist in Phorge
if [ ! -z "$PHABRICATOR_TITLE" ]; then
  ./bin/config set cluster.instance "$PHABRICATOR_TITLE"
fi

# Set alternate file domain if provided
if [ ! -z "$PHABRICATOR_ALT_FILE_DOMAIN" ]; then
  ./bin/config set security.alternate-file-domain "$PHABRICATOR_ALT_FILE_DOMAIN"
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

echo "Phorge initialization complete!"

# Run custom initialization script
if [ -f /usr/local/bin/init-phorge.sh ]; then
  echo "Running custom initialization script..."
  bash /usr/local/bin/init-phorge.sh
fi

# Start daemons in background
echo "Starting Phorge daemons..."
./bin/phd start

# Start Apache in foreground
exec apache2-foreground
