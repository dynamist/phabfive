#!/bin/bash
# Spaces setup for Phorge
#
# Spaces has no Conduit API, so these go in as rows the way workboard
# columns do in setup-projects.sh.
#
# The S numbers are deliberately not consecutive. Spaces are discovered by
# probing S1, S2, S3... and phid.lookup omits a Space the viewer cannot see
# exactly as though it were absent, so visible numbers are sparse in
# practice. Seeding a gap means a dev instance reproduces that rather than
# the tidy case. The gap below is wider than five, which is what an earlier
# implementation gave up after.

# Source common functions and variables
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${LIB_DIR}/common.sh"

create_spaces() {
  echo ""
  echo "Creating default spaces..."

  for space_data in "${DEFAULT_SPACES[@]}"; do
    IFS=':' read -r space_id space_name is_default <<< "$space_data"

    # The id column is the S number, so it is set rather than generated
    SPACE_COUNT=$(mysql_query phabricator_spaces "SELECT COUNT(*) FROM spaces_namespace WHERE id=$space_id")

    if [ "$SPACE_COUNT" -ne 0 ]; then
      echo "Space S$space_id already exists, skipping..."
      continue
    fi

    SPACE_PHID=$(generate_phid "SPCE")
    TIMESTAMP=$(get_timestamp)

    # isDefaultNamespace is a unique column: one row holds 1, the rest NULL
    if [ "$is_default" = "default" ]; then
      DEFAULT_VALUE="1"
    else
      DEFAULT_VALUE="NULL"
    fi

    echo "Creating space S$space_id '$space_name' with PHID: $SPACE_PHID"

    mysql_exec phabricator_spaces <<EOF
INSERT INTO spaces_namespace (
  id,
  phid,
  namespaceName,
  viewPolicy,
  editPolicy,
  isDefaultNamespace,
  dateCreated,
  dateModified,
  description,
  isArchived
) VALUES (
  $space_id,
  '$SPACE_PHID',
  '$space_name',
  'users',
  'admin',
  $DEFAULT_VALUE,
  $TIMESTAMP,
  $TIMESTAMP,
  '',
  0
);
EOF
  done

  echo "Default spaces created!"
}
