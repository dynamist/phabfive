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

Search for tasks within projects with various filtering options, including advanced project pattern matching with AND/OR logic.

### Basic Search

```bash
# Search within a specific project
phabfive maniphest search "My Project"

# Search all projects
phabfive maniphest search "*"

# Search multiple projects (OR logic)
phabfive maniphest search "ProjectA,ProjectB"

# Search project intersection (AND logic)
phabfive maniphest search "Team Alpha+Sprint 42"
```

!!! tip
    For advanced project filtering with AND/OR logic and complex patterns, see the [Advanced Project Filtering](#advanced-project-filtering) section below.

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

### Advanced Project Filtering

Search for tasks that belong to multiple projects using AND/OR logic. This powerful feature allows you to find tasks at the intersection of different project scopes or combine results from multiple project queries.

#### Pattern Syntax

Project patterns use a query language with AND/OR logic:

- **Comma (`,`)** = OR logic - match tasks in ANY of the projects
- **Plus (`+`)** = AND logic - match tasks in ALL specified projects
- **Wildcards (`*`)** can be combined with AND/OR operators

#### OR Logic Examples

Find tasks that belong to ANY of the specified projects:

```bash
# Tasks in EITHER ProjectA OR ProjectB
phabfive maniphest search "ProjectA,ProjectB"

# Tasks in any Backend project OR any Frontend project
phabfive maniphest search "Backend*,Frontend*"

# Tasks in ProjectA OR ProjectB OR ProjectC
phabfive maniphest search "ProjectA,ProjectB,ProjectC"
```

**Use case**: Finding all tasks across multiple related projects or teams.

#### AND Logic Examples

Find tasks that belong to ALL specified projects simultaneously:

```bash
# Tasks that are in BOTH ProjectA AND ProjectB
phabfive maniphest search "ProjectA+ProjectB"

# Tasks tagged with both a team and a sprint
phabfive maniphest search "Backend Team+Sprint 42"

# Tasks in multiple categories
phabfive maniphest search "Security+High Priority+Q1 2024"
```

**Use case**: Finding tasks at the intersection of multiple categorizations (e.g., tasks that belong to both a team project and a sprint milestone).

#### Complex Combinations

Combine OR and AND logic for sophisticated queries:

```bash
# Tasks that are in (ProjectA AND ProjectB) OR (ProjectC)
phabfive maniphest search "ProjectA+ProjectB,ProjectC"

# Tasks in (Backend Team AND Sprint 42) OR (Frontend Team AND Sprint 42)
phabfive maniphest search "Backend Team+Sprint 42,Frontend Team+Sprint 42"

# Tasks in any Q1 project AND tagged as urgent, OR any Q2 project
phabfive maniphest search "Q1*+Urgent,Q2*"
```

**How it works**: Comma-separated groups are evaluated independently (OR), and within each group, plus-separated projects must all match (AND).

#### Wildcards with AND/OR Logic

Combine wildcard patterns with logical operators:

```bash
# Tasks in any Backend project OR any API project
phabfive maniphest search "Backend*,API*"

# Tasks in both a Backend project AND marked as Security
phabfive maniphest search "Backend*+Security"

# Tasks in (any 2024 project AND High Priority) OR (any Archive project)
phabfive maniphest search "*2024+High Priority,Archive*"
```

#### Projects with Spaces

Project names containing spaces are fully supported:

```bash
# Single project with spaces
phabfive maniphest search "My Project"

# OR logic with spaces
phabfive maniphest search "Project A,Project B"

# AND logic with spaces
phabfive maniphest search "Backend Team+Sprint 42"

# Complex pattern with spaces
phabfive maniphest search "Q1 2024+Backend Team,Q1 2024+Frontend Team"
```

#### Real-World Examples

**Sprint Planning**: Find all tasks for a specific sprint across multiple teams:
```bash
phabfive maniphest search "Backend+Sprint 15,Frontend+Sprint 15,QA+Sprint 15"
```

**Cross-Team Features**: Find tasks that involve multiple teams:
```bash
phabfive maniphest search "Backend Team+Mobile Team"
```

**Security Audits**: Find security tasks across all product areas:
```bash
phabfive maniphest search "Product*+Security"
```

**Quarterly Planning**: Find all high-priority tasks for Q1 across teams:
```bash
phabfive maniphest search "Q1 2024+High Priority"
```

**Release Tracking**: Find tasks for a specific release across components:
```bash
phabfive maniphest search "Release 2.0+API,Release 2.0+UI,Release 2.0+Database"
```

#### Combining with Other Filters

Project patterns work seamlessly with other search filters:

```bash
# Recent tasks in multiple projects
phabfive maniphest search "ProjectA,ProjectB" --updated-after=7

# Tasks in both team and sprint, currently in specific column
phabfive maniphest search "Backend+Sprint 42" --column="in:In Progress"

# High-priority tasks across backend services
phabfive maniphest search "Backend*" --priority="in:High"

# Tasks at intersection of team and milestone, recently completed
phabfive maniphest search "API Team+Milestone 3" \
  --column="to:Done" \
  --updated-after=14

# Security tasks across products that moved backward
phabfive maniphest search "Product*+Security" \
  --column=backward \
  --show-history
```

#### Tips for Project Filtering

**Pattern Evaluation**:
- OR patterns (comma-separated) are evaluated left to right - tasks matching ANY pattern are included
- AND patterns (plus-separated) require the task to belong to ALL specified projects
- Empty strings (`""`) return no results - use `"*"` to search all projects instead

**Performance Considerations**:
- Specific project names are faster than wildcards
- Wildcards like `"*"` (all projects) may take longer for large instances
- Combine with date filters (`--created-after`, `--updated-after`) to narrow results

**Debugging Patterns**:
If a pattern doesn't return expected results:

1. Test each project name individually first
2. Verify project names match exactly (check for typos, extra spaces)
3. Remember that AND logic requires tasks to be in ALL projects simultaneously
4. Use `phabfive maniphest search "*"` to see all available tasks and their projects

**Common Patterns**:
```bash
# Multiple teams working on same feature
"Team A+Feature X,Team B+Feature X"

# All projects in a category
"Backend*,Frontend*,Mobile*"

# Specific sprint across teams
"Sprint 42+Backend,Sprint 42+Frontend,Sprint 42+QA"

# Cross-functional initiatives
"Security+*"
```

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

## Status Filtering

Filter tasks based on their status changes over time. This helps identify tasks that progressed through workflows, track status regressions, and analyze how task completion status evolved.

### Why Use Status Filtering?

Common use cases include:

- **Track completions**: Find tasks that changed to "Resolved"
- **Identify regressions**: Tasks that moved backward from Resolved to Open
- **Find blocked work**: Tasks that are currently Blocked
- **Audit status history**: See complete status change history for tasks
- **Monitor workflow progression**: Find tasks that reached specific milestones

### Status Pattern Types

| Pattern | Description | Example |
|---------|-------------|---------|
| `from:STATUS` | Task changed from STATUS | `from:Open` |
| `from:STATUS:raised` | Task progressed from STATUS | `from:Open:raised` |
| `from:STATUS:lowered` | Task regressed from STATUS | `from:Resolved:lowered` |
| `to:STATUS` | Task changed to STATUS | `to:Resolved` |
| `in:STATUS` | Task is currently at STATUS | `in:Resolved` |
| `been:STATUS` | Task was at STATUS at any point | `been:Resolved` |
| `never:STATUS` | Task was never at STATUS | `never:Blocked` |
| `raised` | Task had any status progression | `raised` |
| `lowered` | Task had any status regression | `lowered` |
| `not:PATTERN` | Negates any pattern above | `not:in:Open`, `not:raised` |

**Negation Prefix `not:`**: Any pattern can be prefixed with `not:` to negate its meaning. This is a general negation operator that works with all pattern types. For example:
- `not:in:Open` - Tasks NOT currently Open
- `not:raised` - Tasks whose status hasn't progressed
- `not:been:Resolved` - Tasks never been Resolved (equivalent to `never:Resolved`)

### Status Values

The tool dynamically fetches status information from your Phabricator/Phorge instance using the `maniphest.querystatuses` API. Standard Phabricator statuses include (in progression order):

- **Open** (0) - Initial state for new tasks
- **Blocked** (1) - Task is blocked/waiting on something
- **Wontfix** (2) - Terminal: Won't be fixed
- **Invalid** (3) - Terminal: Invalid task
- **Duplicate** (4) - Terminal: Duplicate of another task
- **Resolved** (5) - Terminal: Task completed successfully

**Open vs Closed**: Only Open and Blocked are "open" statuses. All others (Wontfix, Invalid, Duplicate, Resolved) are terminal/closed states.

**Status Progression**:
- Moving from a lower number to a higher number is considered **"raised"** (forward progression)
- Moving from a higher number to a lower number is considered **"lowered"** (regression/reopening)
- For example: Open (0) → Resolved (5) is "raised" (task progressed forward)
- For example: Resolved (5) → Open (0) is "lowered" (task was reopened)

**Note**: If your Phabricator/Phorge instance uses custom statuses, the tool will automatically adapt to your configuration.

### Basic Status Examples

```bash
# Find tasks currently Open
phabfive maniphest search "My Project" --status="in:Open"

# Find tasks that were ever Resolved
phabfive maniphest search "My Project" --status="been:Resolved"

# Find tasks that progressed from Open
phabfive maniphest search "My Project" --status="from:Open:raised"

# Find tasks that had any status progression
phabfive maniphest search "My Project" --status=raised
```

### Combining Column, Priority, and Status Filters

You can combine all three filter types for powerful queries:

```bash
# Tasks moved to Done AND were raised from Open AND are currently Resolved
phabfive maniphest search '*' \
  --column='to:Done' \
  --priority='from:Normal:raised' \
  --status='in:Resolved'

# Tasks in progress that have been blocked
phabfive maniphest search "My Project" \
  --column="in:In Progress" \
  --status="been:Blocked"

# Recently completed tasks that were never blocked
phabfive maniphest search "My Project" \
  --status="to:Resolved" \
  --updated-after=7 \
  --status="never:Blocked"
```

### Status OR/AND Logic

Same as column and priority patterns, status patterns support OR (comma) and AND (plus):

```bash
# Tasks currently Open OR Blocked
phabfive maniphest search "My Project" --status="in:Open,in:Blocked"

# Tasks raised from Open AND currently Resolved
phabfive maniphest search "My Project" --status="from:Open:raised+in:Resolved"
```

### Status Negation Patterns

Use the `not:` prefix to negate status patterns:

```bash
# Tasks NOT currently Open
phabfive maniphest search "My Project" --status="not:in:Open"

# Tasks whose status has NOT progressed
phabfive maniphest search "My Project" --status="not:raised"

# Tasks NOT Resolved AND have been Blocked at some point
phabfive maniphest search "My Project" --status="not:in:Resolved+been:Blocked"

# Tasks that progressed but did NOT reach Resolved
phabfive maniphest search "My Project" --status="raised+not:in:Resolved"
```

**Note**: `not:been:STATUS` is functionally equivalent to `never:STATUS`.

## Viewing Metadata

Use `--show-metadata` to see why tasks matched your filters. This is especially useful when debugging complex filter combinations.

```bash
phabfive maniphest search '*' \
  --column='from:Up Next:forward' \
  --priority='been:Normal' \
  --status='in:Resolved' \
  --show-metadata
```

Output includes:
```
Metadata:
  MatchedBoards: ['Development', 'GUNNAR-Core']
  MatchedPriority: true
  MatchedStatus: true
```

The metadata section shows:
- **MatchedBoards**: Which boards satisfied the `--column` filter (in alphabetical order)
- **MatchedPriority**: Whether the task matched the `--priority` filter
- **MatchedStatus**: Whether the task matched the `--status` filter

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
