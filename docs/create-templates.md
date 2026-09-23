# Task Creation Templates

This guide covers YAML templates for creating multiple tasks in bulk using phabfive.

## Overview

Task creation templates allow you to define multiple tasks in a single YAML file, complete with relationships, assignments, and project associations. This is particularly useful for:

- **Project initialization**: Create a standard set of tasks for new projects
- **Sprint planning**: Set up tasks with dependencies and assignments
- **Recurring workflows**: Template common task structures
- **Team onboarding**: Create standardized task sets for new team members

## Usage

Use any template with the `maniphest create` command:

```bash
# Preview tasks without creating them (recommended first step)
phabfive maniphest create --with templates/task-create/template.yaml --dry-run

# Create tasks for real
phabfive maniphest create --with templates/task-create/template.yaml

# Enable debug logging to see detailed processing
phabfive -vv maniphest create --with templates/task-create/template.yaml --dry-run
```

A creation template is a single YAML document: a `---` separator starting a second
one is an error, unlike a [search template](search-templates.md), which may hold
several. The template alone says what to create - `maniphest create` refuses the
options it cannot honour alongside `--with` rather than quietly dropping them.

## Template Structure

### Basic Template Format

```yaml
# Optional: Define variables for reuse throughout the template
variables:
  project_name: "My Project"
  sprint_number: 42
  assignee: "username"

# Define the tasks to create
tasks:
  - title: "Task Title {{ sprint_number }}"
    description: |
      Task description with Jinja2 templating support.
      Variables can be used: {{ project_name }}
    projects:
      - "{{ project_name }}"
    priority: "normal"
    assignment: "{{ assignee }}"
```

