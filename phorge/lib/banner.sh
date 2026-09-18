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

# The three lists below are read from the database rather than from the arrays
# in common.sh, so the banner describes the instance that is actually running.
# The two disagree whenever the deployed data was seeded by a different branch,
# and the arrays are only what this branch would create on a fresh instance.
db_unreachable() {
  echo "  (could not read from the database)"
}

print_users() {
  echo "🤖 Users:"
  local rows username realname
  if ! rows=$(mysql_rows phabricator_user "
        SELECT userName, IFNULL(realName, '')
        FROM user
        WHERE isSystemAgent = 0
          AND isMailingList = 0
          AND userName != '${PHORGE_ADMIN_USER}'
        ORDER BY id"); then
    db_unreachable
    return
  fi
  while IFS=$'\t' read -r username realname; do
    [ -n "$username" ] || continue
    if [ -n "$realname" ]; then
      echo "  - ${username} (${realname})"
    else
      echo "  - ${username}"
    fi
  done <<< "$rows"
}

print_projects() {
  echo "🗂️ Projects:"
  # A milestone is a child with a milestoneNumber, and is shown under its
  # parent's name the way the web UI does. A plain subproject just lists
  # itself. status 100 is archived.
  local rows name parent archived
  if ! rows=$(mysql_rows phabricator_project "
        SELECT p.name,
               IF(p.milestoneNumber IS NULL, '', IFNULL(parent.name, '')),
               IF(p.status = 100, 'archived', '')
        FROM project p
        LEFT JOIN project parent ON parent.phid = p.parentProjectPHID
        ORDER BY p.id"); then
    db_unreachable
    return
  fi
  while IFS=$'\t' read -r name parent archived; do
    [ -n "$name" ] || continue
    local line="  - ${name}"
    if [ -n "$parent" ]; then
      line="${line} (${parent} milestone)"
    fi
    if [ -n "$archived" ]; then
      line="${line} [archived]"
    fi
    echo "$line"
  done <<< "$rows"
}

print_spaces() {
  echo "🌌 Spaces:"
  local rows space_id name is_default archived
  if ! rows=$(mysql_rows phabricator_spaces "
        SELECT id,
               namespaceName,
               IF(isDefaultNamespace = 1, 'default', ''),
               IF(isArchived = 1, 'archived', '')
        FROM spaces_namespace
        ORDER BY id"); then
    db_unreachable
    return
  fi
  while IFS=$'\t' read -r space_id name is_default archived; do
    [ -n "$space_id" ] || continue
    local line="  - S${space_id} ${name}"
    if [ -n "$is_default" ]; then
      line="${line} (default)"
    fi
    if [ -n "$archived" ]; then
      line="${line} [archived]"
    fi
    echo "$line"
  done <<< "$rows"
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
  print_users
  echo ""
  print_projects
  echo ""
  print_spaces
  echo ""
  echo "🌍 Your new Phorge is waiting for you at:"
  echo "   $PHORGE_URL"
  echo ""
  echo "💡 TIP: The API token works immediately without logging in!"
  echo "   PHAB_URL=${PHORGE_URL}/api/ PHAB_TOKEN=${PHORGE_ADMIN_TOKEN} phabfive user whoami"
  echo "================================"
}

# If script is run directly (not sourced), print the banner
if [ "${BASH_SOURCE[0]}" -ef "$0" ]; then
  if [ "${1:-}" = "--wait" ]; then
    wait_for_http
  fi
  print_banner
fi
