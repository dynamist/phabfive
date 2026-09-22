# Policies

Every object in Phorge carries policies: who may see it, who may change it, and
— depending on the application — one capability of its own. phabfive can read
those policies on repositories, tasks and projects, and set them on all three.

The grammar a policy value is written in is the same everywhere, which is why it
is documented here once rather than twice. It lives in `phabfive/policy.py` for
the same reason: no one application owns it.

## Overview

| Application | Read with | Write with |
| --- | --- | --- |
| Diffusion | `diffusion repo show --show-policy`, `diffusion repo list --show-policy` | `diffusion repo edit --visible-to`, `--editable-by`, `--can-push` |
| Maniphest | `maniphest show --show-policy`, `maniphest search --show-policy`; filter with `maniphest search --visible-to` and `--editable-by` | `maniphest edit` and `maniphest create`, `--visible-to` and `--editable-by` |
| Projects | `project show --show-policy`, `project search --show-policy` | `project edit` and `project create`, `--visible-to`, `--editable-by` and `--joinable-by` |

Each object reports three policies. Two of them are the same on all three:

| Shown as | Means | Settable |
| --- | --- | --- |
| `Visible To` | Who can see the object | yes |
| `Editable By` | Who can change it | yes |
| `Can Push` (repositories) | Who can push to it | yes, and only meaningful on a hosted repository |
| `Can Interact` (tasks) | Who can comment on it | **no** — Phorge derives it |
| `Joinable By` (projects) | Who can join it without being added | yes |

