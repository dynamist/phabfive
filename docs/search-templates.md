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
  status: "in:Open"
  column: "in:In Progress"
  priority: "in:High"
  created-after: 7
  updated-after: 7
  show-history: true
  show-metadata: false

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
  status: "in:Resolved"
  updated-after: 7

---
# Add more searches with --- separators
```

## Supported Parameters

- `text_query`: Free-text search in task title/description
- `tag`: Project/workboard filtering with wildcards and logic
- `created-after`: Tasks created within N days
- `updated-after`: Tasks updated within N days
- `column`: Column transition patterns
- `priority`: Priority transition patterns
- `status`: Status transition patterns
- `show-history`: Display transition history (true/false)
- `show-metadata`: Display filter match metadata (true/false)

## Creating Your Own Templates

1. Create a new `.yaml` file in this directory
2. Add a `search:` section with your desired parameters
3. Optionally add a `description:` section for documentation
4. Test with `phabfive maniphest search --with your-template.yaml`

## See Also

- [Task Creation Templates](create-templates.md) - YAML templates for creating multiple tasks in bulk
- [Maniphest CLI](maniphest-cli.md) - Complete Maniphest CLI documentation