# Searching with Specs

A **search spec** is a file that says what to look for. `phabfive search` runs every search in
it, in document order, through one connection:

```bash
phabfive search -f specs/search/blocked-tasks.yaml
```

This page is about running search specs. **The format itself — every filter key of every
object type, the reference grammar, the variable rules, the serializations — is defined in
[the Phorge spec format](phorge-spec.md)**, and is not repeated here.

## The command

```
phabfive search -f FILE [--set NAME=VALUE]...
```

- `-f` (long form `--spec`) is a **required option**. The positional slot is left free on
  purpose; a bare `phabfive search` is click's own `Error: Missing option '-f' / '--spec'.`,
  exit 2.
- The file may be YAML, JSON, JSONL or TOML.
- Hand it a **create** spec and it refuses by name:

  ```console
  $ phabfive search -f specs/create/sprint-tasks.yaml
  Error: specs/create/sprint-tasks.yaml is a create spec. Run it with
    phabfive apply -f specs/create/sprint-tasks.yaml
  ```

  Exit 1, on stderr, in every format.

## Your first spec

`specs/search/blocked-tasks.yaml`, whole:

```yaml
# specs/search/blocked-tasks.yaml
spec: phorge/v1alpha1
kind: search
metadata:
  name: blocked-tasks
  description: >-
    Where work gets stuck: tasks sitting in a Blocked column, and tasks that
    have passed through Waiting or Blocked at some point. Narrow it to one
    project by adding a `tag:` to the search below.
  version: "1"
  author: phabfive
searches:
  - type: task
    title: "🚫 Blocked or Previously Blocked"
    description: "Workflow bottlenecks: where work stops moving"
    search:
      column: "in:Blocked,been:Waiting,been:Blocked"
      show-history: true
```

Three layers, and it is worth keeping them straight:

- `metadata:` describes the **file**.
- `title:` and `description:` on a `searches:` item are the **banner** printed above that
  search's results.
- `search:` is the **filters**. Everything inside it is the same vocabulary the command-line
  flags use — `column:` is `--column`, `show-history:` is `--show-history`.

`type:` says what the item searches, and defaults to `task`.

```console
$ phabfive search -f specs/search/blocked-tasks.yaml

============================================================
🔍 🚫 Blocked or Previously Blocked
📝 Workflow bottlenecks: where work stops moving
============================================================
WARNING - Filtering 476 tasks may take a while as each task requires fetching transition history from the API
```

No results, and that is still exit 0: an empty answer is an answer.

## Several searches in one file

A `searches:` list with more than one item runs them in order, each with its own banner. Of the
eight task search specs that ship, three are four-search reports —
`specs/search/project-status-overview.yaml`,
`specs/search/development-workflow-audit.yaml` and
`specs/search/team-productivity-report.yaml` — and they are the reason the form exists: one
command, one connection, a whole status meeting. The other five are single searches:
`blocked-tasks.yaml`, `escalated-priorities.yaml`, `high-priority-stale-tasks.yaml`,
`recently-moved-to-review.yaml` and `tasks-resolved-but-not-in-done.yaml`.

## Several applications in one file

`kind:` carries the verb and never the object type, so one document can ask Maniphest, Project
and Paste in one run. `specs/search/release-readiness.yaml` does exactly that — here is its
`searches:` list with the per-item descriptions stripped out, so the shape is visible:

```yaml
searches:
  - type: task
    title: Open blockers
    search: {status: open, order: "priority:desc", limit: 25}

  - type: project
    title: Where the work sits
    search: {status: active, milestones: false, limit: 25}

  - type: paste
    title: Release notes so far
    search: {text_query: release notes, limit: 10}
```

```console
$ phabfive --format=jsonl search -f specs/search/release-readiness.yaml |
    jq -r 'if .Task then "Task" elif .Project then "Project" else "Paste" end' |
    sort | uniq -c
     25 Project
     25 Task
```

The paste search matched nothing on that run, and said so on stderr:

```
No pastes found
Text search matches whole words, not parts of words. To match part of a word, prefix it with ~: '~release ~notes'
```