The two special cases are covered under [Can Interact is derived](#can-interact-is-derived)
and [Can Push needs a hosted repository](#can-push-needs-a-hosted-repository).
They are the parts most likely to look like a bug and are not one.

## The value grammar

Every policy option — `--visible-to`, `--editable-by`, `--can-push` and
`--joinable-by` — takes the same set of values:

| Value | Means |
| --- | --- |
| `public` | Anyone, including users who are not logged in |
| `users` | Every logged-in user |
| `admin` | Administrators |
| `no-one` | Nobody |
| `#projectslug` | Members of that project |
| `@username` | That one user |
| `@me` | You — whoever the API token belongs to, as in `maniphest search --assigned=@me` |
| `PHID-...` | Whatever object the PHID names — this is how a **custom policy rule** is set, since it has no other name |

A project may be named by its hashtag or by its display name. Phorge's
`project.search` normalises what it is given, so both of these resolve the same
project, and both are reported back under its canonical slug:

```bash
phabfive maniphest edit T239 --visible-to='#human_resources' --dry-run
phabfive maniphest edit T239 --visible-to='#Human Resources' --dry-run
```

```
[DRY RUN] Would apply to T239:
  Visible To: #infrastructure → #human_resources
```

A raw PHID is accepted and is resolved for display in the same way:

```bash
phabfive maniphest edit T239 --visible-to='PHID-PROJ-4t24wbtrxbhiohw4aira' --dry-run
```

```
[DRY RUN] Would apply to T239:
  Visible To: #infrastructure → #human_resources
```

The four keywords, and the `#` and `@` prefixes, are offered by tab completion;
past a `#` it completes project names and past an `@` it completes usernames.
`@me` is offered alongside the usernames.

## How a policy is displayed

phabfive shows a policy the way Phorge's own web UI shows it, not the way the
API spells it. The four keyword constants are rendered as their web UI labels:

| API value | Displayed as |
| --- | --- |
| `public` | `Public (No Login Required)` |
| `users` | `All Users` |
| `admin` | `Administrators` |
| `no-one` | `No One` |

A PHID is resolved to the project or user it names, and written in the same
spelling the options accept — `#projectslug` and `@username` — so a policy you
read can be typed straight back in. A PHID phabfive cannot name, a custom policy
rule most often, is printed as the PHID rather than guessed at.

The section keys are Phorge's labels too. Phorge special-cases exactly three
capabilities into the `-able By` family — `Visible To`, `Editable By` and
`Joinable By` — and names every other capability after the capability itself,
which is why the third row is `Can Push` and `Can Interact` rather than
`Pushable By` and `Interactable By`.

## Reading a policy

`--show-policy`, or `-P`, adds a `Policy` section:

```bash
phabfive diffusion repo show R1 --show-policy --no-description
```

```
- Link: http://phorge.localhost/source/gunnar-firmware/
  Repository:
    Name: GUNNAR Firmware
    Short Name: gunnar-firmware
    Callsign: GUNNAR
    Monogram: R1
    Status: active
    VCS: git
    Default Branch: main
    Hosted: true
    Importing: false
  Space: S1 Default
  Policy:
    Visible To: All Users
    Editable By: Administrators
    Can Push: All Users
```

```bash
phabfive maniphest show T237 --show-policy --no-description
```

```
- Link: http://phorge.localhost/T237
  Task:
    Name: 'docs-policy probe: interact policy'
    Status: Spite
    Priority: Needs Triage
    Created: '2026-09-20T17:40:29'
    Modified: '2026-09-20T17:50:11'
    Closed: '2026-09-20T17:40:47'
  Assignee: admin
  Space: Default
  Policy:
    Visible To: All Users
    Editable By: '#infrastructure'
    Can Interact: All Users
  Parents: []
  Subtasks: []
```

The flag is on six commands:

| Command | Flag | Short form |
| --- | --- | --- |
| `diffusion repo show` | `--show-policy` | `-P` |
| `diffusion repo list` | `--show-policy` | `-P` |
| `maniphest show` | `--show-policy` | `-P` |
| `maniphest search` | `--show-policy` | — |
| `project show` | `--show-policy` | `-P` |
| `project search` | `--show-policy` | `-P` |

!!! note

    `maniphest search` takes the long option only. On `passphrase show`, `-P`
    already means `--no-public-key`, so do not carry the short form across
    applications out of habit.

### Why it is opt-in

Naming a policy that points at a project or a user costs an extra `phid.query`,
and a read that never looks at the section should not pay for it. The lookup is
batched, so it is one extra round trip per command however many objects are
listed — `diffusion repo list active --show-policy` against the local test
instance issues exactly one more `phid.query` than the same command without it —
but it is a round trip all the same, and most callers listing repositories or
searching tasks do not want it.

Setting a policy needs no `--show-policy`.

`--format=table` flattens the section into three more columns, one per policy,
at the right-hand end of the row. Here is that end of it, with the columns
between `Name` and `Visible To` cut away:

```bash
phabfive --format=table diffusion repo list active --show-policy
```

```
Name                Visible To                  Editable By      Can Push
GUNNAR Firmware     All Users                   Administrators   All Users
docs-policy-probe   Public (No Login Required)  #infrastructure  Administrators
uridoc377           Public (No Login Required)  Administrators   Not a Hosted Repository
```

### Filtering tasks by policy

`maniphest search` takes the same two options as a filter. They keep the tasks
whose stored policy is exactly that value, so the pair of commands below finds
every task still editable by All Users and then moves it:

```bash
phabfive --format=jsonl maniphest search --status=any --space='*' --editable-by=users -l 0
phabfive maniphest edit T12,T13 --editable-by='#humans' --dry-run
```

The match is on the stored value: `--visible-to=users` finds tasks set to "All
Users", not every task a logged-in user could open. See
[Filtering by Policy](maniphest-cli.md#filtering-by-policy).

## Writing a policy

```bash
phabfive diffusion repo edit R42 --visible-to=users --editable-by=admin --can-push='#infrastructure' --dry-run
```

```
[DRY RUN] Would apply to http://phorge.localhost/R42 (docs-policy-probe):
  Visible To: Public (No Login Required) → All Users
  Editable By: #infrastructure → Administrators
  Can Push: Administrators → #infrastructure
```

```bash
phabfive maniphest edit T239 --visible-to='#human_resources' --editable-by=@tommy.svensson --dry-run
```

```
[DRY RUN] Would apply to T239:
  Visible To: #infrastructure → #human_resources
  Editable By: Administrators → @tommy.svensson
```

Both ends of the change are named rather than shown as a PHID, and only the
policies that would actually change are listed — asking for a policy an object
already has produces no line for it.

`maniphest create` takes the same two options:

```bash
phabfive maniphest create "Rotate the deploy key" --description="Scratch" \
    --visible-to='#infrastructure' --editable-by=admin --dry-run
```

```
[DRY RUN] Would create task:
  Title: Rotate the deploy key
  Description: Scratch
  Visible To: #infrastructure
  Editable By: Administrators
```

`phabfive edit T123 --visible-to=...` routes to `maniphest edit` and behaves
identically. See [Edit CLI](edit-cli.md).

## Project policies

A project's third policy is `Joinable By`: who may join it without being added.
It is the third member of Phorge's `-able By` family, so a project is the one
object here whose three policies all read that way:

```bash
phabfive project show '#scratch_12684' --show-policy --no-description
```

```
- Link: http://phorge.localhost/project/view/20/
  Project:
    Name: Scratch 12684
    Hashtag: '#scratch_12684'
    Status: active
    Icon: tag
    Color: green
    Parent:
    Milestone:
  Space: S1 Default
  Policy:
    Visible To: '#scratch_12684'
    Editable By: All Users
    Joinable By: Administrators
```

All three can be set, on `project create` and on `project edit`:

```bash
phabfive project edit '#scratch_12684' --editable-by='#scratch_12684' --joinable-by=users --dry-run
```

```
[DRY RUN] Would apply to http://phorge.localhost/project/view/20/ (Scratch 12684):
  Editable By: All Users → #scratch_12684
  Joinable By: Administrators → All Users
```

A project's policy can name the project itself, which limits it to its members.
You have to be one of them, or Phorge refuses the edit as a
[self-lockout](#self-lockout). `project create --member=@me` makes you a member
at creation time.

To audit every project on the instance:

```bash
phabfive --format=jsonl project search --status=any --space='*' --show-policy -l 0
```

That is one line per project, with its policy section, and costs one extra
lookup for the whole listing. See [Project CLI](project-cli.md).

## Can Interact is derived

A task has no stored interact policy. Phorge computes one every time it is
asked: `ManiphestTask::getPolicy` returns the task's **view** policy, unless the
task's status locks comments, in which case it returns `No One`.

So `Can Interact` tracks `Visible To` and ignores `Editable By`:

```bash
phabfive maniphest show T239 --show-policy --no-description
```

```
- Link: http://phorge.localhost/T239
  Task:
    Name: 'docs-policy probe: created with policies'
    Status: Open
    Priority: Needs Triage
    Created: '2026-09-20T17:48:26'
    Modified: '2026-09-20T17:48:26'
  Assignee: (none)
  Space: Default
  Policy:
    Visible To: '#infrastructure'
    Editable By: Administrators
    Can Interact: '#infrastructure'
  Parents: []
  Subtasks: []
```

Move the view policy and the interact policy moves with it. `T240` below is
assigned to the caller, which is why Phorge accepts `no-one` on it at all — see
[Self-lockout](#self-lockout):

```bash
phabfive maniphest edit T240 --visible-to=no-one --yes
phabfive --format=json maniphest show T240 --show-policy | jq '.[0].Policy'
```

```json
{
  "Visible To": "No One",
  "Editable By": "All Users",
  "Can Interact": "No One"
}
```

There is no `--can-interact`, and there could not be: `maniphest.edit` has no
transaction for it. Sending one is answered by a list of the types it does
accept, and neither `interact` nor `policy.interact` is among them:

```
ERR-CONDUIT-CORE: Transaction with key "0" has invalid type "interact". This
type is not recognized. Valid types are: parent, column, space, title, owner,
status, priority, description, ... view, edit, ...
```

(abridged — the real list is some thirty types long, and holds `view` and
`edit` but nothing for interact.)

The two ways to move a task's interact policy are its **view policy** and its
**status**.

`Can Interact` reading `No One` while `Visible To` reads something else is how a
task whose status locks comments says so. Whether any status does that is an
instance setting: a status in `maniphest.statuses` with `locked` set to
`comments` locks comments, and `edits` forces `Editable By` to `No One` in the
same way. None of Phorge's six default statuses sets it, so on a stock instance
`Can Interact` always equals `Visible To`.

## Can Push needs a hosted repository

Phorge stores and edits a push policy on every repository, but consults it only
on one it hosts. On a repository that observes a remote the stored value is
inert, and both Phorge's own management panel and phabfive report that rather
than a value in force:

```bash
phabfive diffusion repo show R16 --show-policy --no-description
```

```
- Link: http://phorge.localhost/source/uridoc377/
  Repository:
    Name: uridoc377
    Short Name: uridoc377
    Callsign:
    Monogram: R16
    Status: active
    VCS: git
    Default Branch: master
    Hosted: false
    Importing: false
  Space: S1 Default
  Policy:
    Visible To: Public (No Login Required)
    Editable By: Administrators
    Can Push: Not a Hosted Repository
```

`Not a Hosted Repository` is a string, present in every format, so that every
repository carries the same three keys. The boolean to test in a script is
`Repository.Hosted`, in the same record.

`--can-push` still works there, because Phorge allows it and a repository can be
made hosted later, but it says on stderr that nothing will consult it yet:

```bash
phabfive diffusion repo edit R16 --can-push=admin --dry-run
```

```
WARNING: http://phorge.localhost/R16 (uridoc377) is not a hosted repository, so a push policy has no effect on it. It is stored, and applies if the repository becomes hosted.
[DRY RUN] Would apply to http://phorge.localhost/R16 (uridoc377):
  Can Push: All Users → Administrators
```

Note that the preview names the **stored** value, `All Users`, and not the
`Not a Hosted Repository` that `repo show` prints.

## An unknown value is refused before the call

Conduit does not report an unrecognised policy value as a bad value. It reads it
as a policy that nobody satisfies — so sending `--visible-to=nonsense` would come
back as exactly the same self-lockout error as `--visible-to=no-one`, reporting a
typo as a permissions problem.

phabfive checks the shape of the value first, and nothing is sent:

```bash
phabfive maniphest edit T239 --visible-to=nonsense --dry-run
```

```
Error: --visible-to must be one of: public, users, admin, no-one, #project, @user, @me, or a PHID (got 'nonsense')
```

Whether the project or user named actually exists needs the API, so that error
arrives from the lookup rather than from the grammar check — but still before
any edit is attempted:

```bash
phabfive maniphest edit T239 --visible-to='#no-such-project' --dry-run
```

```
Error editing T239: Project '#no-such-project' does not exist
```

Both exit 1. The prefix differs between the two applications — Maniphest writes
`Error:` and `Error editing T123:`, Diffusion writes `ERROR:` — so match on the
sentence rather than on the prefix if you are parsing stderr.

## Self-lockout

Phorge refuses a policy that would stop you seeing or editing the object
yourself. phabfive catches that refusal and reports it as a sentence rather than
a traceback, and nothing is changed:

```bash
phabfive maniphest edit T238 --visible-to=no-one --yes
```

```
Error editing T238: The view policy of this object would no longer allow you to view the object. Nothing was changed; choose a policy that still includes you.
```

```bash
phabfive diffusion repo edit R42 --visible-to=no-one --yes
```

```
ERROR: The view policy of this object would no longer allow you to view the object. Nothing was changed; choose a policy that still includes you.
```

Both exit 1.

What counts as a lockout is Phorge's judgement, not phabfive's, and it accounts
for capabilities you hold for other reasons. A task's assignee always keeps view
and edit on it, so `--visible-to=no-one` on a task assigned to you is accepted
and the task afterwards reads `Visible To: No One` — the same command on an
unassigned task is refused as above.

## Non-interactive safety

Editing two or more objects at a terminal reviews them one at a time. With no
terminal there is nobody to ask, so a batch would otherwise apply unreviewed —
and a policy change is treated as seriously as a title or description change,
the three that are hard to notice afterwards. All three refuse to apply
unreviewed:

```bash
phabfive maniphest edit T237 T238 --visible-to=public < /dev/null
```

```
Error: --yes required for non-interactive mode
No tasks were modified.
```

Pass `--yes` once you have previewed with `--dry-run`, which never asks and so
never needs it. Editing a **single** object applies straight away, with or
without a terminal; `diffusion repo edit` takes one repository and is always in
that case.

## Passphrase credentials

A Passphrase credential has policies in the web UI, and phabfive cannot show
them. `passphrase.query` is the only Conduit method Passphrase publishes, and
its result carries `description`, `id`, `material`, `monogram`, `name`, `phid`,
`type`, `uri` and `username` — no policy field of any kind.

This is an upstream gap rather than a phabfive omission: there is no
`--show-policy` on `passphrase show` because there is nothing to read it from.
Manage credential policies in the web UI.

## See also

- [Maniphest CLI](maniphest-cli.md) — task management, search and output formats
- [Edit CLI](edit-cli.md) — single, batch and piped editing
- [Project CLI](project-cli.md) — projects, their members, subprojects and milestones
- [Diffusion URIs](diffusion-uri.md) — a repository's URIs, which are a separate axis from its policies
