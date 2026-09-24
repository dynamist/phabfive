# Caching

Shell completion asks the server for the values it offers. Without a cache
every TAB is a round trip — three of them, in fact, because connecting costs a
`conduit.query` and a `user.whoami` before the lookup itself runs. Pressing TAB
twice, which is how bash is normally driven, pays for all of it twice.

Phabfive caches those lookups on disk, so the first TAB on an option is the only
one that waits.

## What is cached

Only the lookups that shell completion makes:

| Lookup | Offered on |
|---|---|
| Usernames | `--assign`, `--assigned`, `--subscribe`, `--unsubscribe`, `--author` |
| Project names | `--tag`, in `maniphest`, `paste` and `edit` |
| Spaces | `--space`, in `maniphest create`, `edit` and `search` |
| Priority names | `--priority` and the priority filters |
| Status keys | `--status` and the status filters |

Two more lookups are kept for the commands themselves, because they are instance
configuration a command would otherwise ask for on every run:

| Lookup | Used by |
|---|---|
| The task status map (`maniphest.querystatuses`) | `maniphest create`, `edit` and `search`, and `phabfive edit` |
| Project icons, shared with completion | the `--icon` warning on `project search` and `project create`/`edit --dry-run` |

A script that runs `maniphest edit` in a hundred batches used to ask for the status
map a hundred times; now it asks once a week. The priorities need no such entry: the
commands check them against Phorge's fixed keywords and ask the server nothing.

Nothing else is cached. Phabfive does not cache API responses in general — the
tasks, projects and repositories a command reads or writes always come from the
server. Only the command caches: a program using phabfive as a library asks the
server every time and never writes to the cache directory.

**Secrets are never cached.** Caching is opt-in per call site rather than a
wrapper around the API client, so a response can only be stored by code written
to store it. Nothing in `passphrase` does, and credential values have no
completion that queries the server.

## Where it lives

```
~/.cache/phabfive/v1/<instance>/users/*.json
~/.cache/phabfive/v1/<instance>/projects/*.json
~/.cache/phabfive/v1/<instance>/spaces/*.json
~/.cache/phabfive/v1/<instance>/priorities/*.json
~/.cache/phabfive/v1/<instance>/statuses/*.json
~/.cache/phabfive/v1/<instance>/status-map/*.json
~/.cache/phabfive/v1/<instance>/project-icons/*.json
```

Directories are created `0700` and entries `0600`. Each instance gets its own
directory, keyed by URL and API token, so two accounts on one host never share
entries and rotating a token retires everything cached under the old one. The
token itself is never written out — only a hash of it names the directory.

Entries hold only what completion reads back: a username, a real name and
whether the account is disabled; a project's id, name and parent name; a
Space's monogram and name; the plain lists of priority names, status keys
and project icon keys; and the status map, which is each status's key and name
and whether it is open or closed. No PHIDs, no policies, no dates.

## How long entries live

| Data | Fresh for |
|---|---|
| Usernames, Spaces | 24 hours |
| Priorities, statuses, the status map, project icons | 7 days |
| Project names | 5 minutes |

User lists change rarely and instance configuration barely changes at all, while
projects come and go.

Both name lookups match on the server with a constraint that only ever gets
narrower as you type, so one lookup answers every prefix that extends it: after
`--assign <TAB>` has fetched once, `--assign so<TAB>` and `--assign son<TAB>`
are answered from the same entry without touching the network, and the same
goes for `--tag`.

Project names match on word prefixes rather than substrings, so typing `Core`
finds `GUNNAR-Core` on the server — though only names *starting* with what you
typed are ever offered, because the shell discards the rest.

Priorities and statuses are whole lists for the instance, so there is nothing
to narrow: one lookup a week answers every TAB. Project icons are the same kind
of list, with one difference: the icon set is instance configuration that no
Conduit method reports, so `--icon <TAB>` offers Phorge's stock icons plus every
icon a project on the instance actually carries. An icon that is configured but
not used by any project yet is not offered, and is still accepted when typed. Spaces are a whole list too,
and the one that gains most from being kept: they have no search endpoint, so
finding them means asking `phid.lookup` about `S1`, `S2`, `S3`… over the whole
range, which is two requests before anything can be offered.

## Staleness

