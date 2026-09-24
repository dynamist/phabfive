---
name: phabfive
description: "Read and change Phabricator/Phorge objects - Maniphest tasks, Pastes, Diffusion repositories, Projects and Passphrase credentials - through the phabfive CLI. Use when the user names phabfive or a Phabricator/Phorge instance, or refers to a T, P, K or R monogram such as T123. Do not use for other issue trackers such as Jira, GitHub or GitLab. Requires PHAB_URL and PHAB_TOKEN."
---

# phabfive

phabfive is a command line client for Phabricator and Phorge. It covers Maniphest
(tasks), Paste, Diffusion (repositories), Projects and Passphrase (credentials), and
prints machine-readable YAML or JSON on request.

Before anything else, confirm the CLI is configured and can reach a server:

```bash
phabfive user whoami
```

It reports the host and the user phabfive is authenticated as, and exits 1 if every
configured host failed. If it fails, say phabfive is not configured and stop. Do **not**
run `phabfive user setup` to fix it: that is an interactive wizard and it will hang.
Configuration comes from `PHAB_URL` and `PHAB_TOKEN`, from `~/.arcrc`, and from
`.arcconfig` in the repository root.

## Learn the installed CLI

The installed binary is the authority for command syntax. Start with:

```bash
phabfive --help
```

Then print a group's subcommands by running the group on its own:

```bash
phabfive maniphest
phabfive paste
phabfive diffusion
phabfive project
phabfive passphrase
phabfive user
phabfive cache
```

A group invoked without a subcommand writes its help to **stderr and exits 2**. That is
how phabfive lists commands, not a failure. `--help` on any command is safe.

Never run `phabfive repl` or `phabfive user setup`. Both are interactive and neither
returns.

## Ask for machine-readable output

```bash
phabfive --format=json maniphest show T123
phabfive --format=yaml maniphest search --tag Backend
phabfive --format=jsonl maniphest show T123 T456   # one object per line
```

Global options must come **before** the subcommand. `phabfive maniphest show T123
--format=json` is a parse error; `phabfive --format=json maniphest show T123` is correct.

- `json`, `jsonl` and `yaml` are the machine-readable formats. All three emit the same
  objects with the same keys.
- `json` and `yaml` wrap them in a list. `jsonl` does not: it writes one object per line
  with no wrapper, which is what `jq -c`, `while read` loops and appended log files want.
  Nothing in a `jsonl` line is ever split across lines, so counting lines counts records.
- `rich`, `tree` and `table` are for humans. `rich` refuses to render a line longer than
  4096 characters and raises instead, which real task descriptions do hit.
- `table` is a grid, and list-shaped: `diffusion repo list`, `diffusion uri list`,
  `maniphest search`, `maniphest parents`, `maniphest subtasks`, `paste search`,
  `project search` and `user search` render one row per record. A `show` command
  registers no table and falls back to `rich`. The
  columns are derived from the record, a cell is cut to 60 characters, and a column empty
  in every row is dropped - so never parse it, ask for `json` instead.
