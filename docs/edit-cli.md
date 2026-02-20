# Edit CLI

The Edit CLI provides a unified interface for editing Phabricator/Phorge objects with intelligent auto-detection and batch processing capabilities.

## Overview

The `phabfive edit` command automatically detects object types from their monograms (T for tasks, K for passphrases, P for pastes) and routes to the appropriate editor. It supports both single object editing and batch operations through piped YAML input.

**Current Support:**
- ✅ **Maniphest Tasks (T)** - Full editing support
- 🔜 **Passphrases (K)** - Planned for future release
- 🔜 **Pastes (P)** - Planned for future release

**Key Features:**
- **Auto-detection**: Automatically detects object type from monogram (T123, K456, P789)
- **Piped input support**: Seamlessly process output from `search` and `show` commands
- **Batch operations**: Edit multiple objects atomically with validation
- **Smart board/column handling**: Auto-detect board context or specify explicitly
- **Directional navigation**: Move priority/columns up/down without knowing exact values
- **Change detection**: Only applies changes that differ from current state

## Quick Start

```bash
# Edit a single task
phabfive edit T123 --priority=high --status=resolved

# Pipe search results to edit multiple tasks
phabfive maniphest search --assigned=@me | phabfive edit --status=resolved

# Navigate columns directionally
phabfive edit T123 --tag="Sprint" --column=forward

# Raise priority (with smart Triage skip)
phabfive edit T123 --priority=raise
```

## Command Syntax

```bash
phabfive edit [<object_id>] [options]

Options:
  --priority=PRIORITY       Set priority (unbreak|high|normal|low|wish|raise|lower)
  --status=STATUS           Set status (open|resolved|wontfix|invalid|duplicate)
  --tag=BOARD               Specify board context for --column (also adds task to board)
  --column=COLUMN           Set column on board (or use forward/backward)
  --assign=USER             Set assignee
  --comment=TEXT            Add comment with changes
  --dry-run                 Show changes without applying
  --format=FORMAT           Output format (auto|strict|rich) [default: auto]
```

## Input Modes

The edit command automatically detects how you're providing input:

### Single Object Mode

Provide an object monogram directly:

```bash
# Edit task T123
phabfive edit T123 --priority=high

# Edit from URL (extracts T123)
phabfive edit "https://phorge.example.com/T123" --status=resolved
```

### Batch Mode (Piped Input)

Pipe YAML output from `search` or `show` commands:

```bash
# Edit all tasks assigned to you
phabfive maniphest search --assigned=@me | phabfive edit --priority=lower

# Edit tasks from complex search
phabfive maniphest search --tag="Backend" --status=open | \
  phabfive edit --column=Done --status=resolved --comment="Batch resolved"
```

The command automatically detects piped input using stdin detection - no flags needed!

## Task Editing

### Priority Management

#### Set Explicit Priority

```bash
phabfive edit T123 --priority=high
phabfive edit T123 --priority=unbreak
```

**Available priorities:** `unbreak`, `high`, `triage`, `normal`, `low`, `wish`

#### Directional Priority Navigation

Navigate priority ladder without knowing exact values:

```bash
# Raise priority one level
phabfive edit T123 --priority=raise

# Lower priority one level
phabfive edit T123 --priority=lower
```

**Priority Ladder:**
```
Wish (0) → Low (25) → Normal (50) → High (80) → Unbreak (100)
                                         ↑
                                    Triage (90) [skipped during raise/lower]
```

**Special Triage Handling:**

The Triage priority (90) is automatically **skipped** during directional navigation:

- **Raise from High → Unbreak** (skips Triage)
- **Lower from Unbreak → High** (skips Triage)
- **Raise from Triage → Unbreak** (special case)
- **Lower from Triage → High** (special case)

To explicitly set Triage, use: `--priority=triage`

**Examples:**

