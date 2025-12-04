#!/bin/bash
# Authentication setup for Phorge

# Source common functions and variables
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${LIB_DIR}/common.sh"

setup_auth_provider() {
  echo "Step 1: Enabling username/password authentication provider..."

  AUTH_PROVIDER_COUNT=$(mysql_query phabricator_auth "SELECT COUNT(*) FROM auth_providerconfig WHERE providerClass='PhabricatorPasswordAuthProvider'")

  if [ "$AUTH_PROVIDER_COUNT" -eq 0 ]; then
    AUTH_PHID=$(generate_phid "AUTH")
    TIMESTAMP=$(get_timestamp)

    echo "Creating username/password auth provider with PHID: $AUTH_PHID"

    mysql_exec phabricator_auth <<EOF
INSERT INTO auth_providerconfig (
  phid,
  providerClass,
  providerType,
  providerDomain,
  isEnabled,
  shouldAllowLogin,
  shouldAllowRegistration,
  shouldAllowLink,
  shouldAllowUnlink,
  shouldTrustEmails,
  properties,
  dateCreated,
  dateModified,
  shouldAutoLogin
) VALUES (
  '$AUTH_PHID',
  'PhabricatorPasswordAuthProvider',
  'password',
  'self',
  1,
  1,
  1,
  1,
  0,
  0,
  '{}',
  $TIMESTAMP,
  $TIMESTAMP,
  0
);
EOF

    echo "Username/password authentication provider enabled!"
  else
    echo "Username/password authentication provider already exists, skipping..."
    AUTH_PHID=$(mysql_query phabricator_auth "SELECT phid FROM auth_providerconfig WHERE providerClass='PhabricatorPasswordAuthProvider' LIMIT 1")
    echo "Using existing auth provider PHID: $AUTH_PHID"
  fi

  # Export for use in other scripts
  export AUTH_PHID
}

setup_password_auth() {
  echo "Step 2: Setting up password authentication..."

  # USER_PHID must be set by calling script
  if [ -z "$USER_PHID" ]; then
    echo "ERROR: USER_PHID not set. Run setup-users.sh first."
    return 1
  fi

  # AUTH_PHID must be set by setup_auth_provider
  if [ -z "$AUTH_PHID" ]; then
    echo "ERROR: AUTH_PHID not set. Run setup_auth_provider first."
    return 1
  fi

  AUTH_COUNT=$(mysql_query phabricator_user "SELECT COUNT(*) FROM user_externalaccount WHERE userPHID='$USER_PHID' AND accountType='password'")

  if [ "$AUTH_COUNT" -eq 0 ]; then
    EXTERNAL_PHID=$(generate_phid "XACT")
    TIMESTAMP=$(get_timestamp)

    echo "Creating password authentication account link..."

    mysql_exec phabricator_user <<EOF
INSERT INTO user_externalaccount (
  phid,
  userPHID,
  accountType,
  accountDomain,
  accountSecret,
  accountID,
  displayName,
  dateCreated,
  dateModified,
  username,
  realName,
  email,
  emailVerified,
  accountURI,
  profileImagePHID,
  properties,
  providerConfigPHID
) VALUES (
  '$EXTERNAL_PHID',
  '$USER_PHID',
  'password',
  'self',
  '',
  '$ADMIN_USERNAME',
  '$ADMIN_REALNAME',
  $TIMESTAMP,
  $TIMESTAMP,
  '$ADMIN_USERNAME',
  '$ADMIN_REALNAME',
  '$ADMIN_EMAIL',
  1,
  '',
  '',
  '{}',
  '$AUTH_PHID'
);
EOF

    echo "Password authentication account link created!"
  else
    echo "Password authentication account link already exists, skipping..."
  fi
}

generate_recovery_link() {
  echo "Step 3: Generating password recovery link..."
  cd "$PHORGE_PATH"

  RECOVERY_LINK=$("$PHORGE_PATH/bin/auth" recover "$ADMIN_USERNAME" 2>&1 | grep -o 'http[s]*://[^[:space:]]*' || echo "")

  # Export for use in main script
  export RECOVERY_LINK
}

# If script is run directly (not sourced), execute all setup steps
if [ "${BASH_SOURCE[0]}" -ef "$0" ]; then
  setup_auth_provider
  setup_password_auth
  generate_recovery_link
fi
