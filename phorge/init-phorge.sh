#!/bin/bash
# Phorge initialization orchestrator script
# This script coordinates all Phorge setup modules

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

# Step 8: Create default projects and workboards
source "${LIB_DIR}/setup-projects.sh"
create_projects

# Display final summary
echo ""
echo "================================"
echo "Phorge Automated Setup Complete!"
echo "================================"
echo "Admin Username: $ADMIN_USERNAME"
echo "Admin Email: $ADMIN_EMAIL"
echo "API Token: $API_TOKEN"
echo ""
echo "Additional Users Created:"
echo "  - hholm (Henrik Holm)"
echo "  - grok (Grok User)"
echo "  - tester (Test User)"
echo ""
echo "Default Projects Created:"
echo "  - Admin, Client, IA, Tester"
echo "  - Development, Staging, Production"
echo ""
if [ ! -z "$RECOVERY_LINK" ]; then
  echo "🔑 Use this one-time link to set your password:"
  echo "   $RECOVERY_LINK"
  echo ""
fi
echo "After setting a password, you can log in at:"
echo "   ${PHABRICATOR_URL:-http://phorge.domain.tld}"
echo ""
echo "💡 TIP: The API token works immediately without logging in!"
echo "================================"