- `value` prints bare values - no keys, no header, no decoration - for piping. Which
  value is each command's own choice: `passphrase show` prints the secret, `passphrase
  search` one monogram per line, `paste show` the content, and `paste search` (or
  `paste show --no-content`) one monogram per line. No other app has one to
  print, so for maniphest and diffusion it silently falls back to `rich`.
- `strict` is accepted as an alias for `yaml`, `ndjson` as an alias for `jsonl`, and
  `simple` as an alias for `value`, which it used to be called.

When stdout is not a terminal phabfive already defaults to YAML, but pass `--format`
explicitly so the output does not change under you. `PHAB_FALLBACK` changes that default
to `json` or `jsonl`, and does not accept `table`.

Data goes to stdout; logging, diagnostics, status lines, and the help a bare group or search prints, go
to stderr. Capturing stdout alone is safe - `phabfive --format=json ... 2>/dev/null | jq .`
parses for every command, including the ones that write.

**Every** command honours `--format`, the ones that write included. A write answers a
machine-readable format with the same record `show` gives for the object it touched, so
the link and every field arrive together - `maniphest create/edit/comment`, the bare
`edit`, `paste create/edit/comment`, `diffusion repo create/edit`,
`diffusion uri create/edit` and `project create/edit`:

```bash
phabfive --format=json maniphest create "probe" --yes | jq -r '.[0].Link'
phabfive --format=json maniphest edit T123 --priority=high --yes | jq -r '.[0].Task.Priority'
phabfive --format=json paste create "notes" --content=- --yes | jq -r '.[0].Link'
phabfive --format=json diffusion repo create probe --yes | jq -r '.[0].Repository.Monogram'
phabfive --format=json project create "Probe" | jq -r '.[0].Project.Hashtag'
```

`project edit` reports by ID for the same reason, since `--name` moves the hashtag.
`repo edit` reports by monogram, because `--short-name` can move the short name out from
under the identifier you looked it up by. A URI write answers with the repository's URI
records - what `diffusion uri list` gives - because a URI has no page of its own and
there is no `uri show`. It is the whole set and not the one URI named: `uri create`
demotes every URI already on the repository, so the set is what changed.

An edit that needed no transaction still answers with the object's record: "already at
the target state" is an answer about it, not an absence of one. Under `rich`, `tree`,
`table` and `value` these commands print what they always have - a URL for `create`, a
change list for `edit`.

`--dry-run` on a write command wrote nothing, so there is no record to give: it puts its
preview on stderr and leaves stdout empty under a machine-readable format. Check the exit
code, not the output, to tell a dry run from a refusal. `apply --dry-run` is the one
exception, and deliberately so: it prints the plan itself on stdout, one record per
object it would create, because the plan is the thing you asked for.

`apply -f SPEC` answers with one record per object the spec created, parents and
children alike, in the order they were created - `status`, `monogram`, `phid` and the
`path` in the file that asked for it. A run that stopped partway still answers with the
whole list, the objects that were not reached marked `skipped`, and exits 4.
`maniphest create --with=SPEC`, the deprecated spelling, answers with one `show` record
per task instead.

`cache clear` is the one write with no Phorge object behind it, so it has no `show`
record to give. It answers with what it did instead, in `cache info`'s vocabulary:

```json
{"Removed": 12, "Namespaces": ["users"], "Host": null, "AllInstances": false}
```

`Namespaces` is null when every namespace was cleared, `Host` names the `--url` host
when that was the scope, and `AllInstances` reports `--all` - so the scope is readable
without parsing the sentence that states it.

## Monograms, and the one that writes

A bare monogram as the first argument expands to a command:

| Monogram | Expands to |
| --- | --- |
| `T123` | `maniphest show T123` |
| `P123` | `paste show P123` |
| `K123` | `passphrase show K123` |
| `R123` | `diffusion repo show R123` |

A monogram followed by a word is **not** a read:

```bash
phabfive T123 "looks fine to me"   # posts a comment on T123
```

For `T` and `P`, `phabfive <monogram> <text>` expands to `<app> comment <monogram> <text>`
and writes immediately. Prefer the explicit form whenever you mean to read:

```bash
phabfive --format=json maniphest show T123
```

Expansion only happens when the monogram is the first positional argument, so
`phabfive maniphest parents T123` is left alone.

## Read a task completely

A plain `maniphest show` looks like the whole task but omits the discussion. Comments,
transition history and filter metadata are each behind a flag:

```bash
phabfive --format=json maniphest show T123 --show-comments --show-history --show-metadata
```

Short forms: `-C` comments, `-H` history, `-M` metadata, `-P` policy.
`--no-description` / `-n` drops the description when you only want the fields.

Use `-C` whenever the question is about what was decided, agreed or reported on a task.
Without it you are reading the summary and guessing at the rest.

Several tasks at once, space- or comma-separated:

```bash
phabfive --format=json maniphest show T123 T124
phabfive --format=json maniphest show T123,T124
```

If any ID is missing, the others are still printed and the exit code is 1, so a partial
result is distinguishable from a complete one.

Relations have their own commands:

```bash
phabfive maniphest parents T123
phabfive maniphest subtasks T123
```

`--show-policy` / `-P` adds a `Policy` section, holding three entries where a
repository holds three of its own:

```bash
phabfive --format=json maniphest show T123 --show-policy
```

```yaml
  Policy:
    Visible To: All Users
    Editable By: '#infrastructure'
    Can Interact: All Users
```

It is opt-in because naming a policy that points at a project or a user costs a
`phid.query`, which a read that never looks at the section should not pay.
`maniphest search --show-policy` adds it to every task on the page for one lookup.

The keys are Phorge's own labels, not the API's field names. `Can Interact` is the
one repositories do not have, and a task does not store it - it derives it from
`Visible To`, and answers `No One` while the task's status locks comments. So
`Can Interact` differing from `Visible To` is how a locked task says so, and there is
no option to set it: the way to move it is the status. A policy naming a project or a
user is shown as `#projectslug` or `@username`, the same spelling the options below
take.

The defaults differ per app: `paste show` prints the content by default, and
`passphrase show` prints the **secret** by default. Pass `--no-secret` unless the user
asked for the value, and never echo a retrieved secret into a summary, a commit message or
a file.

## Spaces hide objects that exist

Searches are silently scoped to the Space in `PHAB_SPACE`, which defaults to `S1`. A task
that exists in another Space returns no results and looks deleted.

```bash
phabfive --format=yaml maniphest search --tag Backend --space='*'   # every Space
phabfive --format=yaml maniphest search --space=Archive             # one Space
phabfive -v maniphest search --tag Backend                          # what was searched
```

`--space` takes a monogram, a name, a glob (`S*`, `*Public*`) or a comma-separated list.
`-v` reports on stderr which Space and which projects the search actually used — reach for
it whenever a search returns fewer results than expected.

`PHAB_SPACE` is deliberately not consulted when creating; a new task lands in the server's
default Space unless `--space` says otherwise.

## Search tasks

