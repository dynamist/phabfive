# Project CLI

`phabfive project` reads and writes Phorge projects: the tags tasks are filed
under, their members, their policies, and the subprojects and milestones beneath
them.

| Command | Does |
| --- | --- |
| `project show` | One or more projects in full |
| `project search` | Projects matching filters, as the same records |
| `project create` | A project, a subproject or a milestone |
| `project edit` | A project's name, description, icon, color, hashtags, members, Space and policies |

## Naming a project

Every command that takes a project takes any of these:

| Written as | Means |
| --- | --- |
| `'#development'` | The project with that hashtag |
| `development` | The same, without the `#` |
| `12` | The project with that ID |
| `PHID-PROJ-...` | That project |
| `'Human Resources'` | The one project with exactly that name |

Quote a hashtag: an unquoted `#` starts a comment in the shell.

A name is tried last, and only an exact match counts. Names are not unique, and
milestones are the usual case. Every team's first milestone is called something
like "Sprint 1", and a milestone has no hashtag. A name two projects share is
refused, and the error gives the ID of each:

```bash
phabfive project show 'Sprint 1'
```

```
ERROR - Project name 'Sprint 1' is ambiguous, it matches: Sprint 1 (Development), ID 13; Sprint 1 (QA), ID 15. Use the project ID or PHID instead.
```

There is no monogram shortcut for projects. `P` is Paste, and a hashtag would
need quoting anyway.

## Showing a project

```bash
phabfive project show '#development' --show-members
```

```
- Link: http://phorge.localhost/project/view/12/
  Project:
    Name: Development
    Hashtag: '#development'
    Status: active
    Icon: project
    Color: blue
    Parent:
    Milestone:
    Description: Development tools and environment setup
  Space: S1 Default
  Members:
    - Username: admin
      Name: Administrator
      Roles:
        - admin
        - verified
        - approved
        - activated
    - Username: mikael.wallin
      Name: Mikael Wallin
      Roles:
        - verified
        - approved
        - activated
```

The `Project` section has the same keys on every project, milestones included.
A milestone has a `Milestone` number and a `Parent`, and its `Hashtag` is empty.
A root project has no `Parent`. So a table has the same columns on every row, and
a script can read a key without testing for it first.

The optional sections are:

| Flag | Section |
| --- | --- |
| `--show-policy`, `-P` | `Policy`: `Visible To`, `Editable By`, `Joinable By`. See [Policies](policies.md) |
| `--show-members` | `Members`: `Username`, `Name` and `Roles` of each member |
| `--show-metadata`, `-M` | `Metadata`: PHID, ID, parent and Space PHIDs, timestamps |
| `--no-description`, `-n` | Leaves the description out |

`-M` means `--show-metadata` here, as it does on `diffusion repo show`, so
`--show-members` has no short form.

### Members and their roles

`Roles` comes straight from Phorge, unchanged: `admin`, `bot`, `disabled`,
`verified`, `approved`, `activated` and so on. That is what lets a script tell a
bot account from a person. For example, to list every member of `#humans` who is
not a bot:

```bash
phabfive --format=json project show '#humans' --show-members \
  | jq -r '.[0].Members[] | select(.Roles | index("bot") | not) | .Username'
```

Naming the members costs one `user.search` for all of them, and it is only made
when `--show-members` is given.

## Searching projects

```bash
phabfive --format=table project search --parent='#development'
```

```
Name      Status  Icon       Color  Parent       Milestone  Space
Sprint 1  active  milestone  blue   Development  1          S1 Default
```

With no filters it lists every active project in `PHAB_SPACE`, sorted by name.
Subprojects and milestones are included.

| Option | Keeps projects |
| --- | --- |
| `QUERY` (argument) | Matching the text, the way the web UI's search box matches it |
| `--member` | That any of these users is a member of: `@user`, `user` or `@me` |
| `--parent` | Directly beneath this project |
| `--ancestor` | Anywhere beneath this project |
| `--milestones` / `--no-milestones` | That are, or are not, milestones |
| `--status` | `active` (the default), `archived`, or `any` |
| `--icon`, `--color` | With any of these icons or colors |
| `--space` | In these Spaces; `'*'` for all of them. Defaults to `PHAB_SPACE` |
| `--limit`, `-l` | This many at most; `0` for all. Defaults to 100 |

`--status=any` is the same word `maniphest search` uses. `--all` is a
deprecated alias for it and prints a warning on stderr.

### Icons and colors

`--icon` and `--color` match the icon and color Phorge shows, which for a
milestone is not what is stored on it. Phorge shows every milestone with the
`milestone` icon and its parent's color, so that is what phabfive matches:

```bash
# Every milestone
phabfive project search --icon=milestone

# Green projects, and the milestones of green projects
phabfive project search --color=green
```

Phorge's own `project.search` matches the values stored on a milestone
instead, and those are hidden: `--color=green` there would miss a milestone
shown in green and find one shown in some other color. phabfive asks the server
only about projects that are not milestones, and matches milestones itself
by their parent. So a search by icon or color is two or three requests rather
than one.

Phorge also shows an archived project as `disabled`, whatever color it has.
`--color` matches the color it was given, so `--status=archived --color=red`
finds the archived red projects, and their milestones take that color too.
`disabled` is not a color you can ask for.

The colors are fixed in Phorge's code: `projects.colors` can relabel one, but not
add one. So an unknown color is refused, with the list of valid ones. Icons are
instance configuration (`projects.icons`), and no Conduit method lists them, so
an unknown icon cannot be told from a custom one: it simply matches nothing.

