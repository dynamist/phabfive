# Maniphest CLI

The Maniphest CLI provides powerful commands for managing tasks in Phabricator/Phorge from the terminal.

## Overview

Maniphest is Phabricator's task tracking application. The phabfive CLI allows you to:

- Search and filter tasks with advanced filtering
- Use [search specs](./search-specs.md) for complex, reusable queries
- Use [create specs](./create-specs.md) for bulk task creation with relationships
- Add comments to tasks
- Track and filter tasks by their workboard column transitions
- Track and filter tasks by their priority transitions

## Basic Commands

### Show Task Details

Display information about a specific task:

```bash
# Basic details
phabfive maniphest show T123

# Multiple tasks (space- or comma-separated)
phabfive maniphest show T123 T456
phabfive maniphest show T123,T456

# Show the optional sections: transition history, metadata, comments, policies
phabfive maniphest show T123 --show-history --show-metadata
phabfive maniphest show T123 -H -M -C -P

# Machine-readable: one JSON array, or one object per line
phabfive --format=json maniphest show T123 T456
phabfive --format=jsonl maniphest show T123 T456 | jq -c '.Task.Name'
```

When using `--show-history`, the output includes complete workboard transition history showing:
- All column movements across all workboards
- Timestamps for each transition
- Direction indicators (forward/backward)

Example output with `--show-history`:
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

### Task Policies

`--show-policy` adds who can see, edit and interact with a task, and
`maniphest edit` and `maniphest create` set the first two:

```bash
phabfive maniphest show T237 --show-policy
```

```
  Policy:
    Visible To: All Users
    Editable By: '#infrastructure'
    Can Interact: All Users
```

The value grammar, why `Can Interact` cannot be set, and what happens to a
policy change with no terminal to review it on are all in
[Policies](policies.md).

### Create a Task

`--tag` and `--subscribe` are repeatable and comma-separated, like every
option that adds values, so these two are the same:

```bash
phabfive maniphest create "Fix the importer" --tag=Backend,QA --subscribe=@alice,@bob
phabfive maniphest create "Fix the importer" --tag=Backend --tag=QA --subscribe=@alice --subscribe=@bob
```

`--column` places the task on the board of the first `--tag`. `+` between
values (`--tag=Backend+QA`) still works but is deprecated and warns: in a search
filter `+` means AND, which a list of values to add has no use for. The search
filters below keep that grammar, where `,` is OR.

### Subscribers

`maniphest edit` (and `phabfive edit`) adds subscribers with `--subscribe` and
removes them with `--unsubscribe`. Both are repeatable and comma-separated,
and both may be given in one edit:

```bash
phabfive maniphest edit T123 --subscribe=@alice,@bob
phabfive maniphest edit T123 T124 --unsubscribe=@me
phabfive maniphest edit T123 --subscribe=@bob --unsubscribe=@alice
```

Only what changes is sent: a user already subscribed is not added again, and one
who is not subscribed is not removed, so an edit that changes nothing says so and
sends nothing. The same user in both options is refused, however each was spelled.
`--add-subscriber` and `--remove-subscriber` are accepted as other names for the two options.

Creating a task subscribes its author, who can leave with
`--unsubscribe=@me`. The owner of a task is notified by Phorge whether
subscribed or not.

### Commits

`maniphest edit` (and `phabfive edit`) attaches commits with `--attach` and
detaches them with `--detach`; `maniphest create` takes `--attach`. Each takes a
`rCALLSIGN<hash>`, an `R1:<hash>`, a bare hash of at least seven characters or
a commit PHID, and like the subscriber options they are repeatable,
comma-separated and send only what changes:

```bash
phabfive maniphest create "Fix the importer" --tag=Backend --attach=7d7fc2c
phabfive maniphest edit T123 --attach=rGUNNAR7d7fc2c3e002,R10:3ce278cd9912
phabfive maniphest edit T123 --detach=7d7fc2c
```

`--add-commit` and `--remove-commit` are accepted as other names for the two.

`maniphest show` lists commits after the parents and subtasks, each by the name
Diffusion gives it and linked to its page. Machine-readable formats always carry
the key, as they do `Parents` and `Subtasks`:

```yaml
  Commits:
  - Link: http://phorge.localhost/rGUNNAR7d7fc2c3e0023069cb381fa88cc08b559d40afb0
    Commit:
      Identifier: rGUNNAR7d7fc2c3e002
      Summary: Sketch the telemetry frame format
```