```bash
phabfive --format=json maniphest search "let's encrypt"
phabfive --format=json maniphest search --tag Backend --assigned=@me
phabfive --format=json maniphest search --author=@me --created-after=2w --status=any
phabfive --format=json maniphest search --tag Backend --order=updated --limit 20
```

- The text argument goes to Phorge's full-text search as it stands, in `maniphest search`,
  `paste search` and `project search` alike. It matches **whole words**, stemmed:
  `migration` finds "migrations" but `igrat` finds nothing. Write `~igrat` to match part
  of a word; `"a phrase"`, `-word` to leave a word out and `title:word` also work. An
  empty text search says so on stderr and suggests the `~` form - retry with it before
  concluding that nothing matches. `user search` is the exception: its text matches any
  part of a username or real name.
- `--tag` filters by project or workboard, and supports wildcards and `,` for OR and `+`
  for AND (`--tag "Backend*+Sprint 42"`).
- `--assigned` and `--author` accept a username or `@me`.
- `--status=open|closed|any` sets which statuses a search reaches; it is `open` by default,
  so closed tasks are excluded unless asked for. `--all` is a deprecated `--status=any`.
- `--visible-to` and `--editable-by` keep tasks whose view or edit policy is exactly that
  value (`users`, `admin`, `#project`, `@user`, `@me`, a PHID). It is the stored value: `users`
  means "set to All Users". Applied on the client, before `--limit`.
- `--include T123` pins a task into the results whatever the filters say; `--exclude T123`
  removes one. Include bypasses the limit, exclude is applied before it.
- Dates are **relative only**: `h`, `d`, `w`, `m` (30 days), `y` (365 days); a bare number
  means days. There is no absolute-date syntax. `--created-after`, `--created-before`,
  `--updated-after`, `--updated-before`.
- `--limit` defaults to 100 and is applied on the client *after* ordering, so it keeps the
  top N of the requested order.
- `--order` is `<field>[:asc|:desc]` over `priority`, `updated`, `created`, `closed`,
  `title`, `relevance`. Default `priority`. `relevance` takes no direction.

A search with no criteria at all prints its help and exits 2 rather than returning every task,
as `paste search`, `passphrase search` and `user search` do.
`--status=any` on its own is the deliberate way to ask for every task. Like every search it
is confined to the default Space, so a script that means every task passes
`--status=any --space='*' -l 0`; without `--space='*'`, tasks in other Spaces are silently
missing.

Output timestamps are local time, `%Y-%m-%dT%H:%M:%S`.

### Transition filters

`--column`, `--priority` and `--status` filter on how a task *moved*, not just where it is:

```bash
phabfive --format=json maniphest search --tag Backend --column='in:Blocked'
phabfive --format=json maniphest search --tag Backend --status='been:Resolved'
phabfive --format=json maniphest search --tag Backend --column='from:Review:backward'
```

Grammar: `from:`, `to:`, `in:`, `been:`, `never:`. `,` separates OR groups, `+` joins AND
conditions inside a group, `not:` negates a single condition. A third `:direction` part
(`forward`/`backward` for columns, `raised`/`lowered` for priority and status) is legal
only on `from:`.

A `--status` pattern still reaches open tasks only, so `--status='in:Resolved'` matches
nothing and warns. AND a scope into the group: `--status='closed+in:Resolved'`,
`--status='any+been:Blocked'`. A group of nothing but `open`/`closed`/`any` is answered by
the server and fetches no history.

These filters fetch each candidate task's full transaction history — one API call per
task. On a broad search that is slow; narrow with `--tag` and `--limit` first.

`docs/maniphest-cli.md` in the repository has the full grammar with worked examples.

## Write safely

Writes go to a live instance and are not undoable from the CLI. Only run them against an
instance the user has pointed you at, and prefer this order: dry run, show the user, then
apply.

```bash
phabfive maniphest create "Fix the flaky import test" --priority=high --tag Backend --dry-run
phabfive maniphest edit T123 --status=resolved --dry-run
phabfive maniphest edit T123 --visible-to='#infra' --editable-by=admin --dry-run

# the preview is on stderr here, and stdout is empty
phabfive --format=json maniphest edit T123 --status=resolved --dry-run
```

An option that adds several values - `--tag` and `--subscribe` on `maniphest create` and
`paste create/edit`, the `project` list options - is repeatable and comma-separated:
`--tag=Backend,QA` is `--tag=Backend --tag=QA`. Use `,`, not `+`, which is deprecated there
and means AND only in a search filter.

`--assign` sets the assignee and `--unassign` removes it (`phabfive edit T123 --unassign`); the two
cannot be combined.

`maniphest edit` and `paste edit` take `--subscribe` and `--unsubscribe`, and send
only what changes (`--add-subscriber` and `--remove-subscriber` are hidden aliases):

```bash
phabfive maniphest edit T123 --subscribe=@bob --unsubscribe=@me
```

