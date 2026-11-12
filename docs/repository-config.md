# Repository Configuration

A repository can tell phabfive which Phorge instance its tasks live on, which
workboard they go on, and which other tags its tasks often need. You write that
down once, in the repository's `.arcconfig`, and commit it. Then everyone who
clones the repository gets it, and so does every agent working in it.

```json
{
  "phabricator.uri": "https://phorge.example.com/",
  "phabfive": {
    "tags": {
      "workboard": "dynatron",
      "hints": {
        "security-risk": "Anything that changes who can log in or what they can see",
        "textarion": "Changes to how documents are sent to Textarion for classification",
        "policy": null
      }
    }
  }
}
```

| Key | What it says | What phabfive does with it |
|---|---|---|
| `phabricator.uri` | Which instance | Talks to that instance, even when `~/.arcrc` holds several |
| `phabfive.tags.workboard` | Where this repository's tasks go | Uses it as the default `--tag` |
| `phabfive.tags.hints` | Which other tags this repository's tasks often need, and when | Lists them in `phabfive hints`, and reminds of them on `create`; see [Tag hints](#tag-hints) |

`phabricator.uri` is Arcanist's own key, so `arc` reads it too. Everything inside
`phabfive` belongs to phabfive alone, and Arcanist ignores it.

## The instance

`phabricator.uri` takes the address people open in a browser. A trailing `/api/`
is accepted as well. It decides which host in `~/.arcrc` phabfive uses, so you
are not asked which instance to use, and you don't need `PHORGE_HOST`. The token
still comes from `~/.arcrc`, because a token is personal and never goes in a
repository.

## The workboard

`workboard` names the project, in any form `--tag` accepts: its name, `#hashtag`,
ID or PHID. Inside the repository it fills in `--tag` whenever you don't give one:

| Command | Without `--tag` |
|---|---|
| `maniphest search` | Searches the workboard. A bare `phabfive maniphest search` lists its open tasks, where it would otherwise print help |
| `maniphest stats` | Counts the workboard's tasks |
| `maniphest create` | Puts the new task on the workboard, so `--column=Backlog` works without `--tag` |

Each of these commands says on stderr which workboard it used, so piped output
stays clean:

```
$ phabfive maniphest search --assigned=@me
Searching workboard dynatron (phabfive.tags.workboard in .arcconfig, or PHORGE_WORKBOARD); pass --tag for another, or PHORGE_WORKBOARD= for none
```

The workboard is left out when:

- **you pass `--tag`.** Yours replaces the workboard; the two are not combined
- **you ask for tasks by `--ids` or `--phids`.** Those are found wherever they are
- **the search comes from a spec given with `--with`.** A spec names its own filters
- **the command is `edit`.** Tags on an existing task are never changed unless you ask

To search every project for one command, clear it:

```bash
PHORGE_WORKBOARD= phabfive maniphest search "certificate"
```

`PHORGE_WORKBOARD` can also set the workboard from the environment, or from
`~/.config/phabfive.yaml`, and the environment wins over `.arcconfig`.

## Tag hints

A workboard says where a task goes. It doesn't say whether the task is also a
security risk, or touches the documentation toolchain. Those tags are easy to
forget, and an agent can't guess them. Hints list the tags that matter in this
repository, so the person or agent writing a task knows to consider them.

Each hint is a tag, in any form `--tag` accepts, and a note on **when it applies
here**:

```json
"hints": {
  "security-risk": "Anything that changes who can log in or what they can see",
  "policy": null
}
```

- **A note** says what that tag looks like in *this* repository. The project's own
  description on Phorge says what a security risk is in general. The note says
  which changes here count as one, which is something only the repository knows.
- **`null`** means the tag is relevant here and its description on Phorge says
  enough. Write a description on the project if it has none.

Hints are suggestions. phabfive never adds a hinted tag to a task by itself:
whoever writes the task decides whether it applies, and adds it with `--tag`.

## How the board is used

How a board is used belongs to the board, not to one repository: what each
column means, when a task moves, how priority and assignment are used, where
epics go. Several repositories, and everyone on the web, share it. So it is
written in the workboard project's own description on Phorge, where people see
it on the board and phabfive shows it with the hints. Phorge has no description
for a column, so the project description is the place for those too.

`.arcconfig` holds only what is particular to the repository: which board, and
which other tags matter here.

## Before working here: `phabfive hints`

`phabfive hints` shows what to know before working where you are. Inside the
repository that is everything above in one place: the instance and what chose it,
the `.arcconfig`, the workboard with its description and the columns its board
shows, and then each tag hint with its project's description and the
repository's note.

