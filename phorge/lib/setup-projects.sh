#!/bin/bash
# Project and workboard setup for Phorge

# Source common functions and variables
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${LIB_DIR}/common.sh"

create_projects() {
  echo ""
  echo "Creating default projects/workboards..."

  # Get admin user PHID for project creation
  ADMIN_PHID=$(mysql_query phabricator_user "SELECT phid FROM user WHERE userName='$ADMIN_USERNAME'")

  if [ -z "$ADMIN_PHID" ]; then
    echo "ERROR: Admin user not found. Run setup-users.sh first."
    return 1
  fi

  for project_data in "${DEFAULT_PROJECTS[@]}"; do
    IFS=':' read -r project_name project_desc <<< "$project_data"

    # Check if project exists by name
    PROJECT_COUNT=$(mysql_query phabricator_project "SELECT COUNT(*) FROM project WHERE name='$project_name'")

    if [ "$PROJECT_COUNT" -eq 0 ]; then
      PROJECT_PHID=$(generate_phid "PROJ")
      TIMESTAMP=$(get_timestamp)

      echo "Creating project '$project_name' with PHID: $PROJECT_PHID"

      # Create project
      mysql_exec phabricator_project <<EOF
INSERT INTO project (
  phid,
  name,
  authorPHID,
  dateCreated,
  dateModified,
  status,
  primarySlug,
  icon,
  color,
  mailKey,
  subtype,
  hasWorkboard,
  hasMilestones,
  hasSubprojects,
  milestoneNumber,
  projectPath,
  projectDepth,
  projectPathKey,
  viewPolicy,
  editPolicy,
  joinPolicy,
  isMembershipLocked,
  profileImagePHID,
  spacePHID,
  properties
) VALUES (
  '$PROJECT_PHID',
  '$project_name',
  '$ADMIN_PHID',
  $TIMESTAMP,
  $TIMESTAMP,
  0,
  '$(echo "$project_name" | tr '[:upper:]' '[:lower:]')',
  'project',
  'blue',
  '$(openssl rand -hex 10)',
  'default',
  0,
  0,
  0,
  NULL,
  '$(openssl rand -hex 16)/',
  0,
  '$(openssl rand -hex 2)',
  'public',
  'users',
  'users',
  0,
  NULL,
  NULL,
  '{\"workboard.sort.default\":\"priority\"}'
);
EOF

      # Create project slug for URL routing (/tag/client/, etc.)
      PROJECT_SLUG=$(echo "$project_name" | tr '[:upper:]' '[:lower:]')
      mysql_exec phabricator_project <<EOF
INSERT INTO project_slug (projectPHID, slug, dateCreated, dateModified)
VALUES ('$PROJECT_PHID', '$PROJECT_SLUG', $TIMESTAMP, $TIMESTAMP);
EOF

      # Get the project ID for datasource tokens
      PROJECT_ID=$(mysql_query phabricator_project "SELECT id FROM project WHERE phid='$PROJECT_PHID'")

      # Create datasource tokens for project search/typeahead
      # Tokenize project name (split by non-alphanumeric chars)
      TOKENS=$(echo "$project_name $PROJECT_SLUG" | tr '[:upper:]' '[:lower:]' | tr -s '[:space:][:punct:]' '\n' | sort -u)

      mysql_exec phabricator_project <<EOF
DELETE FROM project_datasourcetoken WHERE projectID=$PROJECT_ID;
EOF

      for token in $TOKENS; do
        if [ ! -z "$token" ]; then
          mysql_exec phabricator_project <<EOF
INSERT INTO project_datasourcetoken (projectID, token) VALUES ($PROJECT_ID, '$token');
EOF
        fi
      done

      # Add admin as project member
      # Edge type 13 = project has member
      # Edge type 14 = user is member of project
      # Edge type 60 = project has watcher (required for membership to work)
      mysql_exec phabricator_project <<EOF
INSERT INTO edge (src, type, dst, dateCreated, seq, dataID)
VALUES ('$PROJECT_PHID', 13, '$ADMIN_PHID', $TIMESTAMP, 0, NULL);

INSERT INTO edge (src, type, dst, dateCreated, seq, dataID)
VALUES ('$PROJECT_PHID', 60, '$ADMIN_PHID', $TIMESTAMP, 0, NULL);

INSERT INTO edge (src, type, dst, dateCreated, seq, dataID)
VALUES ('$ADMIN_PHID', 14, '$PROJECT_PHID', $TIMESTAMP, 0, NULL);
EOF

      # Create default workboard columns
      create_workboard_columns "$PROJECT_PHID" "$TIMESTAMP"

      # Now enable workboard after all columns are created (prevents auto-creation of default column)
      mysql_exec phabricator_project <<EOF
UPDATE project SET hasWorkboard=1 WHERE phid='$PROJECT_PHID';
EOF

      # Create menu configuration to set workboard as default view
      create_workboard_menu "$PROJECT_PHID" "$TIMESTAMP"

      echo "Project '$project_name' created with workboard columns!"
    else
      echo "Project '$project_name' already exists, skipping..."
    fi
  done

  echo "Default projects created!"
}