Commits work the same way with `--attach` and `--detach` (hidden aliases `--add-commit` and
`--remove-commit`), and `maniphest create` takes `--attach`. A commit is `rCALLSIGN<hash>`,
`R1:<hash>`, a bare hash of at least 7 characters (searched in every repository; one found in
several is an error listing them) or a `PHID-CMIT-...`. `maniphest show` lists them under
`Commits`, and a create spec takes `commits:`:

```bash
phabfive maniphest edit T123 --attach=7d7fc2c --detach=rGUNNAR3ce278cd9912
```

To connect a commit to a task, **attach it** - do not paste its URL, or its hash, into a
comment or the description. Text only links: the commit is not listed under `Commits` in
`maniphest show`, and the task does not appear on the commit's page. If a comment should
also say what the commit did, attach it and reference it in the same edit:

```bash
phabfive maniphest edit T123 --attach=7d7fc2c --comment='Fixed in {rGUNNAR7d7fc2c3e002}' --yes
```

### Referencing objects in text

Comments, descriptions and titles are Remarkup. Write a monogram instead of a URL:

| Written | Renders as |
| --- | --- |
| `T123` | a link reading `T123` |
| `{T123}` | a link reading `T123: <the task's title>` |
| `rGUNNAR7d7fc2c3e002`, `7d7fc2c` | a link to the commit, reading what was written |
| `{rGUNNAR7d7fc2c3e002}` | a link reading `rGUNNAR7d7fc2c3e002: <the commit's summary>` |

Use the braces when the reader needs context - a list of related work, a summary, a
hand-over - so that `{T45}` says what T45 is without anyone opening it. Use the plain
form where the title would only repeat what the sentence already says. The title is
fetched when the text is shown, so it stays current if the task is renamed.

Watching a project cannot be changed from phabfive: Conduit's `project.edit` has no watchers
transaction, so it is done in the web UI. `project search --watcher` only filters by it.

`--visible-to` and `--editable-by` set who can see and who can edit a task, named after
the labels Phorge's own form uses. Each takes

| Value | Means |
|---|---|
| `public`, `users`, `admin`, `no-one` | the Phorge keyword |
| `#projectslug` | that project; a display name works too, `#'Human Resources'` |
| `@username` | that user |
| `@me` | you, the token's owner |
| `PHID-...` | that object, including a custom policy rule |

and anything else is refused before a call is made, because Conduit reads a value it
does not recognise as a policy nobody satisfies - so `--visible-to=nonsense` would
otherwise come back as a permissions error rather than a spelling one. `--dry-run`
names both ends of the change rather than showing a PHID:

```
  Visible To: All Users -> #infrastructure
  Editable By: Administrators -> @admin
```

`maniphest create` takes the same two. Phorge refuses a policy that would stop you
seeing or editing the task yourself, and that refusal is reported as a sentence.
There is no `--can-interact`: see the `Policy` section above.

`--dry-run` exists on every write command except the `comment` ones, which stay
immediate because a comment is cheap to correct. Under `--format=json`, `jsonl` or
`yaml` a dry run's preview is on stderr and stdout is empty. Preview first on anything that
cannot be undone: a repository cannot be removed once created, and
`diffusion uri create` **demotes every URI already on the repository** to
`io=read, display=never` before adding the new one, so it can un-publish a clone
URL that nothing asked it to touch. Its `--dry-run` names each URI it would demote.

### Things that will hang you

- `phabfive edit T123` with **no other option** means "edit the description in `$EDITOR`".
  It opens `vi` without checking for a terminal. Always pass at least one edit option.
- `maniphest create` with no `--description` opens `$EDITOR` on a terminal, then asks for
  confirmation. Always pass `--description`.
- `paste create` with no file and no `--content`, and `paste comment` with no text, do the
  same.
- Editing **one** task applies straight away. Editing **two or more** reviews them one at a
  time when you are at a terminal: each task's changes are printed, then `[y,n,a,q,?]` -
  apply it, skip it, apply all the rest, or quit. Nothing you skip is touched.
- Without a terminal there is nobody to ask, so a batch applies unreviewed - except a title,
  description or policy change, which fails with `--yes required for non-interactive mode`
  rather than rewriting text, or narrowing a policy, that nobody has read.
- `--dry-run` never asks, so it never needs `--yes`.
- `--interactive` forces the review for a single task, and reaches past a piped stdin to the
  terminal. With no terminal to show the changes on it fails rather than applying unreviewed.

Feed long text through stdin instead of an editor:

```bash
printf '%s\n' "$body" | phabfive maniphest edit T123 --description=-
```

### Directional edits

You do not need to know the current value to change it by one step:

```bash
phabfive edit T123 --priority=raise
phabfive edit T123 --priority=lower
phabfive edit T123 --tag="Sprint 42" --column=forward
```

`raise`/`lower` walk the priority ladder and skip Triage. `forward`/`backward` move by
workboard column order and stay put at either end.

If a task is on more than one board, `--column` without `--tag` fails and the error lists
ready-to-run commands, one per board. Read those instead of guessing which board was meant.

### Batch and pipelines

