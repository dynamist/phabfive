# Editing with Specs

An edit spec is a file that says what tasks that already exist should be. `phabfive apply`
reads it and changes each task to match:

```bash
phabfive apply -f specs/edit/close-out.yaml --dry-run
phabfive apply -f specs/edit/close-out.yaml
```

This page is about running one. **The format — every key of an edit document, the list
grammar, the stamp — is defined in [the Phorge spec format](phorge-spec.md#edit-documents)**,
and is not repeated here. When a sentence on this page and a sentence on that one disagree,
that one is right.

## The command

```
phabfive apply -f FILE [--dry-run] [--force]
```

`apply -f -` reads the spec from standard input. Tasks can be edited today. Pastes, projects
and repositories have edit endpoints and will follow. A passphrase cannot be edited through
Conduit at all.

## Only the difference is sent

Each item of an edit spec is compared with the task as the server has it, and only the
difference is sent:

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

## Writing one

An edit spec states only what should change. **A key that is absent is left alone**, so this
is a complete spec:

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

Mixing the two forms in one list is refused. Quote an
entry YAML would read as a number: `"+1"`.

`assignment: "@none"` clears the assignee. `priority: raise` and `column: forward` are
refused: they are moves, and a file that is applied twice has to name where the task ends up.

`specs/edit/close-out.yaml` is a small one to copy.

### The column follows the projects

`column:` names a column but not a board, so it means the one workboard the task is on once
the item is applied. That has a consequence for an item that says both `column:` and
`projects:`:

- retag the task into a **different** project and the column is looked for on that
  project's board. The task is placed there if a column of that name exists, and the item is refused
  as `unknown-column` if it does not;
- remove its **only** board project and the item is refused, because there is no board left
  for the column to be on.

When retagging, change `column:` to a column of the new board, or delete the line and let
the board place the task.

## When somebody else changed the task

An item can say when the task was last modified as its writer saw it, with `modified:` and
an ISO 8601 timestamp. If the task has been modified since and the item would change it,
`apply` refuses:

```console
$ phabfive apply -f task.yaml
tasks[0]
  modified: T123 was modified at 2026-02-02T14:05:10Z, after the 2026-01-31T09:30:00Z this item was written against, and the item would change it. Write it against the task as it is now, or apply with --force to overwrite [stale]

task.yaml: 1 error, 0 warnings
```

Exit 2, and nothing is written — not to that task and not to any other in the file. Look
at the task again and redo the edit, or pass `--force`.

`--force` means the file wins. Its lists are full sets written before the other change, so a
subscriber somebody added in between is removed again. Use a `+`/`-` list, or leave the key
out, for anything the file should not own.

The stamp is only consulted for an item that would change something, so applying the same
file a second time is not refused for the modification the first run made. An item with no
`modified:` is never checked. The stamp is to the second, which is how precisely
the server reports it.

## Text is sent as written

Every string in a spec is a template. A description quoting `{{ .Values.tag }}` is refused as
an undefined variable unless the file says `interpolate: false`, which sends every string
exactly as written. Such a file takes no `variables:` and no `--set`.

A spec has no key for points, custom fields, the subtype, comments or the author, so an edit
cannot change them.

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
