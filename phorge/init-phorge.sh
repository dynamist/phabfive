#!/bin/bash

# Phorge initialization orchestrator script

set -e

# Get script directory and lib directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

# Source common functions and configuration
source "${LIB_DIR}/common.sh"

echo "======================================="
echo "Phorge Automated Setup"
echo "======================================="
echo "Phorge initialization script starting..."
echo ""

# Wait a moment for everything to be ready
sleep 2

# Step 1: Setup authentication provider
source "${LIB_DIR}/setup-auth.sh"
setup_auth_provider

# Step 2-3: Create admin user and email
source "${LIB_DIR}/setup-users.sh"
create_admin_user
create_admin_email

# Step 4: Setup password authentication for admin
setup_password_auth

# Step 5: Create API token
source "${LIB_DIR}/setup-tokens.sh"
create_api_token

# Step 6: Generate password recovery link
generate_recovery_link

# Step 7: Create additional fake users
create_fake_users

# Step 8: Set passwords if PHORGE_ADMIN_PASS is provided
if [ ! -z "$PHORGE_ADMIN_PASS" ]; then
  echo ""
  echo "Setting passwords for all users..."
  set_user_password "$PHORGE_ADMIN_USER" "$PHORGE_ADMIN_PASS"
  for user_data in "${FAKE_USERS[@]}"; do
    IFS=':' read -r username email realname <<< "$user_data"
    set_user_password "$username" "$PHORGE_ADMIN_PASS"
  done
fi

# Step 9: Create default projects and workboards
source "${LIB_DIR}/setup-projects.sh"
create_projects

# Step 10: Create milestones (with workboards) under the default projects
create_milestones

# Step 11: Create default spaces
source "${LIB_DIR}/setup-spaces.sh"
create_spaces

# Display final summary
source "${LIB_DIR}/banner.sh"
print_banner