```bash
phabfive edit T1,T2,T3 --status=resolved --dry-run
phabfive --format=yaml maniphest search --assigned=@me --tag Backend | phabfive edit --column=Done
```

`phabfive edit` with no ID reads YAML from stdin. Every object must carry a `Link` field,
which is what `--format=yaml` emits, so a search pipes straight into an edit. Both shapes
are accepted: one document holding a list of objects, and one mapping per `---` document.

Batch validation is atomic: if any task fails validation nothing is modified, and the error
suggests how to partition the tasks by board. Batch mode refuses `$EDITOR` mode. With no
terminal to review on, a title, a description or a **policy** change fails for want of
`--yes` rather than applying unreviewed - the three that are hard to notice afterwards.
Everything else applies unprompted, so dry-run the batch first.

## Other apps

```bash
phabfive --format=json paste search "nginx" --author=@me | jq -r '.[].Paste.Name'
phabfive --format=json paste show P42 | jq -r '.[0].Paste.Content'
phabfive paste create "deploy notes" notes.md --dry-run

phabfive --format=json passphrase search "deploy" --type=password
phabfive --format=json passphrase show K12 --no-secret

phabfive --format=json diffusion repo list active
phabfive --format=table diffusion repo list active   # a grid, for a human
phabfive --format=json diffusion repo list all --show-uris
phabfive --format=json diffusion repo show R5
phabfive diffusion repo show R5 R6 --show-uris --show-branches
phabfive --format=json diffusion repo show R5 --show-policy
phabfive --format=json diffusion uri list R5
phabfive --format=table diffusion uri list R5        # the matrix, one row per URI
phabfive diffusion uri list R5 --clone
phabfive diffusion uri list R5 --io=observe
phabfive diffusion uri list R5 --display=always --external
phabfive diffusion uri list R5 --disabled
phabfive diffusion repo edit R5 --default-branch main --dry-run
phabfive diffusion repo edit R5 --visible-to=public --editable-by='#infra' --can-push=admin --dry-run
phabfive diffusion repo create <name> --dry-run
phabfive diffusion repo create <name> --allow-similar
phabfive diffusion uri create K1 R5 <uri> --observe --dry-run
```

`diffusion repo show` takes several repositories, space- or comma-separated, and
answers with a `Link` and a `Repository` section, plus - only when asked - `URIs`,
`Branches`, `Tags`, `Metadata` and `Policy`: `--show-uris` / `-U`,
`--show-branches` / `-B`, `--show-tags` / `-T`, `--show-metadata` / `-M`,
`--show-policy` / `-P`, and `--no-description` to leave the description out. A
repository that does not exist is a failed lookup, not an empty result: it is
reported on stderr and the exit code is 1 even when the other repositories asked
for were shown.

`diffusion repo list` answers with those same records, minus the per-repository
sections, sorted by name and filtered by the optional `active`, `inactive` or `all`
argument. `--show-uris` and `--show-policy` add their sections; branches and tags
are deliberately not offered here, because each costs one query per repository.
`--url` is a deprecated alias for `--show-uris` and warns on stderr.

`diffusion uri list` answers with one record per URI, covering all four of the
dimensions a URI has: `URI`, `Origin` (`built-in` when Phorge generated it,
`external` when it was added), `Role`, `I/O`, `Display`, the `Credential` it is
bound to, named by monogram, and `Disabled`. A URI is reported as its display
URI, the one the web UI shows, by `uri list`, `repo list --show-uris` and
`repo show --show-uris` alike, which all render this same record.

`I/O` and `Display` are each published as `Raw`, `Default` and `Effective` -
what is written on the URI, what it would inherit, and what is in force. A URI
with `Raw: default` behaves according to `Default`, which itself depends on
whether the repository is hosted and whether the URI is built-in, so neither
`Raw` nor `Effective` alone is the whole answer. `table` has one cell where the
other formats have three levels and spells it `observe (set)` or
`readwrite (default)`.

`Role` is the one-line answer to what the URI does, derived from the effective
I/O and carried in every format: `Phorge pulls from here` (observe),
`Phorge pushes here` (mirror), `clone + push` (readwrite), `clone (read-only)`
(read), `not in use` (none), and `disabled`, which overrides the rest.

The filters combine, and are validated before a request is made:
`--io`, `--display`, `--builtin` / `--external`, `--disabled` / `--enabled`.
`--io` and `--display` match a value that is either set on the URI or in force
on it, so `--io=default` finds the URIs that inherit their I/O and
`--io=readwrite` finds the ones that do read-write however they came by it.
`--clone` keeps only the URIs the instance shows as clone URIs, which is
`--display=always` on the effective value.

A hosted repository can report no URIs at all, and a filter that matches
nothing is an empty result - neither is a failure.

`diffusion repo edit` changes `--name`, `--short-name`, `--default-branch` and
`--status`. Phorge derives the built-in clone URIs from the repository's short
name if it has one and its name if it does not, so a change to either can move
them - `--dry-run` names every built-in URI that would move, with its current
and new address, before anything is applied.

