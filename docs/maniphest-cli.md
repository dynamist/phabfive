# Maniphest CLI

The Maniphest CLI provides powerful commands for managing tasks in Phabricator/Phorge from the terminal.

## Overview

Maniphest is Phabricator's task tracking application. The phabfive CLI allows you to:

- Search and filter tasks with advanced criteria
- Add comments to tasks
- View task details
- Create tasks in bulk from configuration files
- **Track and filter tasks by their workboard column transitions**
- **Track and filter tasks by their priority transitions**

## Basic Commands

### Show Task Details

Display information about a specific task:

```bash
# Basic details
phabfive maniphest show T123

# Show all fields including workboard transition history
phabfive maniphest show T123 --all

# Pretty-print all fields
phabfive maniphest show T123 --pp
```

When using `--all`, the output includes complete workboard transition history showing:
- All column movements across all workboards
- Timestamps for each transition
- Direction indicators (forward/backward)

Example output with `--all`:
```
Ticket ID:      123
phid:           PHID-TASK-abc123
...
dependsOnTaskPHIDs: []

Boards:
  My Project:
    Transitions:
      - "2024-10-10T14:30:00 [→] (new) → Backlog"
      - "2024-10-11T09:15:00 [→] Backlog → In Progress"
      - "2024-10-12T16:45:00 [→] In Progress → Done"
```

### Add Comments

Add a comment to a task:

```bash
phabfive maniphest comment add T123 "This is my comment"
```

The command will output the task URI after successfully adding the comment.

### Create Tasks from Templates

Create multiple tasks in bulk using a YAML configuration file:

```bash
phabfive maniphest create tasks.yaml

# Preview without creating
phabfive maniphest create tasks.yaml --dry-run
```

## Task Search

Search for tasks within projects with various filtering options.

### Basic Search

```bash
# Search within a specific project
phabfive maniphest search "My Project"

# Search all projects
phabfive maniphest search "*"
```

### Wildcard Project Matching

The project name supports wildcard patterns:

```bash
# All projects starting with "Backend"
phabfive maniphest search "Backend*"

# All projects ending with "2024"
phabfive maniphest search "*2024"

# All projects containing "API"
phabfive maniphest search "*API*"
```

!!! note
    Project matching is case-insensitive. If no exact match is found, phabfive will suggest similar project names.

### Date Filtering

Filter tasks by creation or modification date:

```bash
# Tasks created in the last 7 days
phabfive maniphest search "My Project" --created-after=7

# Tasks updated in the last 3 days
phabfive maniphest search "My Project" --updated-after=3

# Combine both filters
phabfive maniphest search "My Project" --created-after=30 --updated-after=7
```

## Filtering Tasks

Filter tasks based on their movement through workboard columns. This feature helps you analyze task workflows, identify bottlenecks, and track specific patterns in your development process.

### Why Use Filtering?

Common use cases include:

- **Find stuck tasks**: Tasks that moved backward from "Done" to "In Progress"
- **Track completion patterns**: Tasks that went from "In Progress" directly to "Done"
- **Identify blocked work**: Tasks currently in "Blocked" that came from "In Progress"
- **Audit workflow violations**: Tasks that never went through required columns
- **Analyze task lifecycle**: See complete transition history for debugging workflows

**Note**: History is only displayed when you use the `--show-history` flag. This works with or without filters.

### Pattern Syntax

Transition patterns use a query language with AND/OR logic:

- **Comma (`,`)** = OR logic - match any pattern
- **Plus (`+`)** = AND logic - all conditions must match

#### Pattern Types

| Pattern | Description | Example |
|---------|-------------|---------|
| `from:COLUMN` | Task moved from COLUMN | `from:Backlog` |
| `from:COLUMN:forward` | Task moved forward from COLUMN | `from:In Progress:forward` |
| `from:COLUMN:backward` | Task moved backward from COLUMN | `from:Done:backward` |
| `to:COLUMN` | Task moved to COLUMN | `to:Done` |
| `in:COLUMN` | Task is currently in COLUMN | `in:Blocked` |
| `been:COLUMN` | Task was in COLUMN at any point | `been:In Review` |
| `never:COLUMN` | Task was never in COLUMN | `never:Blocked` |
| `backward` | Task had any backward movement | `backward` |
| `forward` | Task had any forward movement | `forward` |
| `not:PATTERN` | Negates any pattern above | `not:in:Done`, `not:backward` |

