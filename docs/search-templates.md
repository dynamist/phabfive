# Search Templates

This directory contains example YAML templates for common search patterns in phabfive.

## Usage

Use any template with the `--with` option:

```bash
phabfive maniphest search --with path/to/template.yaml
```

You can override any parameter from the command line:

```bash
phabfive maniphest search --with template.yaml --tag "different-project"
```

## Multi-Document YAML Support

Templates can contain multiple search configurations in a single file using YAML document separators (`---`). Each search will be executed sequentially with clear separators between results:

```bash
phabfive maniphest search --with templates/task-search/project-status-overview.yaml
```

This is perfect for comprehensive reports that need multiple related searches.

## Available Templates

### tasks-resolved-but-not-in-done.yaml
Find tasks marked as resolved but still in active workboard columns. Useful for identifying tasks that need proper closure.

### high-priority-stale-tasks.yaml
Find high-priority tasks that haven't been updated recently. Helps ensure important work doesn't stall.

### recently-moved-to-review.yaml
Find tasks recently moved to review columns. Useful for reviewers to track what needs attention.

### escalated-priorities.yaml
Find tasks that had their priority raised recently. Helps track escalations and priority trends.

### blocked-tasks.yaml
Find tasks that are blocked or have been in blocking states. Useful for identifying workflow bottlenecks.

### project-status-overview.yaml
**Multi-document template** - Comprehensive project status report including high priority tasks, recently resolved work, blocked tasks, and priority escalations.

### development-workflow-audit.yaml
**Multi-document template** - Development workflow analysis including review queue, fast-moving tasks, backward movements, and cleanup needed.

### team-productivity-report.yaml
**Multi-document template** - Team productivity metrics including work created, completed, high priority distribution, and long-running tasks.

## Template Structure

### Single Document Templates

```yaml
search:
  # Any search parameters supported by phabfive maniphest search
  tag: "project-name"
  author: "@me"        # task author; username, @me, or "@me,alice" for OR
  assigned: "@me"      # task assignee; same value syntax as author
  status: "in:Open"
  column: "in:In Progress"
  priority: "in:High"
  order: "updated:asc"
  created-after: "1w"  # Time units: h, d, w, m, y (or use numbers for days)
  updated-after: "1w"
  show-history: true
  show-metadata: false
  show-policy: false

# Optional description for documentation
description: |
  Description of what this template does and when to use it.
```

### Multi-Document Templates

```yaml
title: "First Search Name"
description: "What this search does"
search:
  tag: "project-name"
  status: "in:Open"

---
title: "Second Search Name"
description: "What this search does"
search:
  tag: "project-name"
  status: "closed+in:Resolved"
  updated-after: "1w"  # Can also use plain numbers: 7 (defaults to days)

---
# Add more searches with --- separators
```

## Supported Parameters