`diffusion repo create` and a `repo edit --short-name` both refuse a name that
differs from an existing repository only in **case** or in `.` `-` `_`
punctuation - `MyRepo`, `my-repo`, `my_repo` and `my.repo` all count as the same
name as `myrepo`. The error names the repository clashed with, by monogram, so
it is clear whether the existing one was what was meant. `--allow-similar`
creates or renames it anyway.

This is stricter than Phorge, deliberately. Phorge stores `repositorySlug` in a
`utf8mb4_unicode_ci` column under a unique key, so it refuses a short name that
differs only in case or accents, but it happily accepts one that differs only in
punctuation - and a repository cannot be deleted afterwards through Conduit or
the web UI, only deactivated by an admin running `bin/remove destroy`. Cheap to
create and impossible to remove is why the default is to refuse.

An **exact** clash is always refused: `--allow-similar` does not reach it, because
Phorge would refuse it as well. The check runs at build time, so `--dry-run`
reports the clash rather than previewing a creation that cannot happen.

It also sets the three policies: `--visible-to`, `--editable-by` and
`--can-push`, named the way Phorge names the capability each one sets. Each takes

| Value | Means |
|---|---|
| `public`, `users`, `admin`, `no-one` | the Phorge keyword |
| `#projectslug` | that project; a display name works too, `#'Human Resources'` |
| `@username` | that user |
| `@me` | you, the token's owner |
| `PHID-...` | that object, including a custom policy rule |

and anything else is refused before a call is made, because Conduit reads a value
it does not recognise as a policy nobody satisfies - so `--visible-to=nonsense`
would otherwise come back as a permissions error rather than a spelling one.
`--dry-run` names both ends of the change rather than showing a PHID:

```
  Visible To: All Users -> Public (No Login Required)
  Editable By: Administrators -> #infrastructure
```

`repo show --show-policy` and `repo list --show-policy` report the same names under
`Policy`, in the same spelling the options take, so what a policy is shown as can be
typed straight back in. The section is opt-in because naming a policy that points at
a project or a user costs a `phid.query`, which a read that never looks at the
section should not pay - one lookup for a whole listing, but a lookup all the same.
Setting a policy needs no `--show-policy`. Phorge refuses a policy that would stop
you seeing or editing the repository yourself, and that refusal is reported as a
sentence.

```yaml
  Policy:
    Visible To: All Users
    Editable By: Administrators
    Can Push: All Users
```

The keys are Phorge's own labels rather than the API's field names, and the third
one is `Can Push` for the same reason a task's third one is `Can Interact`: Phorge
special-cases exactly three capabilities into the `-able By` family - `Visible To`,
`Editable By` and `Joinable By` - and names every other one after the capability
itself. `Pushable By` is a label one management panel applies to its own row.

`Can Push` only means anything on a repository Phabricator hosts. On one that
follows a remote, the stored push policy is never consulted, so it is reported as
the string `Not a Hosted Repository` in every format - `Repository.Hosted`, in the
same record, is the boolean to test. `--can-push` still sets a policy there, because
Phorge allows it and a repository can be made hosted later, but it warns on stderr
that nothing will consult it yet.

A repository is addressable by monogram (`R5`), callsign or short name. Not every
repository has a short name, so prefer the monogram when scripting.

```bash
phabfive --format=json project show '#development'
phabfive --format=json project show '#humans' --show-members
phabfive --format=json project show 13 --show-policy --show-metadata
phabfive --format=table project search
phabfive --format=json project search --member=@me
phabfive --format=json project search --parent='#development' --milestones
phabfive --format=jsonl project search --status=any --space='*' --show-policy -l 0
phabfive project create "Platform" --icon=infrastructure --member=@me,@alice --dry-run
phabfive project create "Backend" --parent='#platform' --dry-run
phabfive project create "Sprint 2" --milestone-of='#platform' --dry-run
phabfive project edit '#platform' --join=@bob --leave=@alice --dry-run
phabfive project edit '#platform' --add-slug=plat --editable-by='#platform' --dry-run
```

A project is named by `#hashtag`, bare hashtag, ID, PHID or exact name - quote a
hashtag, or the shell reads `#` as a comment. A milestone has no hashtag and usually
shares its name with other teams' milestones ("Sprint 1"), so name one by its ID: a
name two projects share is refused, and the error lists the ID of each. There is no
monogram shortcut for projects.

`project show` answers with a `Link` and a `Project` section - `Name`, `Hashtag`,
`Status`, `Icon`, `Color`, `Parent`, `Milestone`, `Description` - and `Space`, plus
`Policy` (`--show-policy` / `-P`), `Members` (`--show-members`) and `Metadata`
(`--show-metadata` / `-M`) when asked. Every project carries the same `Project` keys,
a milestone included: `Hashtag` is null on a milestone and `Parent` is null on a root
project. `Members` lists each member as `Username`, `Name` and `Roles`, the roles
passed through from Phorge unchanged - `bot`, `admin`, `disabled`, `verified` and so
on - which is how to tell a bot account from a person:

```bash
phabfive --format=json project show '#humans' --show-members \
  | jq -r '.[0].Members[] | select(.Roles | index("bot") | not) | .Username'
```

`project search` answers with those same records, sorted by name. `--status` picks
`active` (the default), `archived` or `any`; `--all` is a deprecated alias for
`--status=any` and warns on stderr. Subprojects and milestones are included unless
`--milestones` or `--no-milestones` says otherwise. The other filters are `--member`,
`--parent`, `--ancestor`, `--icon`, `--color` and `--space`, and a free-text query as
the argument. `--icon` and `--color` match what Phorge shows: a milestone is the
`milestone` icon and its parent's color, and an archived project matches the color it
was given, not the `disabled` it is shown as. An unknown color is refused; an unknown
icon (they are instance configuration) matches nothing. Like `maniphest search` it looks only in `PHAB_SPACE` unless `--space`
names other Spaces, so every project on the instance is
`--status=any --space='*' -l 0`; `--show-policy` costs one extra lookup for the whole
listing, not one per project.

`project create` takes `--description`, `--icon`, `--color`, `--slug`, `--member`,
`--space` and the three policies, and `--parent` for a subproject or `--milestone-of`
for a milestone, which takes no `--icon`, `--color` or `--slug` (`edit` refuses those
on a milestone too). `project edit` takes `--name`,
`--description`, `--icon`, `--color`, `--add-slug`, `--join`, `--leave` (hidden aliases
`--add-member`, `--remove-member`), `--space` and the three policies; anything already at its target is left out, and
adding a hashtag keeps the ones already there. A name whose hashtag another project
has is refused before anything is sent. The list options are repeatable and
comma-separated: `--member=@a,@b --member=@c`.

The policies are `--visible-to`, `--editable-by` and `--joinable-by`, in the same
grammar as a repository's, reported under `Policy` as `Visible To`, `Editable By` and
`Joinable By` - a project being the one object whose third capability is in the
`-able By` family. Restricting a project's view or edit policy to the project itself
locks out anyone not in it, you included, unless you are a member; Phorge refuses
that and it is reported as a sentence.

A project cannot be archived, unarchived or deleted through Conduit - there is no
transaction for it - so do that in the web UI.

```bash
phabfive --format=table user search --role=any
phabfive --format=json user search viola
phabfive --format=json user search --username=holm
phabfive --format=json user search --realname=larsson
phabfive --format=json user search --role=admin
phabfive --format=jsonl user search --not-role=bot,list,disabled -l 0
```

`user search` lists users, each as a `Link` and a `User` section of `Username`,
`Name` and `Roles` - the same record `project show --show-members` gives for each
member, so the two compare directly. `Metadata` is added with `--show-metadata` /
`-M`. The roles are Phorge's own: `disabled`, `bot`, `list` (a mailing list),
`admin`, `verified`, `approved` and `activated`. The text argument finds any part of
the username or the real name; `--username` searches the username alone and
`--realname` the real name alone, both ignoring case and accents. `--role` keeps users with every
role named and `--not-role` drops users with any of them, both repeatable and
comma-separated; an unknown role is refused. So every person who can use the
instance is `--not-role=bot,list,disabled`. A bare `user search` prints its help and
exits 2 rather than reading every user on the instance; `--role=any` asks for all of
them on purpose. The members of a project who are missing from it are a diff away:

```bash
comm -23 \
  <(phabfive --format=jsonl user search --not-role=bot,list,disabled -l 0 | jq -r .User.Username | sort) \
  <(phabfive --format=json project show '#humans' --show-members | jq -r '.[0].Members[].Username' | sort)
```

Search constraints are named per application and are not interchangeable: `paste search`
has `--author` but no `--assigned`, and a constraint borrowed from another app fails with
`ERR-INVALID-CONSTRAINT`.

`phabfive edit` only implements tasks, and the two other monograms are refused for
different reasons. A `P` returns `Paste editing not yet implemented` - pending work. A
`K` returns `Passphrases cannot be edited: Phorge exposes no passphrase.edit endpoint.
Credentials must be edited in the web UI.` - not pending work: Phorge exposes
`passphrase.query` and nothing else, so no flag, token or newer server changes the
answer. Both exit 1. A create spec's `passphrases:` section is refused offline for the
same reason.

## Specs

A repeatable search or a bulk creation lives in a **spec file**: one document that says
what it is, and then says it.

```yaml
spec: phorge/v1alpha1
kind: create            # or: search
metadata:
  name: sprint-tasks
  description: The plan, the work and the review
  version: "1"
  author: phabfive
variables:
  sprint: 12
tasks:
  - title: "Plan sprint {{ sprint }}"
    projects:
      - Development
```

Two commands run one:

```bash
phabfive apply -f specs/create/sprint-tasks.yaml --dry-run
phabfive apply -f specs/create/sprint-tasks.yaml --set sprint=13
phabfive search -f specs/search/blocked-tasks.yaml
phabfive --format=json apply -f specs/create/platform-bootstrap.yaml --dry-run
```

