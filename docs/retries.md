# Retries

A Conduit call that fails for a reason that may pass - the server was
unreachable, it did not answer in time, it answered HTTP 429 or a 5xx - is tried
again after a short wait. One policy covers every call phabfive makes, the
command's and a program's alike.

A call that succeeds costs nothing extra, so an interactive command is never
slower for it. Only a failing call waits, and with the defaults that is under two
seconds in total before phabfive gives up.

## What is retried

| Failure | A read | A write |
|---|---|---|
| Could not connect | retried | retried |
| Timed out, or the connection broke | retried | only an idempotent edit |
| HTTP 429, 500, 502, 503, 504 | retried | only an idempotent edit |
| Any other HTTP status, or a Conduit error | not retried | not retried |

A **read** is a method that only looks: `*.search`, `*.query`, `phid.lookup`,
`user.whoami`, `maniphest.info` and the like. Asking again is always safe.

A **write** that could not connect never reached the server, so it is safe to
send again. A write that was sent and then timed out may already have been
applied, and sending it again could do it twice - post a comment twice, create
two tasks. So it is not repeated, and the error says so:

```
Error: Failed to connect to Phabricator API: maniphest.edit was sent but no answer came back, so it may have been applied. It was not sent again, since doing so is not safe: check before trying again
```

A write answered with a 5xx is not repeated either, and says the same. A 502 or
504 from a proxy in front of Phorge usually means the server behind it carried on
working, so the comment may well have been posted.

An **idempotent edit** is the exception: one that only sets fields of an existing
object, so that arriving twice leaves it exactly as arriving once did. Setting a
status, a priority or a policy is idempotent; posting a comment is not.
`maniphest edit` and `phabfive edit` mark an edit idempotent whenever it carries
no comment, so a batch edit rides out a timeout without repeating anything.

Paged searches are retried page by page. When page 3 of a long `maniphest search`
fails, page 3 is asked for again with the same cursor; pages 1 and 2 are not
fetched a second time.

## How long it waits

Each retry waits a random time between nothing and a ceiling that doubles:
0.25 seconds before the first retry, 0.5 before the second, 1 before the
third. The randomness keeps many clients that failed together from coming back
together. A server that answers with `Retry-After` is waited for as long as it
asks - but never longer than `PHAB_BACKOFF_MAX`, which caps every single wait.

Every retry is announced on stderr, with what went wrong and the wait:

```
WARNING - maniphest.search: HTTP 503, retry 1 of 3 in 0.2s
```

`-q` silences it.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `PHAB_RETRY` | `3` | How often a failed call is tried again. `0` turns retrying off |
| `PHAB_BACKOFF_MAX` | `5` | The longest single wait, in seconds, `Retry-After` included |
| `PHAB_PACE` | `0` | Seconds to keep between the writes of a batch edit |

Like every setting they can go in the environment or in `~/.config/phabfive.yaml`.
Each must be a finite number of at least 0, and all three are checked as soon as
phabfive starts, so a typo fails before any work rather than halfway through a
batch.
The defaults suit a person waiting at a terminal. A bulk job that should ride out
a server restart can afford more patience:

```bash
PHAB_RETRY=8 PHAB_BACKOFF_MAX=60 phabfive maniphest search --status=any -l 0
```

## Pacing batch edits

A batch edit sends one `maniphest.edit` per task, back to back. Against a server
that is already struggling, `PHAB_PACE` spaces them out:

```bash
phabfive maniphest search --tag=migration --format=yaml |
  PHAB_PACE=1 phabfive edit --editable-by=admin --yes
```

The pause is kept between one write and the next, never before the first, and
the time spent reviewing or fetching in between counts towards it. A task that
needs no change sends nothing and is not paced.

## From a program

The library applies the same policy, configured the same way:

```python
from phabfive import Maniphest

maniphest = Maniphest(
    url="https://phorge.example.com",
    token="api-...",
    config={"PHAB_RETRY": 8, "PHAB_BACKOFF_MAX": 60, "PHAB_PACE": 1},
)
```

`Edit.apply_all()` keeps `PHAB_PACE` between its writes. A program making its own
writes through `self.phab` can declare one idempotent with
`phabfive.retry.idempotent_writes()`, and should only do so for an edit that sets
fields of an object that already exists:

```python
from phabfive.retry import idempotent_writes

with idempotent_writes():
    maniphest.phab.maniphest.edit(
        objectIdentifier="T123",
        transactions=[{"type": "view", "value": "users"}],
    )
```