create_workboard_columns() {
  local PROJECT_PHID=$1
  local TIMESTAMP=$2

  # Column 0: Default (unnamed column with isDefault flag)
  COLUMN0_PHID=$(generate_phid "PCOL")
  mysql_exec phabricator_project <<EOF
INSERT INTO project_column (
  phid,
  projectPHID,
  sequence,
  name,
  status,
  properties,
  dateCreated,
  dateModified,
  proxyPHID
) VALUES (
  '$COLUMN0_PHID',
  '$PROJECT_PHID',
  0,
  '',
  0,
  '{\"isDefault\":true}',
  $TIMESTAMP,
  $TIMESTAMP,
  NULL
);
EOF

  # Column 1: Up Next
  COLUMN1_PHID=$(generate_phid "PCOL")
  mysql_exec phabricator_project <<EOF
INSERT INTO project_column (
  phid,
  projectPHID,
  sequence,
  name,
  status,
  properties,
  dateCreated,
  dateModified,
  proxyPHID
) VALUES (
  '$COLUMN1_PHID',
  '$PROJECT_PHID',
  1,
  'Up Next',
  0,
  '[]',
  $TIMESTAMP,
  $TIMESTAMP,
  NULL
);
EOF

  # Column 2: In Progress
  COLUMN2_PHID=$(generate_phid "PCOL")
  mysql_exec phabricator_project <<EOF
INSERT INTO project_column (
  phid,
  projectPHID,
  sequence,
  name,
  status,
  properties,
  dateCreated,
  dateModified,
  proxyPHID
) VALUES (
  '$COLUMN2_PHID',
  '$PROJECT_PHID',
  2,
  'In Progress',
  0,
  '[]',
  $TIMESTAMP,
  $TIMESTAMP,
  NULL
);
EOF

  # Column 3: In Review
  COLUMN3_PHID=$(generate_phid "PCOL")
  mysql_exec phabricator_project <<EOF
INSERT INTO project_column (
  phid,
  projectPHID,
  sequence,
  name,
  status,
  properties,
  dateCreated,
  dateModified,
  proxyPHID
) VALUES (
  '$COLUMN3_PHID',
  '$PROJECT_PHID',
  3,
  'In Review',
  0,
  '[]',
  $TIMESTAMP,
  $TIMESTAMP,
  NULL
);
EOF

  # Column 4: Done
  COLUMN4_PHID=$(generate_phid "PCOL")
  mysql_exec phabricator_project <<EOF
INSERT INTO project_column (
  phid,
  projectPHID,
  sequence,
  name,
  status,
  properties,
  dateCreated,
  dateModified,
  proxyPHID
) VALUES (
  '$COLUMN4_PHID',
  '$PROJECT_PHID',
  4,
  'Done',
  0,
  '[]',
  $TIMESTAMP,
  $TIMESTAMP,
  NULL
);
EOF
}

create_workboard_menu() {
  local PROJECT_PHID=$1
  local TIMESTAMP=$2

  # Create menu configuration to set workboard as default view
  # When accessing /tag/client/, it will go directly to the workboard
  # Note: Only create the workboard menu item with builtinKey set - Phorge auto-generates the rest
  MENU_PHID_WORKBOARD=$(generate_phid "PANL")

  mysql_exec phabricator_search <<EOF
INSERT INTO search_profilepanelconfiguration (
  phid, profilePHID, menuItemKey, builtinKey, menuItemOrder, visibility,
  menuItemProperties, dateCreated, dateModified, customPHID
) VALUES
  ('$MENU_PHID_WORKBOARD', '$PROJECT_PHID', 'project.workboard', 'project.workboard', 0, 'default', '[]', $TIMESTAMP, $TIMESTAMP, NULL);
EOF
}

# If script is run directly (not sourced), execute setup
if [ "${BASH_SOURCE[0]}" -ef "$0" ]; then
  create_projects
fi