- `text_query`: Free-text search in task title/description
- `tag`: Project/workboard filtering with wildcards and logic
- `include`: Task ID(s) to force-include in results even if other filters don't match them, as a comma-separated string (`"T123,T456"`) or a YAML list (`["T123", "T456"]`)
- `exclude`: Task ID(s) to remove from results even if the filters match them, as a comma-separated string (`"T123"`) or a YAML list (`["T123"]`)
- `assigned`: Assignee: a username, `"@me"`, or a user PHID
- `author`: Author: a username, `"@me"`, or a user PHID
- `space`: Space name or monogram, with wildcards (`"*"` for every Space)
- `visible-to`, `editable-by`: Only tasks whose view or edit policy is exactly this,
  in the [policy grammar](policies.md#the-value-grammar), e.g. `editable-by: users`
- `created-after`: Tasks created within TIME (e.g., `"1w"`, `"2m"`, or `7` for days)
- `created-before`: Tasks created more than TIME ago (e.g., `"1w"`, `"2m"`, or `7` for days)
- `updated-after`: Tasks updated within TIME (e.g., `"1w"`, `"2m"`, or `7` for days)
- `updated-before`: Tasks updated more than TIME ago (e.g., `"1w"`, `"2m"`, or `7` for days)
- `order`: Result ordering as `<field>[:asc|:desc]`, e.g. `"priority"`,
  `"updated:asc"`, `"title"`. Defaults to `"priority"`. See
  [Result Ordering](maniphest-cli.md#result-ordering)
- `limit`: Maximum results, `0` for all (default `100`)
- `column`: Column transition patterns
- `priority`: Priority transition patterns
- `status`: Status scope (`open`, `closed`, `any`) and transition patterns, e.g.
  `"closed+in:Resolved"`. See [Status Scope](maniphest-cli.md#status-scope-open-closed-any)
- `all`: Deprecated, use `status: any`. Still honoured, with a warning
- `show-history`: Display transition history (true/false)
- `show-metadata`: Display filter match metadata (true/false)
- `show-policy`: Display each task's policies (true/false). Off by default: naming a
  policy that points at a project or a user costs one `phid.query` for the page
- `ids`: Only these tasks, with every other filter still applied, as a comma-separated
  string (`"T123,T456"`) or a YAML list. Unlike `include`, the filters still decide
- `phids`: Only these task PHIDs, with every other filter still applied
- `subscriber`: Tasks a user is subscribed to: a username, `"@me"`, or a user PHID
- `subtype`: Task subtype key, e.g. `default` or one this instance defines in
  `maniphest.subtypes`
- `parent`: Only the subtasks of these tasks (`"T123"` or a YAML list)
- `subtask`: Only the parents of these tasks (`"T123"` or a YAML list)
- `has-parents`: Only tasks that are a subtask of something (true/false)
- `has-subtasks`: Only tasks that have subtasks (true/false)
- `closed-by`: Tasks closed by a user: a username, `"@me"`, or a user PHID
- `closed-after`: Tasks closed within TIME (e.g., `"1w"`, `"2m"`, or `7` for days)
- `closed-before`: Tasks closed more than TIME ago (e.g., `"1w"`, `"2m"`, or `7` for days)

**Time Unit Support:**
All date filters support time units: `h` (hours), `d` (days), `w` (weeks), `m` (months), `y` (years).
Examples: `"12h"`, `"1w"`, `"2m"`, `"1y"`. Plain numbers default to days.

## Searching Other Objects

A template is a **search spec**, and every item in one says what it searches:

```yaml
kind: search
searches:
  - type: task
    search: {column: "in:Blocked"}
  - type: project
    search: {status: active, members: ["@me"]}
  - type: paste
    search: {author: "@me"}
  - type: passphrase
    search: {type: key}
```

`type:` defaults to `task`, so every template written before this existed keeps
meaning exactly what it meant. A document holding several items runs them in the
order they are written, each through the app that searches that object, and all of
them share one configuration and one connection.

Each search command reads a spec with the same `--with`, and the command's own
options override what the spec says:

```bash
phabfive project search --with searches.yaml
phabfive paste search --with searches.yaml
phabfive passphrase search --with searches.yaml
```

Which of the three you start from decides nothing but where the first connection
comes from: `phabfive project search --with mixed.yaml` runs the paste and
passphrase items in that file too.

`phabfive maniphest search --with` reads a template of task searches, as it
always has. It has not moved to the shared ingestion point yet, so it is not the
command to run a document naming another `type:` from - use one of the three
above. It does not guess, either: an item whose `type:` is not `task` is refused
by name, with the command that runs it:

```console
$ phabfive maniphest search --with mixed.yaml
ERROR: Search 1: 'maniphest search' runs a task search, and this one is a
'paste' search. Run the spec from 'phabfive paste search --with' instead,
which runs every type a spec holds.
```

Every key below is declared once, in `phabfive/spec/registry.py`, and the key
set of each of the four object types is **complete**: a key that is not listed
is an error naming the key rather than a filter that quietly does not happen.

### project

- `text_query`: Free text, matched the way the web UI's search box matches it
- `members`: Projects any of these users is a member of (`"@me"`, a username or a
  user PHID; a list, or comma-separated)
- `parents`: Direct subprojects and milestones of these projects
- `ancestors`: Projects anywhere beneath these projects
- `milestones`: `true` for only milestones, `false` for no milestones, absent for both
- `status`: `active` (the default), `archived`, or `any`
- `icons`: Projects with any of these icons
- `colors`: Projects of any of these colours; a milestone has its parent's
- `spaces`: Space names, monograms or patterns; `"*"` for every Space. Absent means
  `PHAB_SPACE`
- `show-policy`: Display each project's policies (true/false)
- `show-members`: Display each project's members (true/false)
- `limit`: Maximum results, `0` for all (default `100`)

A project search with no filter at all is a request rather than a mistake: it lists
every active project in `PHAB_SPACE`.

### paste

- `text_query`: Free text, matched against the paste title
- `author`: A username, `"@me"`, or a user PHID
- `limit`: Maximum results, `0` for all (default `100`)

The author filter is sent as Phorge's `authors` constraint, which is what
`paste.search` calls it - `maniphest.search` calls the same filter `authorPHIDs`,
and the wrong one fails with `ERR-INVALID-CONSTRAINT`.

### passphrase

- `text_query`: Part of a credential's name, matched case-insensitively
- `type`: `password`, `token`, `key`, `ssh` or `note`
- `limit`: Maximum matching credentials, `0` for all (default `100`)

**What a passphrase search costs.** Phorge publishes no `passphrase.search`: the
only method is the legacy `passphrase.query`, and it takes no constraints at all.
So both filters above are applied by phabfive, in Python, over **every credential
the token can see** - a search reads the whole list and tests each entry, whatever
you filtered on. `limit` is counted locally for the same reason: sent to the
server it would truncate the list before a single credential had been tested, so
`--limit 2 --type key` could report none of the keys that exist. Reading stops as
soon as enough have matched, which shortens the walk but does not make the query
cheap - on a large instance, expect a passphrase search to cost roughly what
listing every credential costs.

A search spec also never fetches secret material. `--show-secret` cannot be
combined with `--with`, because the walk above would fetch the secret of every
credential on the instance rather than of the ones that matched; read one secret
with `phabfive passphrase show K1` instead.

## Creating Your Own Templates

1. Create a new `.yaml` file in this directory
2. Add a `search:` section with your desired parameters
3. Optionally add a `description:` section for documentation
4. Test with `phabfive maniphest search --with your-template.yaml`

## See Also

- [Task Creation Templates](create-templates.md) - YAML templates for creating multiple tasks in bulk
- [Maniphest CLI](maniphest-cli.md) - Complete Maniphest CLI documentation
