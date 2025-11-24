# Phorge Spaces and Task Distribution

This directory contains enhancements to the Phorge automated setup to include Spaces for access control and automatic task distribution across workboard columns.

## Features Added

### 1. Automatic Spaces Setup

Three Spaces are automatically created during container initialization:

- **Public** (default) - For customer-facing work
  - View: Public (anyone can view)
  - Edit: Users (logged-in users can edit)
  
- **Internal** - For internal team work and development
  - View: Users (logged-in users only)
  - Edit: Users (logged-in users can edit)
  
- **Restricted** - For security-sensitive and compliance work
  - View: Users (logged-in users only)
  - Edit: Admin (only admins can edit)

### 2. Space Assignments in Test Data

The `test-files/mega-2024-simulation.yml` file now includes space assignments for all tasks:
- Security and compliance tasks → **Restricted** space
- Development and infrastructure tasks → **Internal** space
- Documentation and public-facing tasks → **Public** space

This allows you to test and develop features related to Spaces and access control.

### 3. Task Distribution Script

A Python script (`phorge/lib/move-tasks-to-columns.py`) automatically distributes tasks across workboard columns to simulate realistic work in progress.

## Usage

### Quick Start (Recommended)

One command to start Phorge and populate demo data:

```bash
make phorge-setup
```

This automatically:
1. Starts Phorge with Spaces created automatically
2. Creates ~70 demo tasks with Space assignments
3. Distributes tasks across workboard columns
4. Gives you a realistic development environment

### Alternative: Manual Steps

If you prefer more control:

1. **Start Phorge only**:
   ```bash
   make phorge-up
   ```

2. **Populate demo data** (in another terminal):
   ```bash
   make phorge-populate
   ```

3. **Or run individual steps**:
   ```bash
   # Just create tasks
   uv run phabfive maniphest create test-files/mega-2024-simulation.yml
   
   # Then distribute them
   uv run python phorge/lib/move-tasks-to-columns.py
   ```

### Task Distribution Logic

The script moves tasks based on priority:

- **Backlog** (default): wish and low priority tasks
- **Up Next**: normal priority tasks  
- **In Progress**: high priority tasks
- **In Review**: unbreak priority tasks
- **Done**: Q1 tasks and some bugs (simulates completed work)

This gives you a realistic board state with ~10-15 completed tasks and tasks spread across different workflow stages.

## File Structure

```
phorge/
├── lib/
│   ├── setup-spaces.sh          # Bash script to create Spaces
│   ├── move-tasks-to-columns.py # Python script to distribute tasks
│   ├── common.sh                # Contains DEFAULT_SPACES configuration
│   ├── setup-projects.sh        # (existing) Project setup
│   └── setup-users.sh           # (existing) User setup
├── init-phorge.sh               # Main orchestrator (calls setup-spaces.sh)
├── AUTOMATED_SETUP.md           # Main documentation
└── README-SPACES.md             # This file
```

## Customization

### Adding or Modifying Spaces

Edit `phorge/lib/common.sh` and modify the `DEFAULT_SPACES` array:

```bash
export DEFAULT_SPACES=(
  "Name:Description:viewPolicy:editPolicy:isDefault"
)
```

Policy values:
- `public` - Anyone can access
- `users` - Logged-in users only
- `PHID-PLCY-admin` - Administrators only

### Customizing Task Distribution

Edit `phorge/lib/move-tasks-to-columns.py` and modify the `get_column_for_priority()` function to change the distribution logic.

## Testing Space Features

With this setup, you can now:

1. **Test Space visibility** - Log in as different users and verify they can/cannot see tasks in different Spaces
2. **Test Space permissions** - Try editing tasks in Restricted space as non-admin users
3. **Develop Space-related features** in phabfive - The test data now has realistic Space assignments
4. **Test workboard functionality** - Tasks are distributed across columns for realistic testing

## Technical Details

### Database Schema

Spaces are stored in the `phabricator_spaces.spaces_namespace` table with the following fields:
- `phid` - Unique identifier (PHID-SPCE-*)
- `namespaceName` - Display name
- `description` - Description text
- `viewPolicy` - Who can view objects in this space
- `editPolicy` - Who can edit the space configuration
- `isDefaultNamespace` - Whether this is the default space (1 or 0)
- `isArchived` - Whether the space is archived

Tasks and projects reference spaces via their `spacePHID` field.

### API Usage

The Python script uses the Phabricator Conduit API:
- `maniphest.search` - Find tasks by title
- `project.search` - Get project PHIDs
- `project.column.search` - Get column PHIDs for boards
- `maniphest.edit` - Move tasks between columns

## Troubleshooting

### Spaces not created

Check container logs:
```bash
podman logs phorge-container | grep -i space
```

### Tasks not moving

Ensure you have configured phabfive:
```bash
cat ~/.config/phabfive/phabfive.yaml
```

Run the script with verbose output:
```bash
uv run python phorge/lib/move-tasks-to-columns.py --verbose
```

### Permission errors

Verify the admin user has access to all Spaces and projects.

## References

- [Phorge Spaces Documentation](https://we.phorge.it/book/phorge/article/spaces/)
- [Main Setup Documentation](AUTOMATED_SETUP.md)
- [Phabricator Conduit API](http://phorge.domain.tld/conduit/)