`apply` creates everything a `kind: create` spec holds - tasks, projects, milestones,
linked to each other by the `$local-id`s the file gives them. `search` runs every search
a `kind: search` spec holds, in document order, through one client. Handed the other
kind, each refuses by name and prints the command that does run it, exit 1.

`apply -f` and `search -f` read YAML, JSON, JSONL and TOML, chosen by extension:
`.yaml`/`.yml`, `.json`, `.jsonl`/`.ndjson`, `.toml`. Several `---` documents in one file are folded into one
spec, whichever kind it is, so a search spec's items and a `searches:` list are the same
thing written two ways.

Every string is rendered with Jinja2. A `{{ name }}` the file does not declare under
`variables:` and no `--set NAME=VALUE` supplies is an error before anything runs, not an
empty string. `--set` is repeatable and beats the file's own default.

Check a file without running it:

```bash
phabfive spec validate specs/create/sprint-tasks.yaml --offline
phabfive --format=json spec validate specs/create/sprint-tasks.yaml
```

There are two layers and both commands run both before writing or asking anything. The
**offline** layer needs no token, no `PHAB_URL` and no network - shapes, keys, variables,
`$local-id` links - and is all `--offline` runs. The **online** layer resolves the names
the file uses against the instance: users, projects, Spaces, workboard columns. So a
spec whose tenth task names a user who does not exist creates nothing at all. Under
`--format=json` each problem is a record carrying `code`, `layer` (`offline` or
`online`), `severity`, the `object` and the `field`.

`--dry-run` on `apply` plans and prints and writes nothing; under a machine-readable
format it emits one planned-item record per object. A real run emits one result record
per object instead, each with `status` (`created`, `failed` or `skipped`), `monogram`
and `phid` - which is what makes a partial failure readable: exit 4 means it stopped
partway and the records say which objects exist.

Branch on the exit status, not on the output. `apply`: `0` created, or planned cleanly
under `--dry-run`; `1` the file could not be read, is the wrong kind, or failed the
offline layer; `2` the online layer failed - a name does not resolve, or the instance
refused what was asked; `3` there is no instance to ask; `4` it stopped partway and some
objects exist. `search` is the same without `4`, and adds a search item that names no
filter at all to `1`. `spec validate` is the same without `4` too. A usage mistake - a
missing `-f`, an unknown flag - is click's own exit 2, so a `2` from any of the three is
not always an online failure; the message says which.

`--with` is the deprecated spelling, on seven commands: `maniphest`, `project` and
`paste create`, and `maniphest`, `project`, `paste` and `passphrase search`. It is the
same reader as `-f`, so all four serializations load at every one of them, and it warns
once naming `apply -f` or `search -f`. Two differences from `-f` are worth knowing:
a `create --with` **refuses** any other option on the line rather than dropping it,
because a create spec is applied as it stands; a `search --with` lets the options beside
it override what the spec says. `maniphest search` runs task searches only, so a spec
holding a project, paste or passphrase search is refused by name there — every other
`search --with` runs all of them. Prefer `apply -f` and `search -f`.

`specs/` in the repository holds runnable examples of both kinds and of all four
serializations; `specs/broken/` holds files that are wrong on purpose, each named after
the code it produces. `docs/phorge-spec.md` defines the format itself;
`docs/create-specs.md` and `docs/search-specs.md` are the guides.

## Caching

Only shell-completion data is cached on disk, keyed by instance and token, and never
secrets or task content. `phabfive cache info` reports counts and sizes without printing
values; `phabfive cache clear` drops them. Set `PHAB_CACHE=0` to disable. Nothing here
touches the server.

## Exit codes

- `0` success. Also a search that matched nothing, and a search given no criteria.
- `1` API, configuration or validation error — and a `show` where some of the requested
  IDs were missing but the rest printed.
- `2` a group or the root command was invoked without a subcommand; the help was written
  to stderr.
- `130` interrupted.

`apply`, `search` and `spec validate` are the exception and share their own table,
above: they use `2` for an online failure, `3` for no instance, and `apply` uses `4` for
a run that stopped partway. Their `2` is therefore not always a usage mistake, and the
message says which.

## Rules

- Read with `maniphest show`, never with the bare-monogram form, which comments when a
  word follows it.
- Pass `--show-comments` before concluding anything about a task's discussion or decisions.
- Attach commits to tasks with `--attach`, never by pasting a URL or hash into a comment.
  In text, write `{T123}` rather than `T123` or a URL where the title gives useful context.
- Pass `--format=json`, `--format=jsonl` or `--format=yaml` before parsing, and put it
  before the subcommand.
- Add `--space='*'` before reporting that a task or project does not exist.
- Dry-run every create, edit and batch, and show the user the preview before applying it.
- One task applies without asking. Two or more are reviewed one at a time when there is a
  terminal. Pass `--yes` to skip the review, `--interactive` to force it for a single task
  or for piped input. Neither overrides a validation error, and `--force` is a deprecated
  alias for `--yes`.
- Never run a data-altering command against an instance the user has not named.
- Never print a Passphrase secret anywhere except a direct answer to a request for it.