**Negation Prefix `not:`**: Any pattern can be prefixed with `not:` to negate its meaning. This is a general negation operator that works with all pattern types. For example:
- `not:in:Done` - Tasks NOT currently in Done
- `not:from:Backlog` - Tasks that didn't move from Backlog
- `not:backward` - Tasks that haven't moved backward
- `not:been:Blocked` - Tasks that were never in Blocked (equivalent to `never:Blocked`)

### Basic Examples

```bash
# Find all tasks that moved backward (returned to earlier columns)
phabfive maniphest search "My Project" --column=backward

# Find tasks currently in the "Blocked" column
phabfive maniphest search "My Project" --column="in:Blocked"

# Find tasks that moved to "Done"
phabfive maniphest search "My Project" --column="to:Done"

# Find tasks that moved forward from "In Progress"
phabfive maniphest search "My Project" --column="from:In Progress:forward"
```

### OR Logic (Comma Separator)

Match tasks that satisfy **any** of the patterns:

```bash
# Tasks that are EITHER in Done OR in Blocked
phabfive maniphest search "My Project" --column="in:Done,in:Blocked"

# Tasks that moved to Done OR moved backward
phabfive maniphest search "My Project" --column="to:Done,backward"

# Tasks in multiple columns
phabfive maniphest search "My Project" --column="in:In Progress,in:In Review,in:Testing"
```

### AND Logic (Plus Separator)

Match tasks that satisfy **all** conditions:

```bash
# Tasks that moved from "In Progress" AND are currently in "Done"
phabfive maniphest search "My Project" --column="from:In Progress+in:Done"

# Tasks that moved from "Up Next" forward AND never got blocked
phabfive maniphest search "My Project" --column="from:Up Next:forward+never:Blocked"

# Tasks currently in Done AND moved there from In Progress (skipped review)
phabfive maniphest search "My Project" --column="in:Done+from:In Progress"
```

### Complex Combinations

Combine OR and AND logic for sophisticated queries:

```bash
# Tasks that either:
# - Moved from "In Progress" forward AND are currently in "Done"
# OR
# - Are currently in "Blocked"
phabfive maniphest search "My Project" \
  --column="from:In Progress:forward+in:Done,in:Blocked"

# Find workflow violations:
# Tasks in Done that either moved backward OR never went through Review
phabfive maniphest search "My Project" \
  --column="in:Done+backward,in:Done+never:In Review"
```

### Negation Patterns

Use the `not:` prefix to negate any pattern:

```bash
# Tasks NOT currently in Done
phabfive maniphest search "My Project" --column="not:in:Done"

# Tasks that have NOT moved backward
phabfive maniphest search "My Project" --column="not:backward"

# Tasks NOT currently in Done AND have been in Review
phabfive maniphest search "My Project" --column="not:in:Done+been:In Review"

# Tasks in Done that did NOT come from Backlog
phabfive maniphest search "My Project" --column="in:Done+not:from:Backlog"

# Complex: Tasks NOT in Review AND have NOT been blocked
phabfive maniphest search "My Project" --column="not:in:Review+not:been:Blocked"
```

**Note**: `not:been:COLUMN` is functionally equivalent to `never:COLUMN`. Both patterns exist for flexibility and readability.

### Viewing Transition History

Use `--show-history` to see transition history for tasks:

```bash
# Show history with filtering
phabfive maniphest search "My Project" --column=backward --show-history

# Show history without filtering
phabfive maniphest search "My Project" --show-history

# Filtering without history (only shows current state)
phabfive maniphest search "My Project" --column=backward
```

Output includes:
- Timestamp of each transition
- Source and destination columns/priorities
- Direction indicator (forward/backward for columns, raised/lowered for priorities)

