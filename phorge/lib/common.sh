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

# Additional fake users for testing (RMI GUNNAR team members)
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

# Default Spaces (name:description:viewPolicy:editPolicy:isDefault)
# Policy values: "public" (anyone), "users" (logged in), "PHID-PLCY-admin" (admins only)
export DEFAULT_SPACES=(
  "Public:Public space for customer-facing work:public:users:1"
  "Internal:Internal team work and development:users:users:0"
  "Restricted:Security-sensitive and compliance work:users:PHID-PLCY-admin:0"
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
