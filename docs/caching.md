# Caching

Shell completion asks the server for the values it offers. Without a cache
every TAB is a round trip — three of them, in fact, because connecting costs a
`conduit.query` and a `user.whoami` before the lookup itself runs. Pressing TAB
twice, which is how bash is normally driven, pays for all of it twice.

Phabfive caches those lookups on disk, so the first TAB on an option is the only
one that waits.

## What is cached

Only the lookups that shell completion makes. Today that is usernames, for
`--assign`, `--assigned`, `--subscribe` and `--author`.

Nothing else is cached. Phabfive does not cache API responses in general, and
the cache is never consulted when a command actually does something — `maniphest
show` and friends always ask the server.

**Secrets are never cached.** Caching is opt-in per call site rather than a
wrapper around the API client, so a response can only be stored by code written
to store it. Nothing in `passphrase` does, and credential values have no
completion that queries the server.

## Where it lives

```
~/.cache/phabfive/v1/<instance>/users/*.json
```

Directories are created `0700` and entries `0600`. Each instance gets its own
directory, keyed by URL and API token, so two accounts on one host never share
entries and rotating a token retires everything cached under the old one. The
token itself is never written out — only a hash of it names the directory.

Entries hold a username, a real name and whether the account is disabled. No
PHIDs, no policies, no dates.

## How long entries live

| Data | Fresh for |
|---|---|
| Usernames, spaces | 24 hours |
| Priorities, statuses | 7 days |
| Projects, board columns | 5 minutes |

User lists change rarely and instance configuration barely changes at all, while
projects and their columns come and go.

Because the `nameLike` lookup matches substrings, one lookup answers every
prefix that extends it: after `--assign <TAB>` has fetched once, `--assign
so<TAB>` and `--assign son<TAB>` are answered from the same entry without
touching the network.

## Staleness

Completion is advisory. A name that is not offered can still be typed, and the
server resolves it normally — so the worst a stale entry does is make you type
a colleague's name in full. If somebody has just joined, left or been renamed:

```bash
phabfive cache clear
```

## Commands

```bash
phabfive cache info          # where the cache is, how much is in it, how old
phabfive cache clear         # drop the configured instance's entries
phabfive cache clear --all   # drop every instance's, works without credentials
```

`cache info` reports sizes and ages only; it never prints what was cached.

## Turning it off

```bash
export PHAB_CACHE=0          # takes effect immediately, including mid-completion
```

or in `~/.config/phabfive.yaml`:

```yaml
PHAB_CACHE: false
```

There is no `--no-cache` flag, because a flag cannot be typed inside a
completion — which is the one place the cache is used.

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
