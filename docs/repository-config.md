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
| `phabfive.tags.hints` | Which other tags this repository's tasks often need, and when | Checks them; see [Tag hints](#tag-hints) |

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

!!! note "Not shown by any command yet"
    phabfive reads `hints` and warns about a malformed one, but no command shows
    them yet. That is planned: `maniphest create` will list them, each with its
    project's description and your note. Until then, the file is where people and
    agents read them.

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
