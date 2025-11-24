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

# Step 8: Create default Spaces
source "${LIB_DIR}/setup-spaces.sh"
create_spaces

# Step 9: Create default projects and workboards
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
echo "Additional Users Created (RMI GUNNAR Team):"
echo "  - mikael.wallin (Mikael Wallin - Team Lead/Scrum Master)"
echo "  - ove.pettersson (Ove Pettersson - System Architect)"
echo "  - viola.larsson (Viola Larsson - System Administrator)"
echo "  - daniel.lindgren (Daniel Lindgren - DevOps Engineer)"
echo "  - sonja.bergstrom (Sonja Bergström - Windows SharePoint Developer)"
echo "  - gabriel.blomqvist (Gabriel Blomqvist - Windows C# Developer)"
echo "  - sebastian.soderberg (Sebastian Söderberg - Windows C# Developer)"
echo "  - tommy.svensson (Tommy Svensson - QA Engineer)"
echo ""
echo "Default Spaces Created:"
echo "  - Public (default), Internal, Restricted"
echo ""
echo "Default Projects Created:"
echo "  - GUNNAR-Core, Architecture, Infrastructure, Development"
echo "  - QA, SharePoint, Security"
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