### Supported Task Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `title` | string | Task title (supports Jinja2 variables) | `"Fix bug in {{ component }}"` |
| `description` | string | Task description (supports Jinja2 variables) | Multi-line YAML string |
| `projects` | list | Project names or hashtags, written without the `#` | `["Backend Team", "sprint_42"]` |
| `space` | string | Space to create the task in, by monogram, name, or a pattern matching one | `"S3"`, `"Archive"` |
| `priority` | string | Task priority | `"high"`, `"normal"`, `"low"`, etc. |
| `assignment` | string | Assignee: a username, `@username`, `@me` or a user PHID (supports Jinja2 variables) | `"alice"`, `"@me"` |
| `subscribers` | list | Subscribers, each spelled as for `assignment` (supports Jinja2 variables) | `["bob", "@carol", "PHID-USER-..."]` |
| `status` | string | Status key or name, e.g. `open` or `resolved` | `"resolved"` |
| `column` | string | Workboard column to place it in; needs `projects:` naming the board | `"In Review"` |
| `visible-to` | string | View policy: a keyword, `#project`, `@user`, a PHID or a `$local-id` | `"#backend"` |
| `editable-by` | string | Edit policy, spelled as `visible-to` | `"admin"` |
| `parent` | string | One parent, the singular of `parents` | `"T123"`, `"$epic"` |
| `parents` | list | Parent task IDs | `["T123", "T456"]` |
| `subtasks` | list | Subtask IDs to attach | `["T789"]` |
| `id` | string | A name the rest of this file can refer to this task by; see [Referring to what the same file creates](#referring-to-what-the-same-file-creates) | `"epic"` |
| `tasks` | list | Nested subtasks (see [Subtasks](#subtasks)) | Array of task objects |

Only `title` is required. A task with a title and no description is created;
an item with neither a title nor nested `tasks:` nor a `parent:` is an error,
and the whole file is refused rather than that one item being skipped.

### Spaces

A task can name the Space it should be created in, by monogram, by name, or by
a pattern that matches exactly one - the same values `maniphest create --space`
takes, and refused the same way if more than one Space matches:

```yaml
variables:
  where: "Archive"

tasks:
  - title: "Quarterly cleanup"
    description: "Filed out of the way"
    space: "{{ where }}"

    tasks:
      - title: "Collect the old plans"
        description: "Nested tasks name their own Space"
        space: "S10"
```

Without a `space` field a task lands wherever the server puts it, which is the
instance's default Space; `PHAB_SPACE` narrows searches and is not consulted
here. Every Space named is resolved once, however many tasks name it.

### Users

`assignment` and `subscribers` take users the way every phabfive option that
takes a user does: a username, with or without a leading `@`, `@me` for whoever
runs the command, or a user PHID. Usernames match in any case.

```yaml
variables:
  lead: "alice"

tasks:
  - title: "Plan the release"
    description: "Owned by the lead, watched by the rest"
    assignment: "{{ lead }}"
    subscribers: ["@bob", "@me", "PHID-USER-abcdefghijklmnopqrst"]
```

Every user the template names is looked up before any task is created, so an
unknown one - named in the error, with any others - leaves nothing created.
`--dry-run` shows each task's assignee and subscribers under its title.

### Jinja2 Variable Support

Templates support Jinja2 templating for dynamic content:

```yaml
variables:
  team: "Backend"
  quarter: "Q1 2024"
  epic_id: "T100"

tasks:
  - title: "[{{ team }}] {{ quarter }} Planning"
    description: |
      Planning task for {{ team }} team in {{ quarter }}.

      Related to epic: {{ epic_id }}
    projects:
      - "{{ team }} Team"
      - "{{ quarter }}"
```

#### Defaults, and undefined variables

A `{{ name }}` that names nothing is an **error**, in the `variables:` section
and in a task's own fields alike, and the message names the variable:

```yaml
variables:
  team: "Backend"
  board: "{{ tema }} board"   # Undefined variable 'tema'
```

```yaml
tasks:
  - title: "Sprint {{ sprint_numbr }} planning"   # Undefined variable 'sprint_numbr'
```

Jinja2's own default is to render an undefined name as the empty string, so
`{{ tema }}` used to disappear silently and the task was created as
"Sprint  planning" rather than refused. Where a value really is optional, say
so — either with Jinja2's
`default` filter at the point of use:

```yaml
variables:
  board: "{{ team | default('Backend') }} board"
```

or by declaring a default for the variable itself, which is a mapping with a
single `default` key:

```yaml
variables:
  sprint: {default: 42}       # a declaration - {{ sprint }} renders "42"
  owners: {lead: "alice"}     # any other mapping is an ordinary value
```

A variable declared with nothing after it has no value and no default, so it
has to be supplied from outside the template and is an error until it is:

```yaml
variables:
  sprint:                     # Variable 'sprint' has no value
```

## Advanced Features

### Subtasks

Create hierarchical task structures with nested subtasks:

```yaml
tasks:
  - title: "Epic: User Authentication System"
    description: "Main epic for authentication feature"
    projects: ["Backend Team"]
    priority: "high"
    tasks:  # Nested subtasks
      - title: "Design authentication API"
        description: "Design the API endpoints for authentication"
        projects: ["Backend Team"]  # Named again, since nothing is inherited
        priority: "normal"
        assignment: "api-designer"

      - title: "Implement JWT tokens"
        description: "Implement JWT token generation and validation"
        priority: "normal"
        # No projects: of its own, so this subtask lands in no project
```

**Subtask Behavior:**
- A subtask names its own projects. Nothing is inherited from the parent, so a
  subtask without a `projects:` of its own is created in no project at all
- Every other field is the subtask's own too, and defaults the same way a
  top-level task's does
- Subtasks are created after their parent and automatically linked
- Nesting can be multiple levels deep

### Task Relationships

`parents` and `subtasks` link a task to tasks that already exist on the
instance, each named by its monogram:

```yaml
tasks:
  - title: "Setup database schema"
    description: "Create initial database tables"
    projects: ["Backend"]

  - title: "Implement user model"
    description: "Create user model and validation"
    projects: ["Backend"]
    parents: ["T123"]  # T123 already exists; this task becomes its subtask

  - title: "Add user authentication"
    description: "Implement login/logout functionality"
    projects: ["Backend"]
    # Nothing links this to the task above it: tasks listed side by side in a
    # template are unrelated. Nest them under `tasks:` to link them
```

Nesting links tasks the same template creates - see [Subtasks](#subtasks) -
and `$local-id` links anything to anything; see the next section. A monogram
in `parents` or `subtasks` that no task answers to is an error, and nothing
is created.

#### Hanging new subtasks off a task that already exists

An item with a `parent:` (or `parents:`) and **no `title:`** creates nothing.
It is an *anchor*: it is read for the PHID of what it names, and the tasks
nested under it are created as children of that task.

```yaml
tasks:
  - parent: T123          # already exists; read, never written
    tasks:
      - title: "Subtask A"
      - title: "Subtask B"
```

T123 itself is never edited. Each new task carries a `parents.add` naming it,
so **the subtasks T123 already had are kept**. No create ever rewrites a
collection on an object that already exists: every list field is sent as
`.add`, never as a replacement, and the whole create path refuses to send a
`.set` at all.

The flat form is exactly equivalent, and is what to write when the new tasks
differ from one another:

```yaml
tasks:
  - title: "Subtask A"
    parents: ["T123"]
  - title: "Subtask B"
    parents: ["T123"]
```

### Referring to what the same file creates

`$name` refers to an object **this file creates**, which is how one file can
create a project and the tasks tagged into it. Give the object an `id:` and
write `$id` wherever a reference goes:

```yaml
kind: create

projects:
  - id: platform
    name: "Platform"
    slugs: ["platform"]

tasks:
  - id: epic
    title: "Bootstrap the platform"
    projects: ["$platform"]

  - title: "Write the runbook"
    projects: ["$platform"]
    parents: ["$epic"]
```

The objects are created in the order that makes each `$ref` resolvable -
the project first, then the epic, then the task that hangs off it - whatever
order they are written in. Ties are broken by the order in the file.

`$name` is not Jinja. `{{ ... }}` is substituted before anything is created;
`$name` is substituted **during** the run, as each object comes into
existence. `$` is used rather than `@` or `#` because those already mean a
user and a project.

What a `$ref` may name is checked before anything is sent, with no network:

| Reported | Cause |
|----------|-------|
| `unknown-local-id` | `$platfrom` - nothing in the file declares that `id:` |
| `duplicate-local-id` | two objects declare the same `id:` |
| `local-id-cycle` | `$a` names `$b` and `$b` names `$a`, so neither can be first |
| `not-creatable` | `$p` names a project and it was written in `parents:`, which names a task - or it names an item that creates nothing |

### Creating projects

A create file may hold a `projects:` section as well as `tasks:`. A project
is named by `name:` and takes `description`, `icon`, `color`, `slugs`,
`members`, `parent`, `milestone-of`, `space`, `visible-to`, `editable-by` and
`joinable-by` - the same keys `phabfive project create` has flags for.

```yaml
kind: create

projects:
  - id: platform
    name: "Platform"
    slugs: ["platform"]
    members: ["alice", "@bob"]
  - name: "Sprint 1"
    milestone-of: "$platform"
```

**A project cannot be deleted or archived through Conduit.** A name whose
hashtag an existing project already holds is therefore refused before the
first object is created, not by the server halfway through. Preview with
`--dry-run` first.

`passphrases:` is recognised and always refused: Phorge exposes
`passphrase.query` and no `passphrase.edit`, so nothing can create a
credential from a file. Passphrase *search* specs are supported.

### When something fails partway through

Conduit has no transactions, so a file whose fourth object is refused has
already created the three before it. The command says so per object and
exits 1:

```
Error: 3 of 5 objects created. task 'Wire it up' failed: ERR-CONDUIT-CORE: ...
  created  project $platform 'Platform' #platform
  created  task T408 'Sprint kickoff'
  created  task T409 'Bootstrap'
  failed   task 'Wire it up': ERR-CONDUIT-CORE: ...
  skipped  task 'Follow-up'
```

With `--format=json`, `--format=jsonl` or `--format=yaml` the records go to
stdout and the sentence to stderr, so a reader piping stdout into `jq` sees
records and nothing else. `skipped` means nothing was sent for it and nothing
exists - which is a different thing from having been tried and refused.

### YAML Anchors and References

Use YAML features for reusable content:

```yaml
variables:
  # Define reusable content with YAML anchors
  backend_team: &backend_projects
    - "Backend Team"
    - "Current Sprint"

  common_subscribers: &team_leads
    - "tech-lead"
    - "product-manager"

tasks:
  - title: "API Endpoint Development"
    description: "Develop REST API endpoints"
    projects: *backend_projects  # Reference the anchor
    subscribers: *team_leads

  - title: "Database Migration"
    description: "Update database schema"
    projects: *backend_projects  # Reuse the same project list
    subscribers: *team_leads
```

## Examples

### Simple Project Setup

```yaml
# templates/task-create/project-setup.yaml
variables:
  project_name: "Mobile App Redesign"
  tech_lead: "alice"

tasks:
  - title: "[EPIC] {{ project_name }}"
    description: |
      Main epic for {{ project_name }} project.

      This epic tracks the overall progress of the redesign initiative.
    projects:
      - "{{ project_name }}"
      - "Design Team"
    priority: "high"
    assignment: "{{ tech_lead }}"

  - title: "User Research and Analysis"
    description: "Conduct user interviews and analyze current app usage"
    projects: ["{{ project_name }}", "UX Research"]
    priority: "high"

  - title: "Design System Update"
    description: "Update design system for new visual direction"
    projects: ["{{ project_name }}", "Design Team"]
    priority: "normal"
```

### Sprint Planning Template

```yaml
# templates/task-create/sprint-planning.yaml
variables:
  sprint: "Sprint 15"
  team: "Backend Team"

tasks:
  - title: "[PLANNING] {{ sprint }} Planning"
    description: |
      Planning session for {{ sprint }}

      - Review backlog
      - Estimate stories
      - Assign tasks
    projects: ["{{ team }}", "{{ sprint }}"]
    priority: "high"
    tasks:
      - title: "Backlog refinement"
        description: "Review and refine backlog items"
        assignment: "product-owner"

      - title: "Capacity planning"
        description: "Determine team capacity for sprint"
        assignment: "scrum-master"

      - title: "Task assignment"
        description: "Assign tasks to team members"
        priority: "normal"
```

### Complex Workflow with Dependencies

```yaml
# templates/task-create/feature-development.yaml
variables:
  feature: "User Notifications"
  backend_dev: "bob"
  frontend_dev: "carol"
  qa_engineer: "dave"

tasks:
  - title: "[EPIC] {{ feature }} Implementation"
    description: "Epic tracking {{ feature }} development"
    projects: ["Notifications Team"]
    priority: "high"
    subscribers: ["{{ backend_dev }}", "{{ frontend_dev }}", "{{ qa_engineer }}"]

    tasks:
      - title: "Backend API for {{ feature }}"
        description: "Implement backend API endpoints for notifications"
        assignment: "{{ backend_dev }}"
        priority: "high"

        tasks:
          - title: "Database schema for notifications"
            description: "Design and implement notification tables"
            assignment: "{{ backend_dev }}"
            priority: "high"

          - title: "Notification delivery service"
            description: "Implement service for sending notifications"
            assignment: "{{ backend_dev }}"
            priority: "normal"

      - title: "Frontend UI for {{ feature }}"
        description: "Implement notification UI components"
        assignment: "{{ frontend_dev }}"
        priority: "normal"

      - title: "QA Testing for {{ feature }}"
        description: "Test notification functionality end-to-end"
        assignment: "{{ qa_engineer }}"
        priority: "normal"
```

## Available Templates

### test-template.yaml
Simple example showing basic task creation with variables and project assignment.

**Use case**: Learning template syntax and testing basic functionality.

```bash
phabfive maniphest create --with templates/task-create/test-template.yaml --dry-run
```

### test-template-v2.yml
Advanced example demonstrating:
- Hierarchical task structures (epics with subtasks)
- Task relationships and dependencies
- User assignments and subscribers
- Multiple project associations
- Priority settings

**Use case**: Complex project setup with multiple related tasks.

```bash
phabfive -vv maniphest create --with templates/task-create/test-template-v2.yml --dry-run
```

## Requirements and Setup

### Phabricator/Phorge Setup

Before using task creation templates, ensure your Phabricator instance has:

1. **Required Projects**: All projects referenced in templates must exist
2. **User Accounts**: All users mentioned in assignments/subscribers must exist
3. **API Token**: Valid API token with task creation permissions
4. **Existing Tasks**: Any parent/subtask references (like `T123`) must exist

### Template Testing

Always test templates before creating real tasks:

```bash
# 1. Start with dry-run to see what would be created
phabfive maniphest create --with templates/task-create/your-template.yaml --dry-run

# 2. Enable debug logging for detailed information
phabfive -vv maniphest create --with templates/task-create/your-template.yaml --dry-run

# 3. Create tasks only after verification
phabfive maniphest create --with templates/task-create/your-template.yaml
```

### Error Handling

Common errors and solutions:

| Error | Cause | Solution |
|-------|-------|----------|
| "Project 'X' not found" | Project doesn't exist | Create project in Phabricator first |
| "No such user: 'X'" | A username or PHID in `assignment` or `subscribers` that is not a user | Verify the user exists; every unknown one is named, and no task is created |
| "No such user: 'me'" | `@me` always means you; a bare `me` is the username | Write `@me` for yourself, or `me` for the account of that name |
| "Task 'T123' not found" | Invalid task reference | Check task ID exists |
| "No object in this spec is called $X" | A `$ref` to an `id:` nothing declares | Declare the `id:`, or fix the spelling |
| "$p is declared by projects[0], which creates a project, and parents: names a task" | A `$ref` of the wrong kind | Use a key that takes that kind of object |
| "No workboard column called 'X'" | `column:` names a column none of the task's `projects:` has | Check the board's columns, or drop `column:` |
| "passphrases cannot be created" | A `passphrases:` section | Credentials must be created in the web UI |
| "Permission denied" | Insufficient API permissions | Update API token permissions |
| "Undefined variable 'X'" | A `variables:` entry names a variable nothing defines - usually a typo | Define it, or write `{{ X \| default("...") }}` to allow it to be missing |
| "Variable 'X' has no value" | `X:` is declared with neither a value nor a `default:` | Give it a value, or declare `X: {default: ...}` |

## Best Practices

### Template Organization

- **Use descriptive names**: `sprint-setup.yaml`, not `template1.yaml`
- **Include documentation**: Add comments explaining template purpose
- **Version templates**: Use descriptive filenames like `onboarding-v2.yaml`
- **Test thoroughly**: Always use `--dry-run` first

### Variable Usage

- **Consistent naming**: Use clear variable names like `team_name`, not `t`
- **Default values**: Consider providing sensible defaults
- **Documentation**: Comment complex variable usage

### Project Structure

```yaml
# Good: Clear structure with documentation
variables:
  # Project configuration
  project_name: "Feature Development"
  team: "Backend Team"

  # Team assignments
  tech_lead: "alice"
  developer: "bob"

tasks:
  # Epic level tasks
  - title: "[EPIC] {{ project_name }}"
    # ... epic configuration

    tasks:
      # Feature development tasks
      - title: "API Development"
        # ... task configuration
```

### Error Prevention

- **Validate references**: Ensure all referenced projects/users exist
- **Use dry-run**: Always test before creating real tasks
- **Start simple**: Begin with basic templates and add complexity gradually
- **Check permissions**: Verify API token has necessary permissions

## Creating Your Own Templates

### Template Development Workflow

1. **Start with existing template**: Copy and modify an existing template
2. **Define variables**: Identify reusable values
3. **Structure tasks**: Organize tasks logically (epics → features → implementation)
4. **Test incrementally**: Use `--dry-run` to test each change
5. **Document usage**: Add comments explaining template purpose and requirements

### Template Naming Conventions

- **Purpose-based**: `project-setup.yaml`, `sprint-planning.yaml`
- **Team-specific**: `backend-deployment.yaml`, `qa-testing.yaml`
- **Workflow-based**: `feature-development.yaml`, `bug-fix-workflow.yaml`

### Sharing Templates

Templates can be:
- **Committed to version control**: Share across team via git repository
- **Stored centrally**: Place in shared directory for team access
- **Documented in wiki**: Link to templates from project documentation

## See Also

- [Maniphest CLI](maniphest-cli.md) - Complete CLI command reference
- [Search Templates](search-templates.md) - YAML templates for task searching
- [Development Guide](development.md) - Set up development environment
- [Phorge Setup](phorge-setup.md) - Run local Phorge instance for testing
