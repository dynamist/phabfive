#!/bin/bash
# Spaces setup for Phorge

# Source common functions and variables
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${LIB_DIR}/common.sh"

create_spaces() {
  echo ""
  echo "Creating default Spaces..."

  # Get admin user PHID for space creation
  ADMIN_PHID=$(mysql_query phabricator_user "SELECT phid FROM user WHERE userName='$ADMIN_USERNAME'")

  if [ -z "$ADMIN_PHID" ]; then
    echo "ERROR: Admin user not found. Run setup-users.sh first."
    return 1
  fi

  for space_data in "${DEFAULT_SPACES[@]}"; do
    IFS=':' read -r space_name space_desc view_policy edit_policy is_default <<< "$space_data"

    # Check if space exists by name
    SPACE_COUNT=$(mysql_query phabricator_spaces "SELECT COUNT(*) FROM spaces_namespace WHERE namespaceName='$space_name'")

    if [ "$SPACE_COUNT" -eq 0 ]; then
      SPACE_PHID=$(generate_phid "SPCE")
      TIMESTAMP=$(get_timestamp)

      echo "Creating space '$space_name' with PHID: $SPACE_PHID"

      # Create space
      mysql_exec phabricator_spaces <<EOF
INSERT INTO spaces_namespace (
  phid,
  namespaceName,
  description,
  viewPolicy,
  editPolicy,
  isDefaultNamespace,
  isArchived,
  dateCreated,
  dateModified
) VALUES (
  '$SPACE_PHID',
  '$space_name',
  '$space_desc',
  '$view_policy',
  '$edit_policy',
  $is_default,
  0,
  $TIMESTAMP,
  $TIMESTAMP
);
EOF

      echo "Space '$space_name' created!"
    else
      echo "Space '$space_name' already exists, skipping..."
    fi
  done

  echo "Default Spaces created!"
}

# If script is run directly (not sourced), execute setup
if [ "${BASH_SOURCE[0]}" -ef "$0" ]; then
  create_spaces
fi
