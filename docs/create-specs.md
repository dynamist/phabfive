# Creating with Specs

A **spec** is a file that says what should exist. `phabfive apply` reads one and creates it:

```bash
phabfive apply -f specs/create/sprint-tasks.yaml --dry-run   # plan it
phabfive apply -f specs/create/sprint-tasks.yaml             # create it
```

This page is about running specs. **The format itself — every key, the reference grammar,
the variable rules, the serializations — is defined in
[the Phorge spec format](phorge-spec.md)**, and is not repeated here. When a sentence on this
page and a sentence on that one disagree, that one is right.

## The command

```
phabfive apply -f FILE [--dry-run] [--set NAME=VALUE]...
```

- `-f` (long form `--spec`) is a **required option**, not a positional argument. A bare
  `phabfive apply` is click's own `Error: Missing option '-f' / '--spec'.`, exit 2.
- The file may be YAML, JSON, JSONL or TOML. `apply` reads the extension and the content, not
  a flag.
- `apply` needs an instance. It resolves every project, user, space and monogram the file
  names before it writes anything, so there is no useful offline mode; use
  `phabfive spec validate --offline FILE` for that.
- Hand it a **search** spec and it refuses by name rather than guessing:

  ```console
  $ phabfive apply -f specs/search/blocked-tasks.yaml
  Error: specs/search/blocked-tasks.yaml is a search spec. Run it with
    phabfive search -f specs/search/blocked-tasks.yaml
  ```

  Exit 1. That sentence goes to stderr in every `--format`; it is a usage refusal, not a
  validation problem, so it never appears in a JSON problem stream.

## Your first spec

`specs/create/sprint-tasks.yaml` is the smallest one worth copying:

```yaml
# specs/create/sprint-tasks.yaml
spec: phorge/v1alpha1
kind: create
metadata:
  name: sprint-tasks
  description: >-
    Three tasks that open a sprint: the plan, the work and the review.
  version: "1"
  author: phabfive
variables:
  sprint: 12
  release: "2024.4"
  reviewer: "tommy.svensson"
tasks:
  - title: "Plan sprint {{ sprint }}"
    description: |
      Agree what sprint {{ sprint }} contains and write it down.
    projects:
      - Development
    priority: "high"
  - title: "Build release {{ release }}"
    description: |
      Produce release {{ release }} from what sprint {{ sprint }} agreed on.
    projects:
      - Development
      - Infrastructure
    priority: "normal"
  - title: "Review release {{ release }} against the security checklist"
    description: |
      Walk release {{ release }} through the checklist before it ships.
    projects:
      - Security
      - QA
    priority: "normal"
    assignment: "{{ reviewer }}"
```

The file in the repository carries the full descriptions and a comment block; the shape above
is all of it that matters. `spec:` and `kind:` are the envelope, `variables:` are rendered
with Jinja2 into every string, and `tasks:` is what gets created.

## Preview before you write

`--dry-run` resolves everything and prints the plan without sending a single write:

```console
$ phabfive apply -f specs/create/sprint-tasks.yaml --dry-run
[DRY RUN] specs/create/sprint-tasks.yaml: would create 3 tasks.
  - task 'Plan sprint 12'
  - task 'Build release 2024.4'
  - task 'Review release 2024.4 against the security checklist'
```

A dry run is not a syntax check. It asks the instance about every name in the file, so it is
also where you find out that `Develpoment` is not a project and `tommy.svensen` is not a user.
It can never exit 4, because it writes nothing.

## Supplying values from outside the file

`--set NAME=VALUE` supplies a variable the spec declares, and beats the declared value. It is
repeatable, and it is what makes one spec serve every sprint:

```console
$ phabfive apply -f specs/create/sprint-tasks.yaml --dry-run \
    --set sprint=13 --set reviewer=viola.larsson
[DRY RUN] specs/create/sprint-tasks.yaml: would create 3 tasks.
  - task 'Plan sprint 13'
  - task 'Build release 2024.4'
  - task 'Review release 2024.4 against the security checklist'
```

A variable declared with no value at all is an error until something supplies it — that is the
`sprint:` with nothing after it form, and `--set` is what it is waiting for:

```console
$ phabfive spec validate --offline needs-a-sprint.yaml
variables
  sprint: Variable 'sprint' has no value: give it one under variables:, write {default: <value>}, or supply it with --set [missing-variable]
tasks[0]
  title: {{ sprint }} is not declared under variables: and was not supplied. Did you mean 'sprint'? [undefined-variable]

needs-a-sprint.yaml: 2 errors, 0 warnings

$ phabfive spec validate --offline needs-a-sprint.yaml --set sprint=7
needs-a-sprint.yaml: no problems found (offline).
```