The single-application examples are `specs/search/active-projects.yaml` (`type: project`),
`specs/search/my-pastes.yaml` (`type: paste`, `author: "@me"`) and
`specs/search/deploy-credentials.yaml` (`type: passphrase`).

## What a passphrase search costs

This one is phabfive's behaviour rather than the format's, so it belongs here.

Phorge publishes no `passphrase.search`. The only method is the legacy `passphrase.query`, and
it takes no constraints at all. So every filter a passphrase item names — `text_query:`,
`type:` — is applied by phabfive, in Python, over every credential the token can see. `limit:`
is counted locally for the same reason: sent to the server it would truncate the list before a
single credential had been tested, so a limit of 2 with `type: key` could report none of the
keys that exist. Reading stops as soon as enough have matched, which shortens the walk but
does not make the query cheap. On a large instance, expect a passphrase search to cost roughly
what listing every credential costs — which is why
`specs/search/deploy-credentials.yaml` carries a small `limit` and says so in its
`metadata.description`.

**A search spec never fetches secret material.** `--show-secret` is refused outright next to a
spec, because the walk above would fetch the secret of every credential on the instance rather
than of the ones that matched:

```console
$ phabfive passphrase search --with specs/search/deploy-credentials.yaml --show-secret
ERROR: --show-secret cannot be combined with --with; a search spec never fetches secret material, use `passphrase show` for one credential
```

Read one secret with `phabfive passphrase show K1`.

## Transition patterns are not free either

