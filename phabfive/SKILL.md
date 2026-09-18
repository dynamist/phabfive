---
name: phabfive
description: "Read and change Phabricator/Phorge objects - Maniphest tasks, Pastes, Diffusion repositories and Passphrase credentials - through the phabfive CLI. Use when the user names phabfive or a Phabricator/Phorge instance, or refers to a T, P, K or R monogram such as T123. Do not use for other issue trackers such as Jira, GitHub or GitLab. Requires PHAB_URL and PHAB_TOKEN."
---

# phabfive

phabfive is a command line client for Phabricator and Phorge. It covers Maniphest
(tasks), Paste, Diffusion (repositories) and Passphrase (credentials), and prints
machine-readable YAML or JSON on request.

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
- `rich` and `tree` are for humans. `rich` refuses to render a line longer than 4096
  characters and raises instead, which real task descriptions do hit.
- `simple` only means something for `passphrase` (prints the bare secret) and `paste`
  (prints bare content). For maniphest it silently falls back to `rich`.
- `strict` is accepted as an alias for `yaml`, and `ndjson` as an alias for `jsonl`.

When stdout is not a terminal phabfive already defaults to YAML, but pass `--format`
explicitly so the output does not change under you. `PHAB_FALLBACK` changes that default
to `json` or `jsonl`.

Data goes to stdout; logging, diagnostics and group help go to stderr. Capturing stdout
alone is safe.

## Monograms, and the one that writes

A bare monogram as the first argument expands to a command:

| Monogram | Expands to |
| --- | --- |
| `T123` | `maniphest show T123` |
| `P123` | `paste show P123` |
| `K123` | `passphrase show K123` |
| `R123` | `diffusion branch list R123` |

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

Short forms: `-C` comments, `-H` history, `-M` metadata. `--no-description` / `-n` drops
the description when you only want the fields.

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
phabfive --format=json maniphest search --author=@me --created-after=2w --all
phabfive --format=json maniphest search --tag Backend --order=updated --limit 20
```

- `--tag` filters by project or workboard, and supports wildcards and `,` for OR and `+`
  for AND (`--tag "Backend*+Sprint 42"`).
- `--assigned` and `--author` accept a username or `@me`.
- `--all` includes closed tasks; they are excluded by default.
- `--include T123` pins a task into the results whatever the filters say; `--exclude T123`
  removes one. Include bypasses the limit, exclude is applied before it.
- Dates are **relative only**: `h`, `d`, `w`, `m` (30 days), `y` (365 days); a bare number
  means days. There is no absolute-date syntax. `--created-after`, `--created-before`,
  `--updated-after`, `--updated-before`.
- `--limit` defaults to 100 and is applied on the client *after* ordering, so it keeps the
  top N of the requested order.
- `--order` is `<field>[:asc|:desc]` over `priority`, `updated`, `created`, `closed`,
  `title`, `relevance`. Default `priority`. `relevance` takes no direction.

A search with no criteria at all prints usage and exits 0 rather than returning every task.

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
```

`--dry-run` exists on `maniphest create`, `maniphest edit`, top-level `edit`,
`paste create` and `paste edit`. It does **not** exist on any `comment` command, nor on
`diffusion repo create`, `diffusion uri create` or `diffusion uri edit` — those write the
moment you run them.

### Things that will hang you

- `phabfive edit T123` with **no other option** means "edit the description in `$EDITOR`".
  It opens `vi` without checking for a terminal. Always pass at least one edit option.
- `maniphest create` with no `--description` opens `$EDITOR` on a terminal, then asks for
  confirmation. Always pass `--description`.
- `paste create` with no file and no `--content`, and `paste comment` with no text, do the
  same.
- Any change to a title, description or paste content prints a diff and asks to confirm.
  Pass `--force` to apply it non-interactively; without a terminal and without `--force`
  the command fails with `--force required for non-interactive mode`.

Feed long text through stdin instead of an editor:

```bash
printf '%s\n' "$body" | phabfive maniphest edit T123 --description=- --force
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
suggests how to partition the tasks by board. Batch mode refuses `$EDITOR` mode, and it
does **not** ask for diff confirmation — a batch title or description change applies
unprompted. Dry-run the batch first.

## Other apps

```bash
phabfive --format=json paste search "nginx" --author=@me
phabfive --format=json paste show P42
phabfive paste create "deploy notes" notes.md --dry-run

phabfive --format=json passphrase search "deploy" --type=password
phabfive --format=json passphrase show K12 --no-secret

phabfive --format=json diffusion repo list active
phabfive diffusion uri list R5 --clone
```

Search constraints are named per application and are not interchangeable: `paste search`
has `--author` but no `--assigned`, and a constraint borrowed from another app fails with
`ERR-INVALID-CONSTRAINT`.

`phabfive edit` only implements tasks. A `P` or `K` monogram returns
`Paste editing not yet implemented` / `Passphrase editing not yet implemented` and exits 1.

## Templates

Repeatable searches and bulk creation live in YAML, used with `--with`:

```bash
phabfive --format=json maniphest search --with templates/task-search/blocked-tasks.yaml
phabfive maniphest create --with templates/task-create/sprint.yaml --dry-run
```

Templates may hold several documents separated by `---`. Command line options override
template values. See `docs/search-templates.md` and `docs/create-templates.md`.

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

## Rules

- Read with `maniphest show`, never with the bare-monogram form, which comments when a
  word follows it.
- Pass `--show-comments` before concluding anything about a task's discussion or decisions.
- Pass `--format=json`, `--format=jsonl` or `--format=yaml` before parsing, and put it
  before the subcommand.
- Add `--space='*'` before reporting that a task or project does not exist.
- Dry-run every create, edit and batch, and show the user the preview before applying it.
- Pass `--force` only for a change the user has asked for; it exists to skip a confirmation
  prompt, not to override a validation error.
- Never run a data-altering command against an instance the user has not named.
- Never print a Passphrase secret anywhere except a direct answer to a request for it.
