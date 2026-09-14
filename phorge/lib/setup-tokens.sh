#!/bin/bash
# API token setup for Phorge

# Source common functions and variables
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${LIB_DIR}/common.sh"

create_api_token() {
  echo "Creating API token..."

  # USER_PHID must be set by calling script
  if [ -z "$USER_PHID" ]; then
    echo "ERROR: USER_PHID not set. Run setup-users.sh first."
    return 1
  fi

  TOKEN_COUNT=$(mysql_query phabricator_conduit "SELECT COUNT(*) FROM conduit_token WHERE objectPHID='$USER_PHID' AND token='$PHORGE_ADMIN_TOKEN'")

  if [ "$TOKEN_COUNT" -gt 0 ]; then
    echo "API token already exists, skipping..."
  else
    echo "Creating API token: $PHORGE_ADMIN_TOKEN"
    TIMESTAMP=$(get_timestamp)

    # Newer Phorge releases added a required tokenName column
    HAS_TOKEN_NAME=$(mysql_query phabricator_conduit "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='phabricator_conduit' AND TABLE_NAME='conduit_token' AND COLUMN_NAME='tokenName'")

    if [ "$HAS_TOKEN_NAME" -gt 0 ]; then
      mysql_exec phabricator_conduit <<EOF
INSERT INTO conduit_token (objectPHID, tokenType, token, tokenName, expires, dateCreated, dateModified)
VALUES ('$USER_PHID', 'cli', '$PHORGE_ADMIN_TOKEN', 'phabfive-dev', NULL, $TIMESTAMP, $TIMESTAMP);
EOF
    else
      mysql_exec phabricator_conduit <<EOF
INSERT INTO conduit_token (objectPHID, tokenType, token, expires, dateCreated, dateModified)
VALUES ('$USER_PHID', 'cli', '$PHORGE_ADMIN_TOKEN', NULL, $TIMESTAMP, $TIMESTAMP);
EOF
    fi

    echo "API token created successfully!"
  fi
}

# If script is run directly (not sourced), execute setup
if [ "${BASH_SOURCE[0]}" -ef "$0" ]; then
  create_api_token
fi