```bash
# Task at Normal priority
phabfive edit T123 --priority=raise    # → High
phabfive edit T123 --priority=raise    # → Unbreak (skips Triage!)

# Task at Unbreak priority
phabfive edit T123 --priority=lower    # → High (skips Triage!)

# Task at Triage priority (from manual set)
phabfive edit T123 --priority=raise    # → Unbreak
phabfive edit T123 --priority=lower    # → High
```

### Status Management

Set task status:

```bash
phabfive edit T123 --status=resolved
phabfive edit T123 --status=wontfix
phabfive edit T123 --status=open
```

**Common statuses:** `open`, `resolved`, `wontfix`, `invalid`, `duplicate`

### Workboard Column Management

#### Board/Column Context

When moving tasks between columns, you can specify the board explicitly or let phabfive auto-detect:

**Auto-detection (task on single board):**
```bash
# Automatically detects which board
phabfive edit T123 --column=Done
```

**Explicit board specification:**
```bash
# Specify board when task is on multiple boards
phabfive edit T123 --tag="Sprint 42" --column=Done

# Add task to new board and set column
phabfive edit T123 --tag="Backend Team" --column=Backlog
```

#### Set Column by Name

Move to a specific column:

```bash
phabfive edit T123 --tag="Sprint" --column="In Progress"
phabfive edit T123 --tag="Backend" --column=Done
```

Column names are **case-insensitive**.

#### Directional Column Navigation

Navigate columns by sequence without knowing column names:

```bash
# Move forward one column
phabfive edit T123 --tag="Sprint" --column=forward

# Move backward one column
phabfive edit T123 --tag="Sprint" --column=backward
```

**Column Navigation Behavior:**
- Columns are ordered by their `sequence` field
- `forward` moves to the next column in sequence
- `backward` moves to the previous column in sequence
- At boundaries (first/last column), the task stays in place

**Example workflow:**
```
Backlog (seq: 0) → In Progress (seq: 1) → Review (seq: 2) → Done (seq: 3)

# Task in "In Progress"
phabfive edit T123 --column=forward    # → Review
phabfive edit T123 --column=forward    # → Done
phabfive edit T123 --column=forward    # → Done (stays at end)
phabfive edit T123 --column=backward   # → Review
```

### Assignment Management

Assign tasks to users:

```bash
# Assign to specific user
phabfive edit T123 --assign=alice

# Assign to yourself
phabfive edit T123 --assign=@me
```

### Adding Comments

Add a comment along with your changes:

```bash
phabfive edit T123 --status=resolved --comment="Fixed in commit abc123"
```

## Batch Operations

### Basic Batch Editing

Edit multiple tasks by piping search results:

```bash
# Resolve all tasks assigned to you
phabfive maniphest search --assigned=@me | \
  phabfive edit --status=resolved --comment="Completed"

# Lower priority for all tasks in a project
phabfive maniphest search --tag="Backlog" | \
  phabfive edit --priority=lower
```

### Atomic Validation

Batch operations use **atomic validation**: ALL tasks are validated before ANY are modified.

If any task fails validation, the entire batch fails with detailed error messages:

```
ERROR: Validation failed for 2 task(s):
  - T123: Task T123 is on multiple boards [Backend, Sprint 42]. Use --tag=BOARD to specify which board.
  - T124: Task T124 is on multiple boards [Backend, Sprint 42]. Use --tag=BOARD to specify which board.

No tasks were modified (atomic batch failure).
```

### Handling Multiple Boards

When tasks are on multiple boards and you're using `--column`, you must specify which board:

```bash
# This will fail if tasks are on multiple boards
phabfive maniphest search --assigned=@me | phabfive edit --column=Done

# Specify the board
phabfive maniphest search --assigned=@me | \
  phabfive edit --tag="Sprint 42" --column=Done
```

### Partition Suggestions

When batch operations fail due to tasks on different boards, phabfive provides **partition suggestions** to help you split the work:

```
ERROR: Validation failed for 3 task(s):
  - T123: Task T123 is on multiple boards [Backend Team, Sprint 42]. Use --tag=BOARD to specify which board.
  - T124: Task T124 is on multiple boards [Backend Team, Sprint 42]. Use --tag=BOARD to specify which board.
  - T125: Task T125 is on multiple boards [Backend Team, Frontend Team]. Use --tag=BOARD to specify which board.

Suggested partition commands:

# Tasks on Backend Team + Sprint 42:
echo "T123\nT124" | phabfive edit --tag="Backend Team" --column=Done

# Task on Backend Team + Frontend Team:
echo "T125" | phabfive edit --tag="Backend Team" --column=Done

No tasks were modified (atomic batch failure).
```

## Advanced Workflows

### Complex Multi-Field Updates

Combine multiple field updates in one operation:

```bash
phabfive edit T123 \
  --priority=high \
  --status=open \
  --tag="Sprint 42" \
  --column="In Progress" \
  --assign=alice \
  --comment="Moving to current sprint"
```

### Pipeline with Search Filters

Use powerful search filters and pipe to edit:

```bash
# Resolve all tasks created before a date
phabfive maniphest search --created-before="2024-01-01" --status=open | \
  phabfive edit --status=wontfix --comment="Closed old tasks"

# Move tasks updated in last week to Done
phabfive maniphest search --updated-after=7 --tag="Sprint" | \
  phabfive edit --column=Done
```

### Dry Run Mode

Preview changes without applying them:

```bash
phabfive edit T123 --priority=high --status=resolved --dry-run
```

Useful for:
- Testing batch operations before applying
- Verifying change detection works correctly
- Debugging complex workflows

### Change Detection

The edit command automatically detects which fields have changed and only applies necessary transactions:

```bash
# Task T123 already has priority=high
phabfive edit T123 --priority=high --status=resolved
# Only applies status change (priority unchanged)
```

Benefits:
- Cleaner audit trails (no redundant transactions)
- Faster API calls
- Better performance for batch operations

## Examples by Use Case

### Sprint Management

```bash
# Move all completed tasks to Done
phabfive maniphest search --tag="Sprint 42" --status=resolved | \
  phabfive edit --column=Done

# Triage new tasks
phabfive maniphest search --tag="Sprint 42" --column="Backlog" | \
  phabfive edit --priority=triage --column="Triage"

# Bump priority for P1 tasks
phabfive maniphest search --tag="Sprint 42" --priority=high | \
  phabfive edit --priority=raise
```

### Bulk Task Management

```bash
# Reassign tasks from leaving team member
phabfive maniphest search --assigned=bob | \
  phabfive edit --assign=alice --comment="Reassigned during transition"

# Close stale tasks
phabfive maniphest search --updated-before=90 --status=open | \
  phabfive edit --status=wontfix --comment="Closed due to inactivity"

# Add all backend tasks to new board
phabfive maniphest search --tag="Backend" | \
  phabfive edit --tag="Q1 Planning" --column=Backlog
```

### Personal Workflow

```bash
# Mark your tasks as done
phabfive maniphest search --assigned=@me --status=resolved | \
  phabfive edit --column=Done --comment="Completed"

# Deprioritize all your low-priority tasks
phabfive maniphest search --assigned=@me --priority=normal | \
  phabfive edit --priority=lower

# Move your in-progress tasks forward
phabfive maniphest search --assigned=@me --column="In Progress" | \
  phabfive edit --column=forward
```

## Board/Column Validation Rules

Understanding when board context is required:

| Scenario | Board Context | Behavior |
|----------|---------------|----------|
| `--column` only, task on single board | ✅ Auto-detected | Moves to column |
| `--column` only, task on multiple boards | ❌ Required | Error with suggestions |
| `--tag` + `--column`, task on specified board | ✅ Explicit | Moves to column |
| `--tag` + `--column`, task NOT on board | ✅ Explicit | Adds to board + sets column |
| No `--column` specified | N/A | No validation needed |

## Output Formats

Control output format for piping and display:

