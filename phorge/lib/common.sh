#!/bin/bash
# Common configuration and functions for Phorge initialization scripts

# Exit on error
set -e

# Configuration - can be overridden via environment variables
export PHORGE_URL="${PHORGE_URL:-http://phorge.localhost}"
export PHORGE_PATH="${PHORGE_PATH:-/app/phorge}"
export PHORGE_ADMIN_USER="${PHORGE_ADMIN_USER:-admin}"
export PHORGE_ADMIN_PASS="${PHORGE_ADMIN_PASS:-supersecr3tpassw0rdfordevelop1}"
export PHORGE_ADMIN_EMAIL="${PHORGE_ADMIN_EMAIL:-admin@domain.tld}"
export PHORGE_ADMIN_NAME="${PHORGE_ADMIN_NAME:-Administrator}"
export PHORGE_ADMIN_TOKEN="${PHORGE_ADMIN_TOKEN:-api-supersecr3tapikeyfordevelop1}"

# Fake users for testing (RMI GUNNAR Team)
export FAKE_USERS=(
  "mikael.wallin:mikael.wallin@air.rmi.se:Mikael Wallin"
  "ove.pettersson:ove.pettersson@air.rmi.se:Ove Pettersson"
  "viola.larsson:viola.larsson@air.rmi.se:Viola Larsson"
  "daniel.lindgren:daniel.lindgren@air.rmi.se:Daniel Lindgren"
  "sonja.bergstrom:sonja.bergstrom@air.rmi.se:Sonja Bergström"
  "gabriel.blomqvist:gabriel.blomqvist@air.rmi.se:Gabriel Blomqvist"
  "sebastian.soderberg:sebastian.soderberg@air.rmi.se:Sebastian Söderberg"
  "tommy.svensson:tommy.svensson@air.rmi.se:Tommy Svensson"
)

# Default projects/workboards (RMI GUNNAR system projects)
export DEFAULT_PROJECTS=(
  "GUNNAR-Core:Main chip blueprint development and secure design"
  "Architecture:System architecture and design specifications"
  "Infrastructure:Servers, virtualization, and network management"
  "Development:Development tools and environment setup"
  "QA:Testing, quality assurance, and compliance validation"
  "SharePoint:Windows SharePoint integration and document management"
  "Security:Security compliance, hardening, and vulnerability assessment"
)

# Default milestones (parent project:milestone name). Milestones in different
# projects share a name, shown as "Sprint 1 (Development)" and "Sprint 1 (QA)"
# in the web UI, so the API can only tell them apart by ID or PHID.
export DEFAULT_MILESTONES=(
  "Development:Sprint 1"
  "QA:Sprint 1"
)

# Default spaces (S number:name:default flag). The numbers are deliberately
# not consecutive: phid.lookup hides a Space the viewer cannot see exactly as
# though it were absent, so visible S numbers are sparse on a real instance
# and anything discovering them has to probe past a gap. S10 sits more than
# five past S3, which is what an earlier implementation gave up after.
export DEFAULT_SPACES=(
  "1:Default:default"
  "3:Restricted:"
  "10:Archive:"
)

# Function to call a Conduit method in-process as the admin user.
# Runs without the web server, so it can be used during setup.
conduit_call() {
  local method=$1
  local params=$2
  echo "$params" | /app/phorge/bin/conduit call --local --as "$PHORGE_ADMIN_USER" \
    --method "$method" --input - 2>/dev/null
}

# Function to generate a PHID
generate_phid() {
  local type=$1
  local unique_string=$(date +%s%N)-$(openssl rand -hex 8)
  local hash=$(echo -n "${unique_string}" | sha256sum | cut -c1-40 | tr '[:lower:]' '[:upper:]')
  echo "PHID-${type}-${hash:0:20}"
}

# Function to get current timestamp
get_timestamp() {
  date +%s
}

# Function to execute MySQL command with standard connection params
mysql_exec() {
  local database=$1
  shift
  mysql -h"$MYSQL_HOST" -P"$MYSQL_PORT" -u"$MYSQL_USER" -p"$MYSQL_PASS" "$database" "$@"
}

# Function to execute MySQL query and return result
mysql_query() {
  local database=$1
  local query=$2
  mysql -h"$MYSQL_HOST" -P"$MYSQL_PORT" -u"$MYSQL_USER" -p"$MYSQL_PASS" "$database" -N -e "$query" 2>/dev/null || echo "0"
}

# Function to execute MySQL query and return the rows, tab separated.
# Unlike mysql_query it answers a failure with a non-zero status rather than
# "0", so a caller can tell an empty result from an unreachable database, and
# it asks for utf8mb4 because the client would otherwise mangle a name like
# "Sonja Bergström" on the way out. The connect timeout keeps a caller such as
# banner.sh from hanging when the database is not there at all.
mysql_rows() {
  local database=$1
  local query=$2
  mysql --default-character-set=utf8mb4 --connect-timeout=5 \
    -h"$MYSQL_HOST" -P"$MYSQL_PORT" \
    -u"$MYSQL_USER" -p"$MYSQL_PASS" "$database" -N -B -e "$query" 2>/dev/null
}

# Function to hash a password using bcrypt (PHP's password_hash)
hash_password() {
  local password="$1"
  php -r "echo password_hash(\$argv[1], PASSWORD_BCRYPT);" -- "$password"
}