A bare hash that more than one repository has is an error naming each match;
see [Attaching Commits](edit-cli.md#attaching-commits).

### Add Comments

Add a comment to a task:

```bash
phabfive maniphest comment T123 "This is my comment"
```

The command will output the task URI after successfully adding the comment.

### Create Tasks from a Spec

Several related tasks come from one file rather than from one command per task. The command
is `phabfive apply`, at the top level, because a file may create projects as well as tasks:

```bash
# Always preview first
phabfive apply -f specs/create/sprint-tasks.yaml --dry-run

# Create them for real
phabfive apply -f specs/create/sprint-tasks.yaml

# Supply a variable the file declares
phabfive apply -f specs/create/sprint-tasks.yaml --set sprint=13
```

```console
$ phabfive apply -f specs/create/sprint-tasks.yaml --dry-run
[DRY RUN] specs/create/sprint-tasks.yaml: would create 3 tasks.
  - task 'Plan sprint 12'
  - task 'Build release 2024.4'
  - task 'Review release 2024.4 against the security checklist'
```

A create spec gives you nested subtasks linked to their parent, links onto tasks that already
exist, Jinja2 variables, assignments, subscribers, Spaces and per-task projects. See
[Creating with Specs](create-specs.md) for the guide and
[the format page](phorge-spec.md) for every key.

`phabfive maniphest create --with FILE` still reads the same file and still works, but warns
that `phabfive apply -f FILE` replaces it. `project create` and `paste create` take `--with`
too, and all three read every spec a create spec may be, whatever it creates.

## Task Search

Search for tasks using free-text queries and various filtering options, including advanced project pattern matching with AND/OR logic.

### Using a Search Spec

A complex or frequently-used search belongs in a file. `phabfive search` runs every search in
one, whatever applications they ask:

```bash
# One search
phabfive search -f specs/search/high-priority-stale-tasks.yaml

# Four searches in one file, each with its own banner
phabfive search -f specs/search/project-status-overview.yaml

# Machine-readable, one record per line
phabfive --format=jsonl search -f specs/search/blocked-tasks.yaml
```

Everything inside a spec's `search:` block is the same vocabulary as the flags below:
`column:` is `--column`, `show-history:` is `--show-history`. See
[Searching with Specs](search-specs.md) for the guide and [the format page](phorge-spec.md)
for every key.

`phabfive maniphest search --with FILE` still works and still lets command-line flags override
what the file says, but warns that `phabfive search -f FILE` replaces it - and unlike
`--with`, `search -f` runs project, paste and passphrase items too.

### Free-Text Search

Search for tasks by text in their title or description:

```bash
# Search for "Let's Encrypt" in any task
phabfive maniphest search "Let's Encrypt"

# Search for "OpenStack" with date filter
phabfive maniphest search "OpenStack" --updated-after=1w

# Search for text within a specific project
phabfive maniphest search "API error" --tag "Backend Team"

# Search with status filter
phabfive maniphest search "database migration" --status="in:Open"
```

**How it works:**
- Searches both task titles and descriptions
- Case-insensitive matching
- Uses Phabricator's built-in full-text search, the same as the web UI's search box
- Matches **whole words**, stemmed: `migration` finds "migrations", but `igrat` finds nothing

The text is passed to Phorge as it stands, so its search operators work here:

| Written as | Matches |
| --- | --- |
| `word` | The whole word, stemmed |
| `~word` | Any word containing it: `~igrat` finds "migration" |
| `"a phrase"` | Those words in that order |
| `-word` | Leaves out what contains the word |
| `title:word` | Only the title is searched |

When a text search finds nothing, phabfive says so on stderr and suggests the same
search with `~` in front of each plain word:

```console
$ phabfive maniphest search igrat
No tasks found
Text search matches whole words, not parts of words. To match part of a word, prefix it with ~: '~igrat'
Other operators: "a phrase" in that order, -word to leave a word out, title:word to search only titles.
```

`paste search` and `project search` take the same syntax and give the same hint.

### Basic Project Filtering

Filter tasks by project/workboard using the `--tag` option:

```bash
# Search within a specific project
phabfive maniphest search --tag "My Project"

# Search all projects (or omit --tag entirely)
phabfive maniphest search --tag "*"

# Search multiple projects (OR logic)
phabfive maniphest search --tag "ProjectA,ProjectB"

# Search project intersection (AND logic)
phabfive maniphest search --tag "Team Alpha+Sprint 42"

# Combine with date filters
phabfive maniphest search --tag "Backend" --updated-after=1w

# Search by project ID (from the project URL, e.g. /project/view/8048/) or PHID
phabfive maniphest search --tag 8048
phabfive maniphest search --tag PHID-PROJ-abcdefghijklmnopqrst
```

!!! tip
    Use a project ID when a name is ambiguous, for example milestones that share a
    name like "Kanban Board" with milestones of other projects. If a project's name or
    hashtag is itself a number, the name/hashtag match wins over the ID.

!!! tip
    For advanced project filtering with AND/OR logic and complex patterns, see the [Advanced Project Filtering](#advanced-project-filtering) section below.

### Wildcard Project Matching

The `--tag` option supports wildcard patterns:

```bash
# All projects starting with "Backend"
phabfive maniphest search --tag "Backend*"

# All projects ending with "2024"
phabfive maniphest search --tag "*2024"

# All projects containing "API"
phabfive maniphest search --tag "*API*"
```

!!! note
    Project matching is case-insensitive. If no exact match is found, phabfive will suggest similar project names.

### Combining Filters

You can combine free-text search with project filters and other options:

```bash
# Text search within a specific project
phabfive maniphest search "kubernetes" --tag "Infrastructure"

# Text search across multiple projects
phabfive maniphest search "security" --tag "Backend*,Frontend*"

# Text search with date and column filters
phabfive maniphest search "migration" --tag "Database" --column="in:In Progress" --updated-after=2w
```

### Naming Users

Every option that takes a user - `--assigned`, `--author`, `--assign`,
`--subscribe` and `--unsubscribe` here, and the same options on `paste` and `project` - takes any of:

| Spelling | Names |
|---|---|
| `alice` or `@alice` | the user with that username, in any case |
| `@me` | you, whoever the API token belongs to |
| `PHID-USER-...` | the user with that PHID |

```bash
phabfive maniphest search --assigned=@me,alice
phabfive maniphest edit T123 --assign=PHID-USER-75k6ju3upxlmi3gmks3m --subscribe=@bob
```

A name that is not a user is an error rather than a search that matches nothing.

`@me` is a **keyword** and always means you, even on an instance that has a user
whose username is `me`. The `@` is what makes it one, and this is the only place
in phabfive where the sigil changes what a value means - everywhere else
`alice` and `@alice` are the same user. To name that account, write it without
the sigil:

```bash
phabfive maniphest search --author=@me     # your tasks
phabfive maniphest search --author=me      # the user whose username is "me"
```

!!! important
    **Validation:** At least one filter is required, to prevent accidentally
    querying all tasks. Any one of these counts:

    `TEXT_QUERY`, `--tag`, `--ids`, `--phids`, `--include`, `--assigned`,
    `--author`, `--subscriber`, `--closed-by`, `--subtype`, `--parent`,
    `--subtask`, `--has-parents`, `--has-subtasks`, `--space`,
    `--created-after`, `--created-before`, `--updated-after`,
    `--updated-before`, `--closed-after`, `--closed-before`, `--visible-to`,
    `--editable-by`, `--column`, `--priority` or `--status`.

    `--exclude`, `--order` and the `--show-*` flags do **not** count: they say
    what to leave out, how to sort and what to display, not which tasks to look
    at. `--status=any` on its own is the deliberate way to ask for all of them.

### Narrowing by identity and structure

These reach `maniphest.search` as constraints, so only matching tasks cross the
wire:

| Option | Filter |
|---|---|
| `--ids T1,T2` | Only these tasks, with every other filter still applied |
| `--phids PHID-TASK-...` | The same, by PHID |
| `--subscriber @me,alice` | Tasks any of them is subscribed to |
| `--subtype bug` | Tasks of this subtype (`maniphest.subtypes` configuration) |
| `--parent T10` | Subtasks of these tasks |
| `--subtask T11` | Parents of these tasks |
| `--has-parents` | Only tasks that are a subtask of something |
| `--has-subtasks` | Only tasks that have subtasks |
| `--closed-by @me` | Tasks closed by any of these users |
| `--closed-after 7d` | Tasks closed within TIME |
| `--closed-before 30d` | Tasks closed more than TIME ago |

`--ids` is not `--include`: an id given to `--ids` is still subject to every
other filter, and the result is the intersection. `--include` is the opposite -
it forces a task into the results whatever the filters say.

A task id is written as a monogram, `T123`, everywhere - `--ids`, `--parent`,
`--subtask`, `--include` and `--exclude` alike, and in a spec. A bare number is
refused by name.

`--has-parents` and `--has-subtasks` are tri-state: not given sends nothing,
and the flags set them true. The false half - "tasks with no parent at all" - is
written in a [search spec](search-specs.md) as `has-parents: false`.

### Listing Every Task

`--status=any` on its own is a deliberate request for every task, open or
closed. Like every search it is confined to the default Space (`PHAB_SPACE`,
`S1` unless configured), so add `--space='*'` to reach tasks in every Space, and
`-l 0` to lift the default limit of 100:

```bash
# Every task you can see, in every Space, one JSON object per line
phabfive --format=jsonl maniphest search --status=any --space='*' -l 0

# The same with each task's policies, for an audit
phabfive --format=jsonl maniphest search --status=any --space='*' --show-policy -l 0
```

Leaving out `--space='*'` is the easy mistake in a script that means to touch
every task: tasks in other Spaces are silently not listed.

A bare `phabfive maniphest search` still queries nothing: it prints its help and exits 2.

### Filtering by Policy

`--visible-to` and `--editable-by` keep the tasks whose view or edit policy is
exactly the value given, in the same grammar `maniphest edit` takes: `public`,
`users`, `admin`, `no-one`, a `#project`, an `@user`, `@me` or a PHID. See
[Policies](policies.md).

```bash
# Every task, in every Space, still editable by All Users
phabfive --format=jsonl maniphest search --status=any --space='*' --editable-by=users -l 0

# Open tasks only #infrastructure can see
phabfive maniphest search --visible-to='#infrastructure'
```

The match is on the stored policy, not on who can see or edit the task:
`--visible-to=users` finds tasks set to "All Users", not every task a logged-in
user could open. Both options together must both match.

`maniphest.search` has no policy constraint, so phabfive filters the tasks it
fetched. The filter runs before `--limit`, so `-l 10` keeps the first ten tasks
that match. A value outside the grammar, or a project or user that does not
exist, is an error before anything is fetched.

`--all` is a deprecated spelling of `--status=any`. It still works, prints a
warning, and is refused together with `--status=open` or `--status=closed`.
See [Status Scope](#status-scope-open-closed-any).

### Pinning Tasks Into or Out of Results

Force specific tasks into the results with `--include`, even when the other
filters (tag, dates, space, column/priority/status patterns, or `--limit`)
don't match them — useful for pinning tasks into reports. The orthogonal
`--exclude` removes matched tasks to unpin them:

```bash
# Sprint report with two pinned tasks and one unpinned
phabfive maniphest search --tag "Sprint*" --include T2069,T2257 --exclude T1500

# Exactly these tasks (no other filters needed)
phabfive maniphest search --include T2069,T2257
```

**Behavior:**

- `--include` tasks always appear in the output, bypassing status/space/date
  constraints, post-filters, and `--limit`. A task that both matches the
  search and is included appears once.
- `--exclude` is applied before `--limit`, so freed slots fill with other
  matches. Excluded IDs that didn't match anything are silently ignored.
  `--limit` keeps the top N of the [result ordering](#result-ordering).
- Passing the same task to both `--include` and `--exclude` is an error.
- Both are supported in [search specs](search-specs.md) as a
  comma-separated string (`include: "T2069,T2257"`) or a YAML list:

  ```yaml
  search:
    include:
      - T2069
      - T2257
    exclude: "T1500"
  ```

### Result Ordering

Results come back in a fixed order every run. The default is **priority**,
highest first, which is the same order the Phorge web UI shows by default.
Pick another with `--order` (`-o`):

```bash
phabfive maniphest search --tag Sprint1 --order updated
phabfive maniphest search --tag Sprint1 --order title:desc --limit 10
```

An order is written as `<field>[:asc|:desc]`. The bare field gives the
direction you almost always want, and both directions are available for every
field:

| Value | Order |
|---|---|
| `priority` *(default)* | Highest priority first |
| `priority:asc` | Lowest priority first |
| `updated` | Recently updated first |
| `updated:asc` | Least recently updated first |
| `created` | Newest task first |
| `created:asc` | Oldest task first |
| `closed` | Recently closed first |
| `closed:asc` | Longest-closed first |
| `title` | Alphabetical, A-Z |
| `title:desc` | Alphabetical, Z-A |
| `relevance` | Best text match first |

**Behavior:**

- Ordering is applied **before** `--limit`, so `--limit 20 --order priority`
  returns the twenty highest-priority tasks rather than twenty arbitrary ones.
- `closed` and `closed:asc` both leave open tasks at the end; they have no
  close date to sort by.
- `--include` tasks are appended after the limit and keep the order you listed
  them in, so they stay at the end of the output whatever `--order` says. That
  makes it easy to see what you pinned.
- `relevance` uses the server's own text-match ranking, which only means
  anything alongside a text query. It is the one order phabfive cannot re-sort
  locally, so when a tag spans several projects those results stay grouped by
  project (deterministically, but grouped).
- `order` is supported in [search specs](search-specs.md):

  ```yaml
  search:
    tag: "Sprint*"
    order: updated:asc
  ```

- Phorge's own order names are not accepted, since `outdated` is opaque and
  `newest` never says newest what. Typing one tells you the phabfive spelling:
  `--order newest` suggests `created`.

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
phabfive maniphest search --tag "ProjectA,ProjectB"

# Tasks in any Backend project OR any Frontend project
phabfive maniphest search --tag "Backend*,Frontend*"

# Tasks in ProjectA OR ProjectB OR ProjectC
phabfive maniphest search --tag "ProjectA,ProjectB,ProjectC"
```

**Use case**: Finding all tasks across multiple related projects or teams.

#### AND Logic Examples

Find tasks that belong to ALL specified projects simultaneously:

```bash
# Tasks that are in BOTH ProjectA AND ProjectB
phabfive maniphest search --tag "ProjectA+ProjectB"

# Tasks tagged with both a team and a sprint
phabfive maniphest search --tag "Backend Team+Sprint 42"

# Tasks in multiple categories
phabfive maniphest search --tag "Security+High Priority+Q1 2024"
```

**Use case**: Finding tasks at the intersection of multiple categorizations (e.g., tasks that belong to both a team project and a sprint milestone).

#### Complex Combinations

Combine OR and AND logic for sophisticated queries:

```bash
# Tasks that are in (ProjectA AND ProjectB) OR (ProjectC)
phabfive maniphest search --tag "ProjectA+ProjectB,ProjectC"

# Tasks in (Backend Team AND Sprint 42) OR (Frontend Team AND Sprint 42)
phabfive maniphest search --tag "Backend Team+Sprint 42,Frontend Team+Sprint 42"

# Tasks in any Q1 project AND tagged as urgent, OR any Q2 project
phabfive maniphest search --tag "Q1*+Urgent,Q2*"
```

**How it works**: Comma-separated groups are evaluated independently (OR), and within each group, plus-separated projects must all match (AND).

#### Wildcards with AND/OR Logic

Combine wildcard patterns with logical operators:

```bash
# Tasks in any Backend project OR any API project
phabfive maniphest search --tag "Backend*,API*"

# Tasks in both a Backend project AND marked as Security
phabfive maniphest search --tag "Backend*+Security"

# Tasks in (any 2024 project AND High Priority) OR (any Archive project)
phabfive maniphest search --tag "*2024+High Priority,Archive*"
```

#### Projects with Spaces

Project names containing spaces are fully supported:

```bash
# Single project with spaces
phabfive maniphest search --tag "My Project"

# OR logic with spaces
phabfive maniphest search --tag "Project A,Project B"

# AND logic with spaces
phabfive maniphest search --tag "Backend Team+Sprint 42"

# Complex pattern with spaces
phabfive maniphest search --tag "Q1 2024+Backend Team,Q1 2024+Frontend Team"
```

#### Real-World Examples

**Sprint Planning**: Find all tasks for a specific sprint across multiple teams:
```bash
phabfive maniphest search --tag "Backend+Sprint 15,Frontend+Sprint 15,QA+Sprint 15"
```

**Cross-Team Features**: Find tasks that involve multiple teams:
```bash
phabfive maniphest search --tag "Backend Team+Mobile Team"
```

**Security Audits**: Find security tasks across all product areas:
```bash
phabfive maniphest search --tag "Product*+Security"
```

**Quarterly Planning**: Find all high-priority tasks for Q1 across teams:
```bash
phabfive maniphest search --tag "Q1 2024+High Priority"
```

**Release Tracking**: Find tasks for a specific release across components:
```bash
phabfive maniphest search --tag "Release 2.0+API,Release 2.0+UI,Release 2.0+Database"
```

#### Combining with Other Filters

Project patterns work seamlessly with other search filters:

```bash
# Recent tasks in multiple projects
phabfive maniphest search --tag "ProjectA,ProjectB" --updated-after=1w

# Tasks in both team and sprint, currently in specific column
phabfive maniphest search --tag "Backend+Sprint 42" --column="in:In Progress"

# High-priority tasks across backend services
phabfive maniphest search --tag "Backend*" --priority="in:High"

# Tasks at intersection of team and milestone, recently completed
phabfive maniphest search --tag "API Team+Milestone 3" \
  --column="to:Done" \
  --updated-after=2w

# Security tasks across products that moved backward
phabfive maniphest search --tag "Product*+Security" \
  --column=backward \
  --show-history
```

#### Tips for Project Filtering

**Pattern Evaluation**:
- OR patterns (comma-separated) are evaluated left to right - tasks matching ANY pattern are included
- AND patterns (plus-separated) require the task to belong to ALL specified projects
- Use `--tag "*"` to search all projects, or omit `--tag` entirely for the same effect

**Performance Considerations**:
- Specific project names are faster than wildcards
- Wildcards like `"*"` (all projects) may take longer for large instances
- Combine with date filters (`--created-after`, `--created-before`, `--updated-after`, `--updated-before`) to narrow results

**Debugging Patterns**:
If a pattern doesn't return expected results:

1. Test each project name individually first
2. Verify project names match exactly (check for typos, extra spaces)
3. Remember that AND logic requires tasks to be in ALL projects simultaneously
4. Use `phabfive maniphest search --tag "*"` to see all available tasks and their projects

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

Filter tasks by creation or modification date using both "after" (within the last TIME) and "before" (more than TIME ago) filters. Time values support multiple units for convenience.

**Supported Time Units:**
- `h` - hours (e.g., `12h` = 12 hours)
- `d` - days (e.g., `7d` = 7 days, or just `7` defaults to days)
- `w` - weeks (e.g., `2w` = 2 weeks = 14 days)
- `m` - months (e.g., `1m` = 1 month ≈ 30 days)
- `y` - years (e.g., `1y` = 1 year ≈ 365 days)

```bash
# Tasks created in the last week (using time units)
phabfive maniphest search --tag "My Project" --created-after=1w

# Tasks created more than a month ago (older tasks)
phabfive maniphest search --tag "My Project" --created-before=1m

# Tasks updated in the last 3 days (backward compatible)
phabfive maniphest search --updated-after=3

# Tasks updated more than 1 week ago (stale tasks)
phabfive maniphest search --tag "My Project" --updated-before=1w

# Tasks updated in the last 12 hours (recent activity)
phabfive maniphest search --tag "My Project" --updated-after=12h

# Combine both filters to create date ranges
# Tasks created between 1 week and 1 month ago
phabfive maniphest search --tag "My Project" --created-after=1m --created-before=1w

# Combine with free-text search
phabfive maniphest search "migration" --created-after=1m --updated-after=1w

# Find stale high-priority tasks (not updated in 2 weeks)
phabfive maniphest search --tag "My Project" --updated-before=2w --priority="in:High"
```

**Date Filter Options:**
- `--created-after=TIME`: Tasks created within the last TIME (e.g., `1h`, `7d`, `2w`, `1m`, `1y`)
- `--created-before=TIME`: Tasks created more than TIME ago (e.g., `1h`, `7d`, `2w`, `1m`, `1y`)
- `--updated-after=TIME`: Tasks updated within the last TIME (e.g., `1h`, `7d`, `2w`, `1m`, `1y`)
- `--updated-before=TIME`: Tasks updated more than TIME ago (e.g., `1h`, `7d`, `2w`, `1m`, `1y`)

**Note:** Plain numbers (e.g., `7`) are interpreted as days for backward compatibility.

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

**Negation Prefix `not:`**

Any pattern can be prefixed with `not:` to negate its meaning. This is a general negation operator that works with all pattern types. For example:

- `not:in:Done` - Tasks NOT currently in Done
- `not:from:Backlog` - Tasks that didn't move from Backlog
- `not:backward` - Tasks that haven't moved backward
- `not:been:Blocked` - Tasks that were never in Blocked (equivalent to `never:Blocked`)

### Basic Examples

```bash
# Find all tasks that moved backward (returned to earlier columns)
phabfive maniphest search --tag "My Project" --column=backward

# Find tasks currently in the "Blocked" column
phabfive maniphest search --tag "My Project" --column="in:Blocked"

# Find tasks that moved to "Done"
phabfive maniphest search --tag "My Project" --column="to:Done"

# Find tasks that moved forward from "In Progress"
phabfive maniphest search --tag "My Project" --column="from:In Progress:forward"
```

### OR Logic (Comma Separator)

Match tasks that satisfy **any** of the patterns:

```bash
# Tasks that are EITHER in Done OR in Blocked
phabfive maniphest search --tag "My Project" --column="in:Done,in:Blocked"

# Tasks that moved to Done OR moved backward
phabfive maniphest search --tag "My Project" --column="to:Done,backward"

# Tasks in multiple columns
phabfive maniphest search --tag "My Project" --column="in:In Progress,in:In Review,in:Testing"
```

### AND Logic (Plus Separator)

Match tasks that satisfy **all** conditions:

```bash
# Tasks that moved from "In Progress" AND are currently in "Done"
phabfive maniphest search --tag "My Project" --column="from:In Progress+in:Done"

# Tasks that moved from "Up Next" forward AND never got blocked
phabfive maniphest search --tag "My Project" --column="from:Up Next:forward+never:Blocked"

# Tasks currently in Done AND moved there from In Progress (skipped review)
phabfive maniphest search --tag "My Project" --column="in:Done+from:In Progress"
```

### Complex Combinations

Combine OR and AND logic for sophisticated queries:

```bash
# Tasks that either:
# - Moved from "In Progress" forward AND are currently in "Done"
# OR
# - Are currently in "Blocked"
phabfive maniphest search --tag "My Project" \
  --column="from:In Progress:forward+in:Done,in:Blocked"

# Find workflow violations:
# Tasks in Done that either moved backward OR never went through Review
phabfive maniphest search --tag "My Project" \
  --column="in:Done+backward,in:Done+never:In Review"
```

### Negation Patterns

Use the `not:` prefix to negate any pattern:

```bash
# Tasks NOT currently in Done
phabfive maniphest search --tag "My Project" --column="not:in:Done"

# Tasks that have NOT moved backward
phabfive maniphest search --tag "My Project" --column="not:backward"

# Tasks NOT currently in Done AND have been in Review
phabfive maniphest search --tag "My Project" --column="not:in:Done+been:In Review"

# Tasks in Done that did NOT come from Backlog
phabfive maniphest search --tag "My Project" --column="in:Done+not:from:Backlog"

# Complex: Tasks NOT in Review AND have NOT been blocked
phabfive maniphest search --tag "My Project" --column="not:in:Review+not:been:Blocked"
```

**Note**: `not:been:COLUMN` is functionally equivalent to `never:COLUMN`. Both patterns exist for flexibility and readability.

### Viewing Transition History

Use `--show-history` to see transition history for tasks:

```bash
# Show history with filtering
phabfive maniphest search --tag "My Project" --column=backward --show-history

# Show history without filtering
phabfive maniphest search --tag "My Project" --show-history

# Filtering without history (only shows current state)
phabfive maniphest search --tag "My Project" --column=backward
```

Output includes:
- Timestamp of each transition
- Source and destination columns/priorities
- Direction indicator (forward/backward for columns, raised/lowered for priorities)

Example output:
```
- Link: http://phorge.localhost/T59
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

**Negation Prefix `not:`**

Any pattern can be prefixed with `not:` to negate its meaning. This is a general negation operator that works with all pattern types. For example:

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
phabfive maniphest search --tag "My Project" --priority="in:Unbreak Now!"

# Find tasks that were ever at Unbreak Now!
phabfive maniphest search --tag "My Project" --priority="been:Unbreak Now!"

# Find tasks that were raised from Normal
phabfive maniphest search --tag "My Project" --priority="from:Normal:raised"

# Find tasks that had any priority increase
phabfive maniphest search --tag "My Project" --priority=raised
```

### Combining Column and Priority Filters

You can combine column and priority filters for powerful queries:

```bash
# Tasks that moved forward from "Up Next" AND were ever at Normal priority
phabfive maniphest search '*' \
  --column='from:Up Next:forward' \
  --priority='been:Normal'

# Tasks in Done that were raised from Normal
phabfive maniphest search --tag "My Project" \
  --column="in:Done" \
  --priority="from:Normal:raised"

# Recently completed high-priority tasks
phabfive maniphest search --tag "My Project" \
  --column="to:Done" \
  --priority="in:High" \
  --updated-after=7
```

### Priority OR/AND Logic

Same as column patterns, priority patterns support OR (comma) and AND (plus):

```bash
# Tasks at High OR Unbreak Now!
phabfive maniphest search --tag "My Project" --priority="in:High,in:Unbreak Now!"

# Tasks raised from Normal AND currently at High
phabfive maniphest search --tag "My Project" --priority="from:Normal:raised+in:High"
```

### Priority Negation Patterns

Use the `not:` prefix to negate priority patterns:

```bash
# Tasks NOT currently at High priority
phabfive maniphest search --tag "My Project" --priority="not:in:High"

# Tasks whose priority has NOT been raised
phabfive maniphest search --tag "My Project" --priority="not:raised"

# Tasks NOT at High priority AND have been raised at some point
phabfive maniphest search --tag "My Project" --priority="not:in:High+raised"

# Tasks at Normal that did NOT come from being lowered
phabfive maniphest search --tag "My Project" --priority="in:Normal+not:lowered"
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

### Status Scope: open, closed, any

A search reaches **open** tasks unless told otherwise. Three keywords say which
statuses it reaches instead, and are asked of the server rather than checked
task by task:

| Keyword | Reaches |
|---------|---------|
| `open` | Every open status (the default) |
| `closed` | Every closed status: Resolved, Wontfix, Invalid, Duplicate, and any custom closed status |
| `any` | Every status |

```bash
# Every closed task on the board
phabfive maniphest search --tag "My Project" --status=closed

# Every task, open or closed
phabfive maniphest search --tag "My Project" --status=any
```

A keyword ANDs with the transition patterns below like any other condition.
A pattern on its own keeps the open default, so it costs no history lookups
for closed tasks - which also means a pattern about a closed status needs a
scope:

```bash
# Nothing: only open tasks are reached, and none of them is Resolved
phabfive maniphest search --tag "My Project" --status="in:Resolved"

# Currently Resolved tasks
phabfive maniphest search --tag "My Project" --status="closed+in:Resolved"

# Tasks that were ever Blocked, open or closed
phabfive maniphest search --tag "My Project" --status="any+been:Blocked"
```

phabfive warns when an `in:` condition cannot match within its scope, as in
the first example. Each comma-separated group has its own scope: in
`--status="in:Open,closed+in:Resolved"` the first group still reaches open
tasks only.

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
| `open`, `closed`, `any` | Which statuses the search reaches; see [Status Scope](#status-scope-open-closed-any) | `closed+in:Resolved` |
| `not:PATTERN` | Negates any pattern above | `not:in:Open`, `not:raised` |

**Negation Prefix `not:`**

Any pattern can be prefixed with `not:` to negate its meaning. This is a general negation operator that works with all pattern types. For example:

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
phabfive maniphest search --tag "My Project" --status="in:Open"

# Find tasks that were ever Resolved, open or closed now
phabfive maniphest search --tag "My Project" --status="any+been:Resolved"

# Find tasks that progressed from Open, open or closed now
phabfive maniphest search --tag "My Project" --status="any+from:Open:raised"

# Find tasks that had any status progression
phabfive maniphest search --tag "My Project" --status=raised
```

### Combining Column, Priority, and Status Filters

You can combine all three filter types for powerful queries:

```bash
# Tasks moved to Done AND were raised from Open AND are currently Resolved
phabfive maniphest search '*' \
  --column='to:Done' \
  --priority='from:Normal:raised' \
  --status='closed+in:Resolved'

# Tasks in progress that have been blocked
phabfive maniphest search --tag "My Project" \
  --column="in:In Progress" \
  --status="been:Blocked"

# Recently completed tasks that were never blocked
phabfive maniphest search --tag "My Project" \
  --status="closed+to:Resolved+never:Blocked" \
  --updated-after=7
```

### Status OR/AND Logic

Same as column and priority patterns, status patterns support OR (comma) and AND (plus):

```bash
# Tasks currently Open OR Blocked
phabfive maniphest search --tag "My Project" --status="in:Open,in:Blocked"

# Tasks raised from Open AND currently Resolved
phabfive maniphest search --tag "My Project" --status="closed+from:Open:raised+in:Resolved"
```

### Status Negation Patterns

Use the `not:` prefix to negate status patterns:

```bash
# Tasks NOT currently Open, closed ones included
phabfive maniphest search --tag "My Project" --status="any+not:in:Open"

# Tasks whose status has NOT progressed
phabfive maniphest search --tag "My Project" --status="not:raised"

# Tasks NOT Resolved AND have been Blocked at some point
phabfive maniphest search --tag "My Project" --status="not:in:Resolved+been:Blocked"

# Tasks that progressed but did NOT reach Resolved
phabfive maniphest search --tag "My Project" --status="raised+not:in:Resolved"
```

**Note**: `not:been:STATUS` is functionally equivalent to `never:STATUS`.

## Spaces

Spaces are Phorge's namespaces: every task is in exactly one, and which one it
is decides who can see it. Phabfive names a Space by monogram (`S3`), by name
(`Archive`), or by a pattern that matches one (`*rch*`), case-insensitively.

### Searching within a Space

Every search narrows to a Space - `PHAB_SPACE`, default `S1` - even when
`--space` is not given, so tasks in other Spaces are silently excluded:

```bash
# The default Space only
phabfive maniphest search --tag '*'

# One Space, by monogram or by name
phabfive maniphest search --space S3 --tag '*'
phabfive maniphest search --space Archive --tag '*'

# Several Spaces, or all of them
phabfive maniphest search --space S1,S3 --tag '*'
phabfive maniphest search --space '*' --tag '*'
```

`-v` reports which Space was searched, which is worth reaching for when a
search returns fewer tasks than expected - see [Verbose Output](#verbose-output).

### Creating in a Space, and moving between them

`--space` on `create` places a new task, and on `edit` moves an existing one:

```bash
phabfive maniphest create "Quarterly cleanup" --space=Archive
phabfive maniphest edit T123 --space=S3
phabfive maniphest edit T123 T124 --space=Archive   # a batch in one go
```

A task is stored in exactly one Space, so unlike the filter these refuse
anything naming more than one, whether a wildcard or a name two Spaces share:

```console
$ phabfive maniphest create "Task" --space='*'
Error: Space '*' is ambiguous, it matches: S1 (Default), S3 (Management Team), S10 (Archive). Use a monogram to name one.
```

A pattern that leaves no doubt is accepted, and `edit` shows both ends of the
move before making it:

```console
$ phabfive maniphest edit T123 --space='*rch*' --dry-run
[DRY RUN] Would apply to T123:
  Space: S1 (Default) → S10 (Archive)
```

`PHAB_SPACE` is a search filter and nothing more. A create with no `--space`
lands wherever the server puts it, which is the instance's own default Space,
so set it explicitly when it matters. A create spec takes a `space` field per
task - see [Creating with Specs](create-specs.md).

Monograms and names complete with TAB, from a list kept for a day - see
[Caching](caching.md).

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

**💡 Tip**: Many of these common workflows already ship as search specs under `specs/search/`:
- `project-status-overview.yaml` - Comprehensive project health check
- `development-workflow-audit.yaml` - Development process analysis
- `blocked-tasks.yaml` - Find workflow bottlenecks

Run one with `phabfive search -f specs/search/blocked-tasks.yaml`. See
[Searching with Specs](search-specs.md) for the complete list.

### Finding Tasks That Got Stuck

Identify tasks that moved backward from completion:

```bash
phabfive maniphest search --tag "Backend Team" \
  --column="from:Done:backward" \
  --updated-after=30
```

### Tracking Fast-Tracked Tasks

Find tasks that went straight to Done without review:

```bash
phabfive maniphest search --tag "Frontend" \
  --column="in:Done+never:In Review"
```

### Monitoring Blocked Work

See what's currently blocked and where it came from:

```bash
phabfive maniphest search --tag "My Project" \
  --column="in:Blocked+from:In Progress"
```

### Quality Assurance

Find recently completed tasks that never went through testing:

```bash
phabfive maniphest search --tag "Product" \
  --column="in:Done+never:Testing" \
  --updated-after=7
```

### Sprint Retrospective

Analyze all tasks completed in the last sprint:

```bash
phabfive maniphest search --tag "Sprint 42" \
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
- Consider combining with date filters (`--created-after`, `--created-before`, `--updated-after`, `--updated-before`) to narrow results
- Use specific project names rather than wildcards when possible

### Debugging Patterns

If a filter pattern doesn't return expected results:

1. Run the search without `--column` or `--priority` to see all tasks
2. Add `--show-history` to inspect actual column and priority movements for all tasks
3. Verify column names match exactly (case-sensitive)
4. Start with simple patterns and add complexity incrementally
5. Add `-v` to see which filters were actually applied (see below)

### Verbose Output

Searches are quiet by default. `-v` reports which filters were applied,
on stderr, so stdout stays clean for `--format=json` and `--format=jsonl` consumers:

```bash
# Which Space and project(s) did this actually search?
phabfive -v maniphest search --tag backend

INFO - Filtering to space(s): S1 (from PHAB_SPACE). Tasks in other spaces are excluded; use --space='*' to include all spaces.
INFO - Filtering to open statuses: ['open']
INFO - Filtering to tag(s): backend (1 project(s))
```

This is worth reaching for when a search returns fewer tasks than expected.
Every search narrows to a Space - `PHAB_SPACE`, default `S1` - even when
`--space` is not given, so tasks in other Spaces are silently excluded.
Use `--space='*'` to search them all, and see [Spaces](#spaces) for placing a
task in one.

`-vv` adds debug detail, including API resolution steps. In the other
direction, `-q` reports only errors and `-qq` only critical failures:

| Flags    | Level    | Shows                                  |
| -------- | -------- | -------------------------------------- |
| `-qq`    | CRITICAL | Critical failures only                 |
| `-q`     | ERROR    | Errors only                            |
| *(none)* | WARNING  | Warnings and errors - the default      |
| `-v`     | INFO     | Which filters were applied             |
| `-vv`    | DEBUG    | API resolution steps, config loading   |

Repeats past either end hold there, so `-vvv` is the same as `-vv`. Opposing
flags cancel, so `-v -q` lands back on the default.

### Using Search Specs for Complex Queries

For frequently-used complex searches, write a spec and keep it in version control:

```bash
# Four searches in one file
phabfive search -f specs/search/project-status-overview.yaml

# Team members run the same file
phabfive search -f specs/search/development-workflow-audit.yaml

# Vary one value without editing the file, where the file declares it
phabfive search -f my-searches.yaml --set team=core
```

**Advantages:**
- **Reproducible**: Same results every time
- **Shareable**: Team-wide standardized searches
- **Documentable**: `metadata.description` and a per-search `description:` say what each is for
- **Multi-query**: Run several related searches, across several applications, in sequence
- **Checkable**: `phabfive spec validate --offline FILE` needs no token and no network

For details, see [Searching with Specs](search-specs.md).

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

- [Policies](policies.md) - Who can see, edit and interact with a task
- [Searching with Specs](search-specs.md) - Running one file's worth of searches with `phabfive search -f`
- [Creating with Specs](create-specs.md) - Creating one file's worth of objects with `phabfive apply -f`
- [The Phorge spec format](phorge-spec.md) - The normative definition of both
- [Development Guide](development.md) - Set up a local development environment
- [Phorge Setup](phorge-setup.md) - Run a local Phorge instance for testing