To make a variable optional inside the file instead, declare it with a `{default: …}`
mapping — `sprint: {default: 12}`. That declaration is the format's opt-out from the
undefined-name rule, and **Jinja2's own `default` filter is not**: `{{ sprint | default(12) }}`
is still a reference to an undeclared `sprint`, and still an error. The rule and the reason are
on the format page, under [an undefined name is an
error](phorge-spec.md#an-undefined-name-is-an-error).

## Applying it

```console
$ phabfive apply -f specs/create/sprint-tasks.yaml --set sprint=99
created  task T604 'Plan sprint 99'
created  task T605 'Build release 2024.4'
created  task T606 'Review release 2024.4 against the security checklist'
3 of 3 objects created.
```

One line per object, printed as it happens rather than at the end. There is no confirmation
prompt: the file is the statement of intent and `--dry-run` is the preview.

## More than one application in one file

This is the reason `apply` sits at the top level rather than under `maniphest`. One document
can create a project, a milestone of it, and the tasks tagged into both, tied together by the
`$local-id`s the file gives them:

```console
$ phabfive apply -f specs/create/platform-bootstrap.yaml --dry-run
[DRY RUN] specs/create/platform-bootstrap.yaml: would create 2 projects, 3 tasks.
  - project $observability 'Observability Platform'
  - project $first-milestone 'Foundations'
  - task $epic 'Stand up the observability platform'
  - task 'Deploy the metrics collector'
  - task 'Write the alerting runbook'
```

Read `specs/create/platform-bootstrap.yaml` for the file; the `$ref` grammar it uses is
[specified here](phorge-spec.md). The order the objects are created in is the order that makes
each reference resolvable, not the order they are written in.

**A project cannot be deleted or archived through Conduit, and a hashtag can only be taken
once.** So this is a file to dry-run first and to apply once.

`specs/create/platform-project.yaml` is the same thing shrunk to one project, if all you want
is the project keys.

## Hanging new tasks off one that already exists

An item with a `parent:` and no `title:` creates nothing: it is an *anchor*, read for the PHID
of the task it names so the items nested under it become its children.

```console
$ phabfive apply -f specs/create/anchor-existing-task.yaml --dry-run
[DRY RUN] specs/create/anchor-existing-task.yaml: would create 3 tasks.
    (existing) task
    - task 'Rotate the build server host keys'
    - task 'Re-run the SSH configuration audit'
  - task 'Record the rotation in the change log'
```

`(existing)` is the anchor. The task it names is never edited: each child carries a
`parents.add`, so whatever already hung off it is kept. That is
[a rule of the format](phorge-spec.md), not a phabfive detail — no create ever rewrites a
collection on an object that already exists.

The last line is the same relationship written flat, one item per child carrying its own
`parents:`. Use nesting when several children share one parent.

## One spec, four serializations

`specs/formats/` holds the same create spec written four ways, and the same search spec beside
it. They plan identically:

```console
$ for f in specs/formats/sprint.yaml specs/formats/sprint.json \
           specs/formats/sprint.jsonl specs/formats/sprint.toml; do
    phabfive --format=json apply -f "$f" --dry-run | jq -S -c . | md5sum
  done
ebb5846321d8acb89f341914f58048d5  -
ebb5846321d8acb89f341914f58048d5  -
ebb5846321d8acb89f341914f58048d5  -
ebb5846321d8acb89f341914f58048d5  -
```

The differences between the four — YAML anchors are YAML-only, JSON and TOML carry no
comments, TOML has no null — are [listed on the format page](phorge-spec.md).

## Machine-readable output

`--format` is a **root** option, so it goes before the subcommand:

```bash
phabfive --format=json apply -f specs/create/sprint-tasks.yaml --dry-run
```

- `--dry-run` with a machine format emits the plan's own records on stdout and the
  `would create …` sentence on stderr, so a reader can pipe stdout straight into `jq`:

  ```console
  $ phabfive --format=json apply -f specs/create/platform-bootstrap.yaml --dry-run |
      jq -r '.[] | "\(.type)\t\(.id // "-")\t\(.display.title // .display.name)"'
  project	observability	Observability Platform
  project	first-milestone	Foundations
  task	epic	Stand up the observability platform
  task	-	Deploy the metrics collector
  task	-	Write the alerting runbook
  ```

- A real run emits one record per object. With `--format=jsonl` each is flushed as it is
  created, which for a run that stops partway is the difference between knowing what exists
  and waiting for a run that has already stopped.
- The summary sentence always goes to stderr under a machine format, never into the record
  stream.

## When something is wrong

There are two validation layers, and they fail differently.

**The offline layer** is the shape of the file: keys, value types, variables, `$local-id`s.
It needs no token and no network. `apply` runs it first and refuses on any error, and you can
run it by itself:

```console
$ phabfive spec validate --offline specs/create/sprint-tasks.yaml
specs/create/sprint-tasks.yaml: no problems found (offline).
```

Each problem carries a stable `code` you can branch on:

```console
$ phabfive spec validate --offline specs/broken/dangling-local-id.yaml
tasks[0]
  projects[0]: No object in this spec is called $platfrom. Did you mean 'platform'? [unknown-local-id]

specs/broken/dangling-local-id.yaml: 1 error, 0 warnings
```

`specs/broken/` holds one file per code, which is the fastest way to see what a given failure
looks like before you meet it.

**The online layer** asks the instance whether the names resolve. `phabfive spec validate`
(without `--offline`) reports it as the same `Problem` records with the same codes.
**`apply` does not**: by the time it asks, it is planning, so it answers with one sentence per
problem on stderr and no codes, whatever `--format` says:

```console
$ phabfive apply -f specs/broken/unknown-user.yaml --dry-run
Error: 2 problem(s) in this spec:
  - tasks[0].subscribers[0]: No such user: '@also.nobody'
  - tasks[0].assignment: No such user: 'nobody.here'

$ phabfive spec validate specs/broken/unknown-user.yaml
tasks[0]
  subscribers[0]: No such user: '@also.nobody' [unknown-user]
  assignment: No such user: 'nobody.here' [unknown-user]

specs/broken/unknown-user.yaml: 2 errors, 0 warnings
```

So when you want codes for an online failure, run `spec validate`, not `apply --dry-run`.

### Exit status

| Status | `apply` |
|---|---|
| 0 | everything was created; or `--dry-run` planned cleanly |
| 1 | the file could not be read, it is a search spec, or the offline layer found an error |
| 2 | the online layer failed: a reference does not resolve, or the instance refused what was asked |
| 3 | the instance could not be asked, or no configuration names one |
| 4 | the run stopped partway: **some objects exist** |

4 is the one worth handling. Conduit has no transactions, so "nothing happened" and "half of
it happened" are the two answers a caller retrying a run most needs to tell apart. `--dry-run`
can never return 4.

**A usage mistake also exits 2** — `phabfive apply` with no `-f`, an unknown flag — because
that is click's own status for a bad command line. The message says which it is: a usage error
prints `Usage: …` and `Try 'phabfive apply --help'`.

### The errors you will actually hit

Every row below was produced on a live instance while this page was written, not recalled from
the source.

| Code | Layer | What it means | Fix |
|---|---|---|---|
| `unreadable` | offline | the file is missing, does not parse, or declares a `spec:` version this phabfive does not read | check the path; `spec: phorge/v1alpha1` |
| `undefined-variable` | offline | `{{ sprint }}` is not declared under `variables:` and was not supplied | declare it, `--set` it, or declare `sprint: {default: 12}`. Not the Jinja `default` filter — see [above](#supplying-values-from-outside-the-file) |
| `missing-variable` | offline | `sprint:` is declared with neither a value nor a `{default: …}` | give it one, or supply it with `--set` |
| `circular-variable` | offline | the variables refer to one another in a circle | break the circle |
| `unknown-local-id` | offline | `$platfrom` — nothing in the file declares that `id:` | fix the spelling, or declare the `id:` |
| `duplicate-local-id` | offline | two objects declare the same `id:` | a local id names one object |
| `local-id-cycle` | offline | `$a` names `$b` and `$b` names `$a`, so neither can be created first | break the circle |
| `not-creatable` | offline | `$p` names a project and it was written in `parents:`, which names a task | use a key that takes that kind of object |
| `missing-required` | offline | `column:` with no `projects:` naming the board | add the project, or drop the column |
| `unknown-value` | offline | `color: chartreuse` — the value is outside a fixed set | the message lists the set |
| `wrong-type` | offline | `priority: 5` — a number where text belongs | quote it |
| `bad-monogram` | offline | `TT9` is not a task id | `T9` |
| `unknown-user` | online | `assignment:` or `subscribers:` names nobody | check the username; `@me` is always you, a bare `me` is the account called "me" |
| `unknown-project` | online | `projects:` names no project | create it first, or fix the name |
| `unknown-space` | online | `space:` names no Space | the message lists the Spaces you can see |
| `unknown-icon` | online (**warning**) | no project uses that icon and Phorge does not ship it | usually a typo; the run goes ahead |

Two things the offline layer deliberately does **not** catch on a create spec:

- an **unknown key** on a task or a project. Only search items are checked for that today: a
  reader may report `unknown-key` only where it declares an object type's key set complete, and
  none of the three create types is — [the format page states the rule and names the
  gap](phorge-spec.md#layer-1-offline). Calling an undeclared key an error before then would
  refuse documents that are correct.
- a bad `priority:` or `status:` **value**. Those are instance configuration, so they are
  refused by the planner instead, with the instance's own list:
  `Error: Invalid priority 'sideways'. Valid choices: Unbreak, Triage, High, Normal, Low, Wish`

Both are reasons to run `--dry-run` rather than `spec validate --offline` alone before a real
apply.

### Pastes

`kind: create` holds `project`, `task` and `paste`. A paste is a leaf — nothing nests under
one — and it takes `title:`, `content:`, `language:`, `projects:`, `subscribers:`,
`visible-to:` and `editable-by:`. `title:` is the spelling because `paste.edit` names the
transaction `title`; the web UI labels it "Name", and so does the preview.

```console
$ phabfive apply -f specs/create/release-notes-paste.yaml --dry-run
[DRY RUN] specs/create/release-notes-paste.yaml: would create 1 paste.
  - paste 'Release notes 2025.10'
```

`projects:` and `subscribers:` are the same two keys a task takes and resolve the same way, so
a paste can be tagged into a project the same document creates by naming its `$local-id`.

`passphrases:` is refused permanently and for a different reason: Phorge publishes
`passphrase.query` and no `passphrase.edit`, so no file can create a credential. A passphrase
can still be *searched* — see [Searching with Specs](search-specs.md).

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
| `specs/create/sprint-tasks.yaml` | the smallest useful spec: an envelope, variables, three tasks, projects by name |
| `specs/create/feature-epic.yaml` | nested subtasks, a YAML anchor reused as a subscriber list, assignments, links onto tasks that already exist |
| `specs/create/large-programme.yaml` | scale — 66 tasks across four quarters — and variables built out of other variables |
| `specs/create/platform-bootstrap.yaml` | a project, a milestone of it, and the tasks tagged into both, tied together with `$local-id`s |
| `specs/create/platform-project.yaml` | a project on its own: slugs, members, icon, colour, policies |
| `specs/create/anchor-existing-task.yaml` | the anchor form, and the same relationship written flat |
| `specs/create/release-notes-paste.yaml` | a paste — **not runnable yet**, see above |
| `specs/formats/sprint.{yaml,json,jsonl,toml}` | one create spec, four serializations, one plan |
| `specs/broken/*.yaml` | one file per failure, each named after the code it produces; `specs/broken/README.md` says which |

## Coming from `--with`

`--with` still works on all three create commands — `maniphest create`, `project create` and
`paste create` — and each of the three now reads the file through the same loader `apply -f`
does. It warns once and names what replaces it:

```console
$ phabfive maniphest create --with specs/create/sprint-tasks.yaml --dry-run
WARNING - --with is deprecated; run `phabfive apply -f FILE` instead
[DRY RUN] specs/create/sprint-tasks.yaml: would create 3 tasks.
  - task 'Plan sprint 12'
  - task 'Build release 2024.4'
  - task 'Review release 2024.4 against the security checklist'
```

That is the same preview, the same records and the same exit codes `apply -f` gives, because
it is the same code path: which of the three commands you reach it through decides nothing.
A spec that creates a project and the tasks tagged into it is created whole and reported
whole from `paste create --with` just as from `apply -f`, which is the strongest argument for
not reaching it through a create command at all.

It will be removed when the format promotes from `phorge/v1alpha1` to `phorge/v1`. One thing
about it is worth knowing until then:

- **every other option on the line is refused, not ignored.** A create spec is applied as it
  stands, so `--priority` beside `--with` would be dropped in silence; it answers
  `--with cannot be combined with --priority` and creates nothing. Put the value in the spec.

What changed is the vocabulary, and it is not only a rename:

| Then | Now |
|---|---|
| a **template**: a YAML file that one command read | a **spec**: a document any command reads, in YAML, JSON, JSONL or TOML |
| `tasks:` at the root, and nothing else | an envelope (`spec:`, `kind:`, `metadata:`) around `tasks:` and `projects:` |
| tasks only | whatever object types the `kind:` admits, in one document |
| `phabfive maniphest create --with F` | `phabfive apply -f F` |
| a partial failure exits 1 | a partial failure exits **4**, so a retry can tell it from "nothing happened" |
| no way to check a file without running it | `phabfive spec validate [--offline] F` |

The shipped files under `specs/` are all in the new format. There is no compatibility shim for
the old one and none is planned: `v1alpha1` promises nothing about key names, and
[the format page states that policy](phorge-spec.md).

## See Also

- [The Phorge spec format](phorge-spec.md) — the normative definition: every key, every rule
- [Searching with Specs](search-specs.md) — the other `kind:`
- [Maniphest CLI](maniphest-cli.md) — the single-task commands
- [Project CLI](project-cli.md) — the single-project commands
- [Phorge Setup](phorge-setup.md) — the local instance the shipped specs are written against
