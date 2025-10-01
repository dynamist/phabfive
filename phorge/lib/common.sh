#!/bin/bash
# Common configuration and functions for Phorge initialization scripts

# Exit on error
set -e

# Configuration - modify these as needed
export PHORGE_PATH="/app/phorge"
export ADMIN_USERNAME="admin"
export ADMIN_EMAIL="admin@example.com"
export ADMIN_REALNAME="Administrator"
export API_TOKEN="api-supersecr3tapikeyfordevelop1"

# Additional fake users for testing (inspired by test templates)
export FAKE_USERS=(
  "hholm:hholm@example.com:Henrik Holm"
  "grok:grok@example.com:Grok User"
  "tester:tester@example.com:Test User"
)

# Default projects/workboards (inspired by test templates)
export DEFAULT_PROJECTS=(
  "Admin:Administrative tasks and operations"
  "Client:Client-related work and deliverables"
  "IA:Information Architecture"
  "Tester:Testing and QA"
  "Development:Development environment tasks"
  "Staging:Staging environment tasks"
  "Production:Production environment tasks"
)

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
