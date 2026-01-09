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
  '$PHORGE_ADMIN_USER',
  '$PHORGE_ADMIN_NAME',
  $TIMESTAMP,
  $TIMESTAMP,
  '$PHORGE_ADMIN_USER',
  '$PHORGE_ADMIN_NAME',
  '$PHORGE_ADMIN_EMAIL',
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

set_user_password() {
  local username="$1"
  local password="$2"

  if [ -z "$password" ]; then
    return 0
  fi

  echo "Setting password for user: $username"

  # Get user PHID
  local user_phid=$(mysql_query phabricator_user "SELECT phid FROM user WHERE userName='$username'")
  if [ -z "$user_phid" ] || [ "$user_phid" = "0" ]; then
    echo "ERROR: User '$username' not found"
    return 1
  fi

  # Check if password already exists for this user
  local pw_count=$(mysql_query phabricator_auth "SELECT COUNT(*) FROM auth_password WHERE objectPHID='$user_phid' AND passwordType='account'")
  if [ "$pw_count" -gt 0 ]; then
    echo "Password already set for $username, skipping..."
    return 0
  fi

  # Generate password salt (64 random alphanumeric chars), hash, and PHID
  local salt=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 64)
  local pw_phid=$(generate_phid "APHP")
  local timestamp=$(get_timestamp)

  # Phorge: HMAC-SHA256(password, salt) -> hex string -> bcrypt
  # Hash format is "bcrypt:$2y$..."
  local hash=$(php -r '
    $password = $argv[1];
    $salt = $argv[2];
    $hmac = hash_hmac("sha256", $password, $salt, false);
    $bcrypt = password_hash($hmac, PASSWORD_BCRYPT, ["cost" => 11]);
    echo "bcrypt:" . $bcrypt;
  ' -- "$password" "$salt")

  # Insert into auth_password table (pipe to avoid $ interpretation in bcrypt hash)
  printf "INSERT INTO auth_password (phid, objectPHID, passwordType, passwordHash, passwordSalt, isRevoked, dateCreated, dateModified) VALUES ('%s', '%s', 'account', '%s', '%s', 0, %s, %s);" \
    "$pw_phid" "$user_phid" "$hash" "$salt" "$timestamp" "$timestamp" | \
    mysql -h"$MYSQL_HOST" -P"$MYSQL_PORT" -u"$MYSQL_USER" -p"$MYSQL_PASS" phabricator_auth

  echo "Password set for $username"
}

generate_recovery_link() {
  # Skip recovery link if password was set directly
  if [ ! -z "$PHORGE_ADMIN_PASS" ]; then
    echo "Password set directly, skipping recovery link generation."
    export RECOVERY_LINK=""
    return 0
  fi

  echo "Step 3: Generating password recovery link..."
  cd "$PHORGE_PATH"

  RECOVERY_LINK=$("$PHORGE_PATH/bin/auth" recover "$PHORGE_ADMIN_USER" 2>&1 | grep -o 'http[s]*://[^[:space:]]*' || echo "")

  # Export for use in main script
  export RECOVERY_LINK
}

# If script is run directly (not sourced), execute all setup steps
if [ "${BASH_SOURCE[0]}" -ef "$0" ]; then
  setup_auth_provider
  setup_password_auth
  generate_recovery_link
fi