Completion is advisory. A name that is not offered can still be typed, and the
server resolves it normally — so the worst a stale entry does is make you type
a colleague's name in full. A new project shows up within five minutes, but a
newly configured priority or status can take a week. A project created or
edited through `phabfive project` shows up straight away: the write drops the
`projects` namespace, and `project-icons` too when it set an icon. After somebody joins,
leaves or is renamed, after a project is created, or after the instance's
priorities or statuses are reconfigured:

```bash
phabfive cache clear
```

The status map is the one entry a command acts on, so it is the one that heals
itself: a status the remembered map does not know is asked of the server once
more before `maniphest edit --status` refuses it, so a newly configured status
can be set straight away. If that second request fails, the remembered map is
kept and the status refused, rather than judged by the standard statuses the
server may not have. What a stale map can still do for up to a week is show
a renamed status under its old name, or leave a status newly made open out of
the open statuses a search reaches by default — `cache clear status-map` fixes
both.

An `--icon` that no project uses and Phorge does not ship is warned about on
`project search` and on a `project create` or `edit` dry run, since a search
answers a misspelled icon with nothing and a dry run never reaches the server.
It is a warning, not an error: an icon configured in `projects.icons` that no
project uses yet cannot be told from a typo. Knowing which icons are in use
means fetching every project, so with caching off the icon is not checked at
all rather than costing that sweep on every run.

## Commands

```bash
phabfive cache info          # where the cache is, how much is in it, how old
phabfive cache clear         # drop the configured instance's lookups
phabfive cache clear users   # drop just one namespace
phabfive cache clear users projects             # or several
phabfive cache clear --url phorge.example.com   # drop every account's, for one host
phabfive cache clear --all   # drop every instance's, works without credentials
```

Naming a namespace keeps the rest: after somebody is renamed, `cache clear users`
costs one slower username completion instead of re-fetching every project too. The
namespaces are `users`, `projects`, `project-icons`, `columns`, `spaces`,
`priorities`, `statuses` and `status-map`, and they complete, showing what each currently holds:

```console
$ phabfive cache clear <TAB>
columns     -- nothing cached
priorities  -- nothing cached
projects    -- 3 lookups, 9 records
users       -- 1 lookup, 3 records
```

An unrecognised name is rejected rather than quietly clearing nothing, and a
namespace combines with `--url` or `--all` to clear it across every account or
every instance.

`cache info` reports sizes and ages only; it never prints what was cached.

Its `Lookups` column counts cached lookups, not objects. One lookup is one
stored entry: it holds a whole result, and a lookup for a shorter prefix answers
every longer one, so a single lookup can serve all project completions on the
instance. `Records` says how many projects or users are in them:

```text
 Namespace   Lookups   Records   Size     TTL
 projects          1        67   4.0 KB   5m
 users             1        29   2.2 KB   1d
```

## Clearing an instance you have no token for

Entries are filed under the URL *and* the token, so two accounts on the same
host never share them and rotating a token retires what was cached under the
old one. That also means `cache clear` only finds the directory belonging to
the token currently configured: with a different one it removes nothing and
says so, rather than appearing to have cleared anything.

`--url` clears every account cached for a host instead, which is what a
destroyed or rebuilt instance calls for. It needs no token, takes a bare
hostname or a full URL, and completes from the hosts already cached:

```bash
phabfive cache clear --url phorge.example.com
phabfive cache clear --url <TAB>
```

## Turning it off

```bash
export PHAB_CACHE=0          # takes effect immediately, including mid-completion
```

or in `~/.config/phabfive.yaml`:

```yaml
PHAB_CACHE: false
```

There is no `--no-cache` flag, because a flag cannot be typed inside a
completion — which is where most of the cache is used. `PHAB_CACHE=0` makes the
commands ask for the status map every time too.

| Setting | Default | Meaning |
|---|---|---|
| `PHAB_CACHE` | `true` | Turn caching off with `0` or `false` |
| `PHAB_CACHE_TTL` | `0` | Seconds; overrides every entry in the table above |
| `PHAB_CACHE_DIR` | unset | Somewhere other than `~/.cache/phabfive` |

Each works as an environment variable or as a key in `~/.config/phabfive.yaml`.

## When something goes wrong

The cache never breaks completion. A corrupt, unreadable, truncated or
stale entry, a cache directory that cannot be written, or a missing `$HOME`
all behave as a cache miss: phabfive asks the server, exactly as it would
with caching switched off.