```
$ phabfive hints
- Instance:
    URL: https://phorge.example.com
    From: .arcconfig
  Repository:
    Config: /src/dynatron/.arcconfig
    Instance: https://phorge.example.com
  Workboard:
    Link: https://phorge.example.com/project/view/8/
    Tag: dynatron
    Project: Dynatron
    Description: |-
      Up next is what we commit to this week. Parent tasks go in Epics.
    Columns:
    - Epics
    - Backlog
    - Up next
    - In progress
    - In review
    - Done
    From: .arcconfig
  Tags:
  - Link: https://phorge.example.com/project/view/12/
    Tag: security-risk
    Project: Security Risk
    Description: Risk to unauthorized access or manipulation.
    Note: Anything that changes who can log in or what they can see
```

Outside a repository there is no board or hints to show. It shows the instance
phabfive would use and every instance in `~/.arcrc`, or, when several are there
and nothing picks one, says how to pick:

```
$ phabfive hints
- Instance:
    URL:
    From: 'none chosen: ~/.arcrc holds several; pick one with PHORGE_HOST, or work in a repository whose .arcconfig names one'
  Instances:
  - https://phorge.example.com
  - https://we.phorge.it
```

It is one record wherever it runs, and a section appears only when it applies:

| Section | When | What it says |
|---|---|---|
| `Instance` | Always | The instance phabfive would use, and `From`: `PHORGE_HOST`, `.arcconfig`, phabfive's configuration files, or `~/.arcrc` |
| `Instances` | Outside a repository | Every instance `~/.arcrc` holds a token for |
| `Repository` | Inside one | Its `.arcconfig`, and the instance it names |
| `Workboard` | When one is named, by `.arcconfig` or `PHORGE_WORKBOARD` | The project, its description and the columns its board shows, and `From`. Phabricator and Phorge before 2023.32 don't say which columns are hidden or in what order, so there the list is every column in the order they were created; see the [support matrix](support-matrix.md) |
| `Tags` | When the repository hints any | Each tag's project, description and note |

It takes `--format` like any command, so an agent can read it as `json` or
`yaml`. It never prompts, and asks the instance nothing unless there is a
workboard or a hint to look up. A hint that names no project, or several, is
still listed, with a `Problem` saying what is wrong, and a warning on stderr: a
stale hint should be fixed, not silently skipped.

## A reminder when creating a task

`maniphest create` ends its stderr with one line naming the hinted tags the new
task doesn't have, in a dry run too, which is when an agent can still add one:

```
$ phabfive maniphest create "fix: Refresh expired sessions" --description=- --dry-run
Tagging with workboard dynatron (phabfive.tags.workboard in .arcconfig, or PHORGE_WORKBOARD); pass --tag for another, or PHORGE_WORKBOARD= for none
Not tagged: security-risk, policy (repository hints; see phabfive hints)
```

It is a reminder only, and the task is created with exactly the tags given. A
spec given to `maniphest create --with` or `phabfive apply -f` gets the same line
for the hinted tags that any of its tasks lack, as `Not tagged on every task: ...`. Tags are compared by name,
ignoring case, a leading `#`, and `-`, `_` or a space, so `security-risk`,
`#Security_Risk` and `Security Risk` are one tag. A tag given by ID or PHID
isn't recognised, so the reminder may name a tag the task already has.

Hints apply on the repository's own instance. When `PHORGE_HOST` points
phabfive at another instance, the hints are left out, since the same name there
would be a different project, and `phabfive hints` says so under `Repository`.

## Mistakes are warned about, not fatal

Everything inside `phabfive` is phabfive's, so a key it doesn't know is a typo,
and it says so once per command:

```
WARNING - Ignoring unknown key phabfive.tags.workbord in /src/dynatron/.arcconfig; known keys: hints, workboard
```

A value of the wrong kind gets the same warning, such as a workboard that isn't
a string or a note that isn't a string or `null`. The value is then ignored and
the command runs without it, so a mistake in `.arcconfig` never stops a command
that doesn't need the setting.

## Where `.arcconfig` is found

phabfive walks up from the current directory to the root of the checkout, which
is the first directory with a `.git` in it, and reads `.arcconfig` there. That
works the same in a clone, a linked worktree (`git worktree add`) and a
submodule, where `.git` is a file rather than a directory.

## Arcanist

`arc` reads `phabricator.uri` and ignores the `phabfive` object without a
warning, on Arcanist's stable and master branches alike. The one way to make
`arc` refuse a key it doesn't know is to pass it on the command line with
`--config`, which nobody does with these.