`project search` looks in `PHAB_SPACE` unless `--space` says otherwise, the same
as `maniphest search`. A project that has no Space of its own belongs to the
default Space, so it is still found.

### Auditing every project

This lists every project on the instance with its policies, one JSON object per
line:

```bash
phabfive --format=jsonl project search --status=any --space='*' --show-policy -l 0
```

- `--space='*'` sends no Space constraint at all.
- `-l 0` follows every page.
- The policies are named with one extra lookup for the whole listing, however
  many projects there are.
- Each line has a `Link`, so every record points back to its project.

```bash
# Every project anyone can see without logging in
phabfive --format=jsonl project search --status=any --space='*' --show-policy -l 0 \
  | jq -c 'select(.Policy["Visible To"] == "Public (No Login Required)") | .Project.Name'
```

## Creating a project

```bash
phabfive project create "Platform" --icon=infrastructure --color=blue \
    --member=@me,@viola.larsson --joinable-by=admin --dry-run
```

```
[DRY RUN] Would create Platform:
  Name: Platform
  Icon: infrastructure
  Color: blue
  Members: @admin, @viola.larsson
  Joinable By: Administrators
```

| Option | Sets |
| --- | --- |
| `--description` | The description |
| `--icon`, `--color` | The icon and color. Tab completion offers them; an unknown color is refused before anything is sent, and the server checks the icon |
| `--slug` | Additional hashtags, besides the one Phorge derives from the name |
| `--member` | Members |
| `--space` | The Space to create it in |
| `--visible-to`, `--editable-by`, `--joinable-by` | Its policies. See [Policies](policies.md) |
| `--parent` | Makes it a subproject of this project |
| `--milestone-of` | Makes it a milestone of this project |

Phorge derives a project's hashtag from its name and refuses a second project
with the same hashtag. phabfive checks this before sending anything, so a dry run
reports the clash:

```
ERROR: Project name 'Development' generates the same hashtag as #development (Development). Choose a unique name.
```

A project cannot be deleted or archived through Conduit, so preview with
`--dry-run` first.

### Subprojects and milestones

```bash
phabfive project create "Backend" --parent='#platform'
phabfive project create "Sprint 2" --milestone-of='#development' --dry-run
```

```
[DRY RUN] Would create Sprint 2:
  Name: Sprint 2
  Milestone Of: #development
```

Phorge numbers milestones within their parent. Any number of milestones may share
a name, so the hashtag check does not apply to them.

A milestone takes no `--icon`, `--color` or `--slug`, on `create` or on `edit`.
Phorge always shows a milestone with the `milestone` icon and its parent's color,
whatever is stored on it. It stores a hashtag on one, but `project.search` never
reports it back. None of them would do what it seems to, and a stored color is
worse than ignored: Phorge's own search matches it, so the milestone would turn
up under a color it is never shown in. phabfive refuses all three.

## Editing a project

```bash
phabfive project edit '#development' --add-member=@admin,@mikael.wallin \
    --remove-member=@viola.larsson --add-slug=dev --dry-run
```

```
[DRY RUN] Would apply to http://phorge.localhost/project/view/12/ (Development):
  Hashtags: Added: #dev
  Members: Removed: @viola.larsson
```

Anything already at its target is left out of the preview and is not sent. Here
`@admin` and `@mikael.wallin` were already members. An edit with nothing left to
send reports `No changes (already at target state)`.

| Option | Changes |
| --- | --- |
| `--name` | The name. Phorge keeps the old hashtag as an additional one |
| `--description`, `--icon`, `--color` | Those fields |
| `--add-slug` | Adds hashtags and keeps the existing ones |
| `--add-member`, `--remove-member` | Membership |
| `--space` | Moves it to another Space |
| `--visible-to`, `--editable-by`, `--joinable-by` | Its policies |

`--add-slug` has to read the project's current hashtags first. Phorge's hashtag
transaction replaces the whole list, and `project.search` reports only the primary
hashtag, so the full list is read from `project.query` and sent back with the new
one added.

A project cannot be archived or unarchived through Conduit, because Phorge offers
no transaction for it. Do that in the web UI.

## Lists of values

Every option that takes several values is repeatable and comma-separated, so
these two are the same:

```bash
phabfive project create "Platform" --member=@alice,@bob --member=@carol
phabfive project create "Platform" --member=@alice --member=@bob --member=@carol
```

A comma cannot appear in a username or a hashtag, so splitting on it never cuts a
real value.

## Output formats

Every command takes the global `--format`, and a write answers the way the other
apps do:

- **A machine format** (`yaml`, `json` or `jsonl`) gets the record
  `project show` gives for the project written. The change list goes to stderr.
- **A dry run** writes nothing and has no record to give, so it leaves stdout
  empty.
- **An edit that changed nothing** still gets the record.

```bash
phabfive --format=json project create "Probe" | jq -r '.[0].Project.Hashtag'
```

`project edit` reports by ID, because `--name` can move the hashtag the project
was found by.

A create or an edit also drops the cached project completions, so the new or
renamed project completes straight away. See [Caching](caching.md).

## See also

- [Policies](policies.md): the policy grammar and how policies are displayed
- [Maniphest CLI](maniphest-cli.md): filing tasks under projects with `--tag`
- [Caching](caching.md): the `projects` and `project-icons` completion caches
