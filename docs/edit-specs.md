# Exporting and Editing with Specs

`phabfive export` writes an object that already exists as a spec. `phabfive apply` reads the
spec back and changes the object to match:

```bash
phabfive export T123 > task.yaml    # the task, as a file
$EDITOR task.yaml                   # change what should change
phabfive apply -f task.yaml --dry-run
phabfive apply -f task.yaml
```

This page is about running them. **The format — every key of an edit document, the list
grammar, the stamp — is defined in [the Phorge spec format](phorge-spec.md#edit-documents)**,
and is not repeated here. When a sentence on this page and a sentence on that one disagree,
that one is right.

## Why not `show`

`phabfive T123` prints a task for a person: `Status: Resolved`, `Visible To: All Users`. None
of it can be typed back in — `--status` takes `resolved`, `--visible-to` takes `users`, and
the tags and subscribers are not printed at all.

An export is the same task in the spelling a spec takes:

```console
$ phabfive export T123
spec: phorge/v1alpha1
kind: edit
interpolate: false
tasks:
  - task: T123
    modified: '2026-01-31T09:30:00Z'
    title: Fix the login flow
    status: open
    priority: high
    assignment: viola.larsson
    projects:
      - '#infrastructure'
      - '#security'
    column: In Progress
    subscribers:
      - tommy.svensson
    space: S1
    visible-to: users
    editable-by: users
    parents:
      - T100
    subtasks: []
    commits: []
    description: |
      Sessions are dropped when the token is refreshed.
      Reproduce with two tabs open.
```

A project is its `#hashtag`, which names one project where a display name might name two. A
list is sorted, so the file changes when the task does and not when the server happens to
answer in another order.

## The commands

```
phabfive export [MONOGRAM]... [--as edit|create] [-o FILE]
phabfive apply -f FILE [--dry-run] [--force]
```

- `export` takes monograms or links, or reads them from standard input: bare monograms, or
  what a search prints in `--format=yaml`, `json` or `jsonl`.

  ```bash
  phabfive export T123 T124 -o tasks.yaml
  phabfive maniphest search --tag=backend | phabfive export > backend.yaml
  ```

- The output is YAML, on a terminal and off one. `--format=json` and `--format=jsonl` write
  the same document in those serializations; the terminal formats are refused, because the
  output is a file.
- `apply -f -` reads the spec from standard input, so the two compose:

  ```bash
  phabfive export T123 | phabfive apply -f - --dry-run
  ```

- Tasks are exported today. Pastes, projects and repositories have edit endpoints and will
  follow. A passphrase cannot be created or edited through Conduit at all, so it never will
  be more than readable.

## An unchanged export changes nothing

Each item of an edit spec is compared with the task as the server has it, and only the
difference is sent. Straight out of `export` there is no difference:

```console
$ phabfive export T123 | phabfive apply -f - --dry-run
[DRY RUN] -: would edit 0 objects, 1 already as written.
  (unchanged) task T123 'Fix the login flow'
```

Change two lines and that is what is sent:

```console
$ phabfive apply -f task.yaml --dry-run
[DRY RUN] task.yaml: would edit 1 object, 0 already as written.
  - task T123 'Fix the login flow'
      Status: Open -> Resolved
      Assignee: viola.larsson -> (none)
$ phabfive apply -f task.yaml
edited    task T123 'Fix the login flow'
      Status: Open -> Resolved
      Assignee: viola.larsson -> (none)
1 of 1 objects edited.
$ phabfive apply -f task.yaml
unchanged task T123 'Fix the login flow'
0 of 1 objects edited, 1 already as written.
```

The last run is why a spec can be kept in a repository and applied again: an object that
already matches is left alone, and the exit status is still 0.

There is no confirmation prompt, as for a create spec. The file is the statement of intent
and `--dry-run` is the preview.

## Writing one by hand

An export states the whole task, but an edit spec does not have to. **A key that is absent is
left alone**, so this is a complete spec:

```yaml
spec: phorge/v1alpha1
kind: edit
tasks:
  - task: T123
    status: resolved
  - task: T124
    status: resolved
    subscribers:
      - +sebastian.soderberg
```

`kind: edit` has to be written. A file without it is read as a create spec — its `tasks:`
section is spelled the same way — and refused for the `task:` key no create item has.

A list is either the full set or a delta:

| Written | Means |
|---|---|
| `[alice, bob]` | Exactly these; anyone else is removed |
| `[]` | Nobody |
| `["+carol", "-bob"]` | Add `carol`, remove `bob`, leave the rest |

`export` always writes the full set. Mixing the two forms in one list is refused. Quote an
entry YAML would read as a number: `"+1"`.

`assignment: "@none"` clears the assignee. `priority: raise` and `column: forward` are
refused: they are moves, and a file that is applied twice has to name where the task ends up.

`specs/edit/close-out.yaml` is a small one to copy.

### The column follows the projects

`column:` names a column but not a board, so it means the one workboard the task is on once
the item is applied. An exported task on one board carries its `column:`, and that has a
consequence when only `projects:` is edited:

- retag it into a **different** project and the column is looked for on that project's
  board. The task is placed there if a column of that name exists, and the item is refused
  as `unknown-column` if it does not;
- remove its **only** board project and the item is refused, because there is no board left
  for the column to be on.

When retagging, change `column:` to a column of the new board, or delete the line and let
the board place the task.

## When somebody else changed the task

An export stamps each task with when it was last modified. If the task has been modified
since and the file would change it, `apply` refuses:

```console
$ phabfive apply -f task.yaml
tasks[0]
  modified: T123 was modified at 2026-02-02T14:05:10Z, after the 2026-01-31T09:30:00Z this item was written against, and the item would change it. Export it again, or apply with --force to overwrite [stale]

task.yaml: 1 error, 0 warnings
```

Exit 2, and nothing is written — not to that task and not to any other in the file. Export
again and redo the edit, or pass `--force`.

`--force` means the file wins. Its lists are full sets written before the other change, so a
subscriber somebody added in between is removed again. Use a `+`/`-` list, or leave the key
out, for anything the file should not own.

The stamp is only consulted for an item that would change something, so applying the same
file a second time is not refused for the modification the first run made. A hand-written
item with no `modified:` is never checked. The stamp is to the second, which is how precisely
the server reports it.

## Copying a task

`--as create` writes a create spec instead: no `task:`, no `modified:`, and no parents,
subtasks or commits, because a copy is not the same node in the graph.

```bash
phabfive export T123 --as create > template.yaml
phabfive apply -f template.yaml          # a new task like T123
```

The result is an ordinary [create spec](create-specs.md). Applying it twice creates two
tasks.

## What is not exported

A spec has no key for points, custom fields, the subtype, comments or the author, so an
export does not carry them and an edit cannot change them.

Three things are exported only when they can be read back as written:

- **A column**, for a task on exactly one workboard. `column:` names no board, so with
  several there is no saying which it means. Move such a task with
  `phabfive edit T123 --tag BOARD --column COLUMN`.
- **A priority** in the stock table. An instance that defines its own has no keyword the
  edit takes, so the key is left out — and an absent key is left alone.
- **A subscriber that is a project or a package** is written as its PHID and kept as it is.
  A spec subscribes and unsubscribes users only.
- **A policy** that is a keyword, a project, a user or a custom policy. "Subscribers" and
  "Task Author" have no spelling in a spec, so the key is left out and the task keeps the
  policy it has.

## Text is sent as written

Every export says `interpolate: false`. A description quoting `{{ .Values.tag }}` is text,
not a variable, and without that line it would be refused as an undefined one. Such a file
takes no `variables:` and no `--set`.

A multi-line description is written as a YAML block where a block holds it exactly. Text a
block would alter — a line ending in a space, a carriage return — is written quoted instead.
Either way `export` parses its own output back before printing it, so what is in the file is
what was in the task.

## Machine-readable output

`--dry-run` prints one record per item, `status` being `would-edit` or `unchanged`, with the
`changes` it would make. A real run prints one record per item with `status` `edited`,
`unchanged`, `failed` or `skipped`:

```console
$ phabfive --format=json apply -f task.yaml
[
  {
    "type": "task",
    "path": "tasks[0]",
    "status": "edited",
    "monogram": "T123",
    "title": "Fix the login flow",
    "changes": [
      {"field": "Status", "old": "Open", "new": "Resolved"}
    ],
    "reason": null
  }
]
```

`--format=jsonl` flushes each record as it happens.

## Exit status

| Status | Means |
|---|---|
| 0 | Every object is as the file says: edited, or already so. Or `--dry-run` planned cleanly |
| 1 | The file could not be read, or the offline layer found an error |
| 2 | The online layer found a problem — a name that does not resolve, a task that does not exist, a `stale` item — and nothing was written. Or the instance refused the first edit |
| 3 | The instance could not be asked |
| 4 | An edit was refused partway: the records say which objects were edited and which were not reached |

Rerunning after a 4 is safe. The objects that were edited are found unchanged, and the rest
are tried again.

`phabfive spec validate FILE` runs both layers on an edit spec without `apply`. Its online
layer is the plan, so it reports exactly what `apply` would refuse.

## See Also

- [The Phorge spec format](phorge-spec.md#edit-documents) - Every key of an edit document
- [Creating with Specs](create-specs.md) - `kind: create`, for objects that do not exist yet
- [Edit CLI](edit-cli.md) - Changing tasks with flags instead of a file