Example output:
```
- Link: http://phorge.domain.tld/T59
  Task:
    Name: '[FEATURE] Improved error diagnostics'
    Created: 2025-10-01T17:21:56
    Modified: 2025-10-24T08:44:53
    Status: Open
    Priority: Unbreak Now!
    Description: |
      > Enhanced error reporting in chip simulator
    Boards:
      Development:
        Column: Up Next
      GUNNAR-Core:
        Column: In Review
  History:
    Priority:
      - "2025-10-01T17:21:56 [↓] Triage → Normal"
      - "2025-10-23T12:55:59 [↑] Normal → Unbreak Now!"
    Boards:
      Development:
        Transitions:
          - "2025-10-14T10:52:33 [→] Backlog → In Review"
          - "2025-10-14T14:31:40 [←] In Review → Up Next"
      GUNNAR-Core:
        Transitions:
          - "2025-10-24T08:44:52 [→] Backlog → Up Next"
          - "2025-10-24T08:44:53 [→] Up Next → In Review"
```

## Priority Filtering

Filter tasks based on their priority changes over time. This helps identify tasks that became urgent, track priority escalations, and analyze how task importance evolved.

### Why Use Priority Filtering?

Common use cases include:

- **Track escalations**: Find tasks that were raised to "Unbreak Now!" from lower priorities
- **Identify deprioritized work**: Tasks that were lowered from High to Normal
- **Find urgent tasks**: All tasks currently at "Unbreak Now!" priority
- **Audit priority history**: See complete priority change history for tasks

### Priority Pattern Types

| Pattern | Description | Example |
|---------|-------------|---------|
| `from:PRIORITY` | Task changed from PRIORITY | `from:Normal` |
| `from:PRIORITY:raised` | Task was raised from PRIORITY | `from:Normal:raised` |
| `from:PRIORITY:lowered` | Task was lowered from PRIORITY | `from:High:lowered` |
| `to:PRIORITY` | Task changed to PRIORITY | `to:Unbreak Now!` |
| `in:PRIORITY` | Task is currently at PRIORITY | `in:High` |
| `been:PRIORITY` | Task was at PRIORITY at any point | `been:Unbreak Now!` |
| `never:PRIORITY` | Task was never at PRIORITY | `never:Low` |
| `raised` | Task had any priority increase | `raised` |
| `lowered` | Task had any priority decrease | `lowered` |
| `not:PATTERN` | Negates any pattern above | `not:in:High`, `not:raised` |

**Negation Prefix `not:`**: Any pattern can be prefixed with `not:` to negate its meaning. This is a general negation operator that works with all pattern types. For example:
- `not:in:High` - Tasks NOT currently at High priority
- `not:raised` - Tasks whose priority hasn't been raised
- `not:been:Unbreak Now!` - Tasks never at Unbreak Now! (equivalent to `never:Unbreak Now!`)

### Priority Levels

Standard Phabricator/Phorge priorities (from highest to lowest):
- Unbreak Now!
- Triage
- High
- Normal
- Low
- Wishlist

### Basic Priority Examples

```bash
# Find tasks currently at Unbreak Now!
phabfive maniphest search "My Project" --priority="in:Unbreak Now!"

# Find tasks that were ever at Unbreak Now!
phabfive maniphest search "My Project" --priority="been:Unbreak Now!"

# Find tasks that were raised from Normal
phabfive maniphest search "My Project" --priority="from:Normal:raised"

# Find tasks that had any priority increase
phabfive maniphest search "My Project" --priority=raised
```

### Combining Column and Priority Filters

You can combine column and priority filters for powerful queries:

```bash
# Tasks that moved forward from "Up Next" AND were ever at Normal priority
phabfive maniphest search '*' \
  --column='from:Up Next:forward' \
  --priority='been:Normal'

# Tasks in Done that were raised from Normal
phabfive maniphest search "My Project" \
  --column="in:Done" \
  --priority="from:Normal:raised"

# Recently completed high-priority tasks
phabfive maniphest search "My Project" \
  --column="to:Done" \
  --priority="in:High" \
  --updated-after=7
```

### Priority OR/AND Logic

Same as column patterns, priority patterns support OR (comma) and AND (plus):

