#!/bin/bash
# User setup for Phorge (admin + fake users)

# Source common functions and variables
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${LIB_DIR}/common.sh"

create_admin_user() {
  echo "Step 1: Creating admin user..."

  USER_COUNT=$(mysql_query phabricator_user "SELECT COUNT(*) FROM user WHERE userName='$PHORGE_ADMIN_USER'")

  if [ "$USER_COUNT" -eq 0 ]; then
    USER_PHID=$(generate_phid "USER")
    TIMESTAMP=$(get_timestamp)

    echo "Creating admin user with PHID: $USER_PHID"

    # Generate password hash for default password (user should change this)
    # Using empty password initially - user will set it on first login
    PASSWORD_HASH='$2y$10$EmptyPasswordHashPlaceholderForInitialSetup'

    mysql_exec phabricator_user <<EOF
INSERT INTO user (
  phid,
  userName,
  realName,
  isApproved,
  isAdmin,
  isSystemAgent,
  isMailingList,
  isDisabled,
  accountSecret,
  dateCreated,
  dateModified,
  conduitCertificate,
  isEmailVerified,
  isEnrolledInMultiFactor
) VALUES (
  '$USER_PHID',
  '$PHORGE_ADMIN_USER',
  '$PHORGE_ADMIN_NAME',
  1,
  1,
  0,
  0,
  0,
  '',
  $TIMESTAMP,
  $TIMESTAMP,
  '',
  1,
  0
);
EOF

    echo "Admin user '$PHORGE_ADMIN_USER' created with PHID: $USER_PHID"
  else
    echo "Admin user '$PHORGE_ADMIN_USER' already exists, skipping..."
    USER_PHID=$(mysql_query phabricator_user "SELECT phid FROM user WHERE userName='$PHORGE_ADMIN_USER'")
    echo "Using existing user PHID: $USER_PHID"

    # Ensure user is admin
    mysql_exec phabricator_user <<EOF
UPDATE user SET isAdmin=1, isApproved=1, isEmailVerified=1 WHERE userName='$PHORGE_ADMIN_USER';
EOF
    echo "Admin privileges confirmed for '$PHORGE_ADMIN_USER'"
  fi

  # Export for use in other scripts
  export USER_PHID
}

create_admin_email() {
  echo "Step 2: Creating and verifying admin email..."

  # USER_PHID must be set by create_admin_user
  if [ -z "$USER_PHID" ]; then
    echo "ERROR: USER_PHID not set. Run create_admin_user first."
    return 1
  fi

  EMAIL_COUNT=$(mysql_query phabricator_user "SELECT COUNT(*) FROM user_email WHERE address='$PHORGE_ADMIN_EMAIL'")

  if [ "$EMAIL_COUNT" -eq 0 ]; then
    EMAIL_PHID=$(generate_phid "EMAIL")
    TIMESTAMP=$(get_timestamp)
    VERIFICATION_CODE=$(openssl rand -hex 12)

    echo "Creating verified email address: $PHORGE_ADMIN_EMAIL"

    mysql_exec phabricator_user <<EOF
INSERT INTO user_email (
  phid,
  userPHID,
  address,
  isVerified,
  isPrimary,
  verificationCode,
  dateCreated,
  dateModified
) VALUES (
  '$EMAIL_PHID',
  '$USER_PHID',
  '$PHORGE_ADMIN_EMAIL',
  1,
  1,
  '$VERIFICATION_CODE',
  $TIMESTAMP,
  $TIMESTAMP
);
EOF

    echo "Email '$PHORGE_ADMIN_EMAIL' created and verified!"
  else
    echo "Email address '$PHORGE_ADMIN_EMAIL' already exists, skipping..."

    # Ensure email is verified and primary
    mysql_exec phabricator_user <<EOF
UPDATE user_email SET isVerified=1, isPrimary=1 WHERE address='$PHORGE_ADMIN_EMAIL';
EOF
    echo "Email verification confirmed for '$PHORGE_ADMIN_EMAIL'"
  fi
}

create_fake_users() {
  echo ""
  echo "Step 3: Creating additional fake users for testing..."

  # AUTH_PHID needed for password authentication
  if [ -z "$AUTH_PHID" ]; then
    AUTH_PHID=$(mysql_query phabricator_auth "SELECT phid FROM auth_providerconfig WHERE providerClass='PhabricatorPasswordAuthProvider' LIMIT 1")
  fi

  for user_data in "${FAKE_USERS[@]}"; do
    IFS=':' read -r username email realname <<< "$user_data"

    USER_COUNT=$(mysql_query phabricator_user "SELECT COUNT(*) FROM user WHERE userName='$username'")

    if [ "$USER_COUNT" -eq 0 ]; then
      local FAKE_USER_PHID=$(generate_phid "USER")
      TIMESTAMP=$(get_timestamp)

      echo "Creating user '$username' with PHID: $FAKE_USER_PHID"

      # Create user
      mysql_exec phabricator_user <<EOF
INSERT INTO user (
  phid,
  userName,
  realName,
  isApproved,
  isAdmin,
  isSystemAgent,
  isMailingList,
  isDisabled,
  accountSecret,
  dateCreated,
  dateModified,
  conduitCertificate,
  isEmailVerified,
  isEnrolledInMultiFactor
) VALUES (
  '$FAKE_USER_PHID',
  '$username',
  '$realname',
  1,
  0,
  0,
  0,
  0,
  '',
  $TIMESTAMP,
  $TIMESTAMP,
  '',
  1,
  0
);
EOF

      # Create and verify email
      EMAIL_PHID=$(generate_phid "EMAIL")
      VERIFICATION_CODE=$(openssl rand -hex 12)

      mysql_exec phabricator_user <<EOF
INSERT INTO user_email (
  phid,
  userPHID,
  address,
  isVerified,
  isPrimary,
  verificationCode,
  dateCreated,
  dateModified
) VALUES (
  '$EMAIL_PHID',
  '$FAKE_USER_PHID',
  '$email',
  1,
  1,
  '$VERIFICATION_CODE',
  $TIMESTAMP,
  $TIMESTAMP
);
EOF

      # Create password authentication link
      EXTERNAL_PHID=$(generate_phid "XACT")

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
  '$FAKE_USER_PHID',
  'password',
  'self',
  '',
  '$username',
  '$realname',
  $TIMESTAMP,
  $TIMESTAMP,
  '$username',
  '$realname',
  '$email',
  1,
  '',
  '',
  '{}',
  '$AUTH_PHID'
);
EOF

      echo "User '$username' created successfully!"
    else
      echo "User '$username' already exists, skipping..."
    fi
  done

  echo "Fake users created!"
}

# If script is run directly (not sourced), execute all setup steps
if [ "${BASH_SOURCE[0]}" -ef "$0" ]; then
  create_admin_user
  create_admin_email
  create_fake_users
fi
