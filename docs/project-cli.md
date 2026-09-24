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

`phabfive user search` gives each user in the same shape (`Username`, `Name`,
`Roles`) under `User`. That makes the other half of a membership check a diff.
For example, to list every person who can use the instance but is not in `#humans`:

```bash
comm -23 \
  <(phabfive --format=jsonl user search --not-role=bot,list,disabled -l 0 | jq -r .User.Username | sort) \
  <(phabfive --format=json project show '#humans' --show-members | jq -r '.[0].Members[].Username' | sort)
```

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
| `QUERY` (argument) | Matching the text in the name or description, the way the web UI's search box matches it: whole words, and `~eam` for part of a word. See [Free-text search](maniphest-cli.md#free-text-search) |
| `--member` | That any of these users is a member of: `@user`, `user` or `@me` |
| `--parent` | Directly beneath this project |
| `--ancestor` | Anywhere beneath this project |
| `--milestones` / `--no-milestones` | That are, or are not, milestones |
| `--status` | `active` (the default), `archived`, or `any` |
| `--icon`, `--color` | With any of these icons or colors |
| `--space` | In these Spaces; `'*'` for all of them. Defaults to `PHAB_SPACE` |
| `--ids` | Only these project ids, with every other filter still applied |
| `--phids` | The same, by PHID |
| `--slug` | With any of these hashtags. A hashtag is exact; `QUERY` is not |
| `--watcher` | That any of these users watches: `@user`, `user` or `@me` |
| `--order`, `-o` | How to sort: see [Ordering](#ordering) |
| `--limit`, `-l` | This many at most; `0` for all. Defaults to 100 |
| `--with` | Reads the searches from a YAML search spec |

Watching can only be searched on. Conduit's `project.edit` has no watchers
transaction, so a project is watched and unwatched in the web UI, with the
Watch Project button on its page. Membership is what `project edit --join`
and `--leave` change.

`--status=any` is the same word `maniphest search` uses. `--all` is a
deprecated alias for it and prints a warning on stderr.

`--with` runs what the spec says. The options a spec has a key for -
`QUERY`, `--member`, `--parent`, `--ancestor`, `--milestones`, `--status`,
`--icon`, `--color`, `--space`, `--show-policy`, `--show-members` and
`--limit` - override the spec's value for every search in it. The five that a
spec has no key for yet - `--ids`, `--phids`, `--slug`, `--watcher` and
`--order` - are **refused** together with `--with` rather than accepted and
ignored, because silently dropping a filter answers a narrower question than
the one asked.

A saved search is a search spec, the same format `maniphest search --with`
reads:

```yaml
kind: search
searches:
  - type: project
    title: Mine
    search: {members: ["@me"], status: active}
```

One document may hold searches of several kinds - projects, tasks, pastes and
credentials - and they run in the order they are written. Run a mixed document
from `project search`, `paste search` or `passphrase search`: `maniphest
search --with` runs task searches only, and refuses an item of another type by
name rather than running it as one. See
[Search Templates](search-templates.md#searching-other-objects).

### Ordering

`--order` takes `<field>[:asc|:desc]`, as `maniphest search --order` does. A
bare field means the direction people usually want, and both directions are
available for every field that has one:

| Value | Order |
|---|---|
| `name` *(default)* | Alphabetical, A-Z |
| `name:asc` | The same, said explicitly |
| `name:desc` | Alphabetical, Z-A |
| `created` | Newest project first |
| `created:asc` | Oldest project first |
| `created:desc` | The same as `created` |
| `relevance` | Best text match first, and takes no direction |

The order is sent to `project.search`, so `--limit` returns the first N in that
order rather than an arbitrary N. `relevance` only means anything alongside
`QUERY`, and is the one order phabfive cannot re-sort locally.

An `--icon` or `--color` search is several searches merged, so the merge is
ordered and limited here rather than by the server; the answer is the same.

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
an unknown icon cannot be told from a custom one: it simply matches nothing. What
phabfive can tell is whether Phorge ships the icon or some project on the instance
carries it, and it warns when neither is true:

```console
$ phabfive project search --icon=grop
WARNING: No project uses the icon 'grop' and it is not one Phorge ships, so it may be misspelled. An icon configured in projects.icons that no project uses yet cannot be checked.
No projects found
```

The search still runs, because a configured icon that no project uses yet looks
exactly the same. The icons in use are cached for a week, with the ones tab
completion offers. Finding them means fetching every project, so with caching
off (`PHAB_CACHE=0`) the icon is not checked and nothing is warned about.

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
| `--icon`, `--color` | The icon and color. Tab completion offers them; an unknown color is refused before anything is sent, and the server checks the icon. A `--dry-run` never reaches the server, so it warns about an icon no project uses instead, as `project search` does |
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
phabfive project edit '#development' --join=@admin,@mikael.wallin \
    --leave=@viola.larsson --add-slug=dev --dry-run
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
| `--join`, `--leave` | Membership: adds or removes any user, not only you. `--add-member` and `--remove-member` are other names for them |
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
- [Caching](caching.md): the `projects` and `project-icons` caches
- [Search Templates](search-templates.md): saving a search as a spec, and `--with`