```bash
# Tasks at High OR Unbreak Now!
phabfive maniphest search "My Project" --priority="in:High,in:Unbreak Now!"

# Tasks raised from Normal AND currently at High
phabfive maniphest search "My Project" --priority="from:Normal:raised+in:High"
```

### Priority Negation Patterns

Use the `not:` prefix to negate priority patterns:

```bash
# Tasks NOT currently at High priority
phabfive maniphest search "My Project" --priority="not:in:High"

# Tasks whose priority has NOT been raised
phabfive maniphest search "My Project" --priority="not:raised"

# Tasks NOT at High priority AND have been raised at some point
phabfive maniphest search "My Project" --priority="not:in:High+raised"

# Tasks at Normal that did NOT come from being lowered
phabfive maniphest search "My Project" --priority="in:Normal+not:lowered"
```

**Note**: `not:been:PRIORITY` is functionally equivalent to `never:PRIORITY`.

## Viewing Metadata

Use `--show-metadata` to see why tasks matched your filters. This is especially useful when debugging complex filter combinations.

```bash
phabfive maniphest search '*' \
  --column='from:Up Next:forward' \
  --priority='been:Normal' \
  --show-metadata
```

Output includes:
```
Metadata:
  MatchedBoards: ['Development', 'GUNNAR-Core']
  MatchedPriority: true
```

The metadata section shows:
- **MatchedBoards**: Which boards satisfied the `--column` filter (in alphabetical order)
- **MatchedPriority**: Whether the task matched the `--priority` filter

This helps you understand exactly why a task appeared in your search results.

## Real-World Workflows

### Finding Tasks That Got Stuck

Identify tasks that moved backward from completion:

```bash
phabfive maniphest search "Backend Team" \
  --column="from:Done:backward" \
  --updated-after=30
```

### Tracking Fast-Tracked Tasks

Find tasks that went straight to Done without review:

```bash
phabfive maniphest search "Frontend" \
  --column="in:Done+never:In Review"
```

### Monitoring Blocked Work

See what's currently blocked and where it came from:

```bash
phabfive maniphest search "My Project" \
  --column="in:Blocked+from:In Progress"
```

### Quality Assurance

Find recently completed tasks that never went through testing:

```bash
phabfive maniphest search "Product" \
  --column="in:Done+never:Testing" \
  --updated-after=7
```

### Sprint Retrospective

Analyze all tasks completed in the last sprint:

```bash
phabfive maniphest search "Sprint 42" \
  --column="to:Done" \
  --updated-after=14
```

## Tips and Best Practices

### Column Name Matching

- Column names are **case-sensitive**
- Use exact column names as they appear in your workboard
- If unsure, check column names with a basic search first

### Performance

- Filtering requires fetching task history, which may be slower for large result sets
- Consider combining with date filters (`--created-after`, `--updated-after`) to narrow results
- Use specific project names rather than wildcards when possible

### Debugging Patterns

If a filter pattern doesn't return expected results:

1. Run the search without `--column` or `--priority` to see all tasks
2. Add `--show-history` to inspect actual column and priority movements for all tasks
3. Verify column names match exactly (case-sensitive)
4. Start with simple patterns and add complexity incrementally

### Common Pattern Combinations

```bash
# Just completed (moved to Done in last 7 days)
--column="to:Done" --updated-after=7

# Currently stuck (backward movement and currently not in Done)
--column="backward+in:In Progress"

# Never blocked, fast completion
--column="to:Done+never:Blocked" --updated-after=14

# Workflow compliance (went through all required stages)
--column="in:Done+been:In Review+been:Testing"
```

## Error Messages

### "Project not found"
The specified project doesn't exist. Phabfive will suggest similar project names.

### "Invalid filter pattern"
Check your pattern syntax:
- Use commas for OR, plus signs for AND
- Ensure column names are in quotes if they contain spaces
- Verify pattern types are spelled correctly

### "No tasks found"
The search returned no results. Try:
- Relaxing date filters
- Using simpler filter patterns
- Verifying the project has tasks with workboard columns

## See Also

- [Development Guide](development.md) - Set up a local development environment
- [Phorge Setup](phorge-setup.md) - Run a local Phorge instance for testing
