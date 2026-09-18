#!/bin/bash
# Print the credentials summary, with --wait only once Phorge answers

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=phorge/lib/common.sh
source "${LIB_DIR}/common.sh"

# Phorge answers "Site Not Found" for any host but its base URI, so /status/ is
# asked for with that host, the way the readiness probe does
wait_for_http() {
  local host timeout=600
  host=${PHORGE_URL#*://}
  host=${host%%/*}
  until curl -fs -o /dev/null -H "Host: ${host}" http://127.0.0.1/status/; do
    timeout=$((timeout - 2))
    if [ "$timeout" -le 0 ]; then
      echo "WARNING: Phorge did not answer on /status/, printing credentials anyway"
      return
    fi
    sleep 2
  done
}

# RECOVERY_LINK is only set while init-phorge.sh runs, a later `make creds` has
# no one-time link to show and leaves that line out
print_banner() {
  echo ""
  echo "================================"
  echo "Phorge Automated Setup Complete!"
  echo "================================"
  echo ""
  echo "👨‍💻 Username: $PHORGE_ADMIN_USER"
  if [ ! -z "$PHORGE_ADMIN_PASS" ]; then
    echo "🔑 Password: $PHORGE_ADMIN_PASS"
  fi
  echo "🔐 API Token: $PHORGE_ADMIN_TOKEN"
  echo "✉️ Email: $PHORGE_ADMIN_EMAIL"
  if [ ! -z "${RECOVERY_LINK:-}" ]; then
    echo ""
    echo "⚡ Use this one-time link to set your password:"
    echo "   $RECOVERY_LINK"
  fi
  echo ""
  echo "🤖 Users Created:"
  local username email realname
  for user_data in "${FAKE_USERS[@]}"; do
    IFS=':' read -r username email realname <<< "$user_data"
    echo "  - ${username} (${realname})"
  done
  echo ""
  echo "🗂️ Projects Created:"
  local name description parent_name milestone_name
  for project_data in "${DEFAULT_PROJECTS[@]}"; do
    IFS=':' read -r name description <<< "$project_data"
    echo "  - ${name}"
  done
  for milestone_data in "${DEFAULT_MILESTONES[@]}"; do
    IFS=':' read -r parent_name milestone_name <<< "$milestone_data"
    echo "  - ${milestone_name} (${parent_name} milestone)"
  done
  echo ""
  echo "🌌 Spaces Created:"
  local space_id space_name is_default
  for space_data in "${DEFAULT_SPACES[@]}"; do
    IFS=':' read -r space_id space_name is_default <<< "$space_data"
    if [ "$is_default" = "default" ]; then
      echo "  - S${space_id} ${space_name} (default)"
    else
      echo "  - S${space_id} ${space_name}"
    fi
  done
  echo ""
  echo "🌍 Your new Phorge is waiting for you at:"
  echo "   $PHORGE_URL"
  echo ""
  echo "💡 TIP: The API token works immediately without logging in!"
  echo "   PHAB_URL=${PHORGE_URL}/api/ PHAB_TOKEN=${PHORGE_ADMIN_TOKEN} phabfive whoami"
  echo "================================"
}

# If script is run directly (not sourced), print the banner
if [ "${BASH_SOURCE[0]}" -ef "$0" ]; then
  if [ "${1:-}" = "--wait" ]; then
    wait_for_http
  fi
  print_banner
fi