`column:`, `priority:` and `status:` accept a transition grammar, spelled out on the
[format page](phorge-spec.md#grammars). Answering one means reading each candidate task's
history, one request per task, and phabfive says so before it starts:

```
WARNING - Filtering 476 tasks may take a while as each task requires fetching transition history from the API
```

Narrow the candidate set first — a `tag:`, a `status:` scope, a `created-after:` — or use an
`order:` where you only wanted the important ones first.
`specs/search/release-readiness.yaml` takes the second route deliberately, and its own comment
explains why; `specs/search/escalated-priorities.yaml` is where the pattern spelling earns its
cost.

## Supplying values from outside the file

`--set NAME=VALUE` supplies a variable the spec declares, repeatable, exactly as for `apply`:

```bash
phabfive search -f my-searches.yaml --set team=core
```

Variables render into the filters the same way they render into a task title - a
`tag: "{{ team }}"` becomes a real project name before the search is built.
`specs/search/high-priority-stale-tasks.yaml` is the shipped one to try it on:

```console
$ phabfive search -f specs/search/high-priority-stale-tasks.yaml --set stale_days=30
```

It declares `stale_days: {default: 14}`, so it runs unchanged and `--set` only widens the
window. That `{default: …}` declaration is the format's opt-out from the undefined-name rule,
and Jinja2's own `default` filter is **not** one — see [an undefined name is an
error](phorge-spec.md#an-undefined-name-is-an-error).

## Machine-readable output

`--format` is a **root** option, so it goes before the subcommand:

```bash
phabfive --format=json  search -f specs/search/active-projects.yaml
phabfive --format=jsonl search -f specs/search/release-readiness.yaml
```

`jsonl` emits one record per line with no wrapping array, flushed as each is written, which is
what to use when several searches in one file produce more records than you want to hold. The
banners and any "nothing found" notices go to stderr, so stdout is records and nothing else.

## When something is wrong

`search` runs the **offline** layer first — keys, value types, variables, patterns, times — and
refuses on any error before it opens a connection. You can run that layer by itself, with no
token and no network:

```console
$ phabfive spec validate --offline specs/search/blocked-tasks.yaml
specs/search/blocked-tasks.yaml: no problems found (offline).
```

For a search spec the offline layer is unusually thorough, because the filter key set of each
of the four object types **is declared complete**: a key that is not one of them is an error
naming the key, rather than a filter that quietly does not happen.

```console
$ phabfive spec validate --offline mistakes.yaml
searches[0]
  search.taag: A task search has no key 'taag'. Did you mean 'tag'? [unknown-key]
  search.limit: limit takes a whole number, not a str [wrong-type]

mistakes.yaml: 2 errors, 0 warnings
```

Anything left after that is the instance's answer, and it is reported as a sentence:

```console
$ phabfive search -f names-nobody.yaml
ERROR: No such user: 'nobody.here'
```

### Exit status

| Status | `search` |
|---|---|
| 0 | every search ran — an empty result is still 0 |
| 1 | the file could not be read, it is a create spec, the offline layer found an error, or a search item names no filter at all |
| 2 | the online layer failed: a reference does not resolve, or the instance refused what was asked |
| 3 | the instance could not be asked, or no configuration names one |

**A usage mistake also exits 2**, because that is click's own status for a bad command line.
The message says which it is: a usage error prints `Usage: …` and
`Try 'phabfive search --help'`.

Note that the deprecated `--with` path answers the *same* unresolvable reference with **1**,
not 2. That is the one behavioural difference between the two entry points, and `search -f` is
the one that is right: 1 means "the spec is wrong", and a user who has been deleted is not a
mistake in the file.

### The errors you will actually hit

Every row below was produced on a live instance while this page was written, not recalled from
the source. Each `Problem` record carries a stable `code`, which is what a CI job branches on.

| Code | Layer | What it means | Fix |
|---|---|---|---|
| `unreadable` | offline | the file is missing, does not parse, or declares a `spec:` version this phabfive does not read | check the path; `spec: phorge/v1alpha1` |
| `unknown-key` | offline | `taag:` — that object type has no such filter | the message suggests the nearest real key |
| `wrong-type` | offline | `limit: "many"` — text where a number belongs | `limit: 100` |
| `bad-time` | offline | `updated-after: "1 fortnight"` | units are `h`, `d`, `w`, `m`, `y`; a plain number is days |
| `bad-pattern` | offline | `column: "sideways:Done"`, `status: "sideways"` | the message names the grammar; it is tabulated [on the format page](phorge-spec.md#grammars) |
| `bad-policy` | offline | `visible-to: "nonsense("` | the value grammar is [on the format page](phorge-spec.md#grammars), and [Policies](policies.md#the-value-grammar) is the longer treatment |
| `bad-monogram` | offline | `parent: "TT9"` | `T9` |
| `deprecated-key` | offline (**warning**) | `all: true` still works but is deprecated | write `status: any` |
| `undefined-variable` | offline | `{{ team }}` is not declared and was not supplied | declare it, `--set` it, or declare `team: {default: core}` |
| `unknown-user` | online | `assigned:`, `author:` or `subscriber:` names nobody | check the username; `@me` is always you, a bare `me` is the account called "me" |
| `unknown-project` | online | `tag:` names no project | check the name or hashtag |
| `unknown-space` | online | `space:` names no Space | the message lists the Spaces you can see |

The full vocabulary of codes is [on the format page](phorge-spec.md). `specs/broken/` holds a
runnable file for four of the codes in it, if you want to see a failure before you meet it.
They are create specs, and four of the five rows above that they demonstrate
(`undefined-variable`, `unknown-user`, and the two local-id codes from
[Creating with Specs](create-specs.md#the-errors-you-will-actually-hit)) are reported the same
way for either kind. `unknown-key` is the one that is not: a search item's key set is declared
complete and a create item's is not, which is the precondition the
[format page](phorge-spec.md#layer-1-offline) states.

## What ships under `specs/`

Every file here is loaded and put through the offline validation layer by CI on every
run (`tests/test_spec_corpus.py`), so a file named here is a file that loads — except the
ones under `specs/broken/`, which that walk excludes on purpose and
`tests/test_spec_broken_corpus.py` checks the other way round, by the problem each one
produces. The **online** layer runs over the same corpus in the Kubernetes job
(`tests/e2e/test_spec_corpus_online.py`), which also plans every create spec with
`--dry-run` — so a shipped example naming a user or a project nobody has goes red there
rather than in your terminal. Nothing in that gate ever applies an example for real: a
project cannot be deleted through Conduit and a hashtag is taken once.

| File | Shows |
|---|---|
| `specs/search/blocked-tasks.yaml` | one search; `column:` transition patterns |
| `specs/search/high-priority-stale-tasks.yaml` | time filters |
| `specs/search/recently-moved-to-review.yaml` | `to:` column history patterns |
| `specs/search/escalated-priorities.yaml` | `priority: raised` — priority transitions |
| `specs/search/tasks-resolved-but-not-in-done.yaml` | a status scope AND a column pattern |
| `specs/search/project-status-overview.yaml` | four task searches: urgent, resolved, stuck, escalated |
| `specs/search/development-workflow-audit.yaml` | four task searches across the pipeline, with `forward`/`backward` |
| `specs/search/team-productivity-report.yaml` | four task searches: in, out, load, age |
| `specs/search/active-projects.yaml` | `type: project` |
| `specs/search/my-pastes.yaml` | `type: paste`, `author: "@me"` |
| `specs/search/deploy-credentials.yaml` | `type: passphrase`, with the walk cost in its `metadata.description` |
| `specs/search/release-readiness.yaml` | task, project and paste in one document |
| `specs/formats/audit.{yaml,json,jsonl,toml}` | one search spec, four serializations |
| `specs/broken/*.yaml` | one file per failure, each named after the code it produces |

## Coming from `--with`

`--with` still works on all four search commands, and still does what it did. It now warns once
and names what replaces it:

```console
$ phabfive maniphest search --with specs/search/high-priority-stale-tasks.yaml
WARNING - --with is deprecated; run `phabfive search -f FILE` instead
...
```

It will be removed when the format promotes from `phorge/v1alpha1` to `phorge/v1`. Until then,
two differences are worth knowing. Reading the file is no longer one of them: `--with` goes
through the same loader `-f` does, so all four serializations load at either spelling.

- **`--with` still lets command-line flags override the file.**
  `phabfive maniphest search --with FILE --tag Development` runs the spec's search with that tag,
  and `--limit 2` truncates it. `phabfive search -f` has no such overrides; it takes `--set`
  for variables and nothing else. Where you relied on flag overrides, declare a variable and
  `--set` it.
- **`maniphest search --with` runs task searches only.** An item whose `type:` is not `task` is
  refused by name:

  ```console
  $ phabfive maniphest search --with specs/search/my-pastes.yaml
  WARNING - --with is deprecated; run `phabfive search -f FILE` instead
  ERROR: My pastes: 'maniphest search' runs a task search, and this one is a 'paste' search. Run the spec with 'phabfive search -f FILE' instead, which runs every type a spec holds.
  ```

  `project`, `paste` and `passphrase search --with` each run every type a spec holds, so which
  of the three you start from decides nothing but where the first connection comes from. That
  is a quirk of the deprecated path, and it is why the sentence above names `search -f` rather
  than one of the other three: answering a deprecated flag with a second deprecated flag would
  only move the migration.

And the vocabulary changed, which is more than a rename:

| Then | Now |
|---|---|
| a **template**: a YAML file that one command read | a **spec**: a document any command reads, in YAML, JSON, JSONL or TOML |
| `search:` at the root, several documents separated by `---` | an envelope (`spec:`, `kind:`, `metadata:`) around one `searches:` list |
| `title:`/`description:` at the root of each document | on each `searches:` item, beside its `search:` |
| tasks only, unless you knew to start from `project search --with` | any `type:` the `kind:` admits, from `phabfive search -f` |
| `phabfive maniphest search --with F` | `phabfive search -f F` |
| no way to check a file without running it | `phabfive spec validate [--offline] F` |

The shipped files under `specs/` are all in the new format. There is no compatibility shim for
the old one and none is planned: `v1alpha1` promises nothing about key names, and
[the format page states that policy](phorge-spec.md).

## See Also

- [The Phorge spec format](phorge-spec.md) — the normative definition: every filter key, every rule
- [Creating with Specs](create-specs.md) — the other `kind:`
- [Maniphest CLI](maniphest-cli.md) — the task search flags, and what each pattern means
- [Project CLI](project-cli.md) — the project-search flags
- [Policies](policies.md) — the `visible-to` / `editable-by` value grammar