```bash
# Auto-detect (rich for terminal, strict for pipes)
phabfive edit T123 --priority=high

# Force strict YAML for piping
phabfive edit T123 --priority=high --format=strict

# Force rich formatting
phabfive edit T123 --priority=high --format=rich
```

**Format auto-detection:**
- Terminal (TTY): Rich formatting with colors
- Piped/redirected: Strict YAML for machine parsing

## Error Handling

### Validation Errors

The edit command validates inputs before making API calls:

```bash
# Invalid priority
phabfive edit T123 --priority=invalid
# ERROR: Invalid priority: invalid

# Invalid status
phabfive edit T123 --status=invalid
# ERROR: Invalid status: invalid

# Column without board context (multiple boards)
phabfive edit T123 --column=Done
# ERROR: Task T123 is on multiple boards [...]. Use --tag=BOARD to specify which board.
```

### API Errors

API errors are reported clearly:

```bash
# Task doesn't exist
phabfive edit T99999 --priority=high
# ERROR: Task not found

# Permission denied
phabfive edit T123 --assign=alice
# ERROR: You don't have permission to edit this task
```

### Batch Operation Failures

Batch operations fail atomically with clear error messages and partition suggestions when applicable.

## Tips and Best Practices

### 1. Always Preview Batch Operations

Use `--dry-run` to preview changes before applying:

```bash
phabfive maniphest search --tag="Cleanup" | \
  phabfive edit --status=wontfix --dry-run
```

### 2. Use Descriptive Comments

Add context to bulk changes:

```bash
phabfive maniphest search --tag="Migration" | \
  phabfive edit --status=resolved --comment="Migration completed in release 2.0"
```

### 3. Leverage Auto-Detection

Let phabfive auto-detect board context when possible:

```bash
# Good (auto-detects board)
phabfive edit T123 --column=Done

# Unnecessary (if task on single board)
phabfive edit T123 --tag="MyBoard" --column=Done
```

### 4. Use Directional Navigation

Prefer directional navigation for workflows:

```bash
# Good (works regardless of current priority)
phabfive edit T123 --priority=raise

# Less flexible (requires knowing current state)
phabfive edit T123 --priority=high
```

### 5. Combine with Search Templates

Use search templates for complex recurring batch operations:

```bash
phabfive maniphest search --with templates/weekly-sprint-tasks.yaml | \
  phabfive edit --column=Done --status=resolved
```

## Troubleshooting

### Task on Multiple Boards

**Problem:** Error when using `--column` without `--tag`

**Solution:** Specify board with `--tag`:
```bash
phabfive edit T123 --tag="Sprint 42" --column=Done
```

### Column Not Found

**Problem:** Error "Column 'XYZ' not found on board"

**Solution:** Check column names are spelled correctly (case-insensitive):
```bash
phabfive maniphest show T123 --all
# Look at "Boards" section for column names
```

### Batch Operation Fails for Some Tasks

**Problem:** Some tasks in batch have different board configurations

**Solution:** Use partition suggestions from error message to split the batch:
```bash
# Original (fails)
echo "T123\nT124\nT125" | phabfive edit --column=Done

# Split by board (from suggestions)
echo "T123\nT124" | phabfive edit --tag="Backend" --column=Done
echo "T125" | phabfive edit --tag="Frontend" --column=Done
```

### Priority Not Changing

**Problem:** Priority appears unchanged after edit

**Solution:** Check change detection - priority may already be at target:
```bash
# Check current priority
phabfive maniphest show T123

# Verify with --dry-run
phabfive edit T123 --priority=high --dry-run
```

## See Also

- [Maniphest CLI](maniphest-cli.md) - Complete task management guide
- [Search Templates](search-templates.md) - Reusable search queries
- [Create Templates](create-templates.md) - Bulk task creation

## Future Support

Planned object types for future releases:

- **Passphrases (K)** - Edit secrets and credentials
- **Pastes (P)** - Edit code pastes

The architecture supports easy extension to new object types through the monogram detection system.
