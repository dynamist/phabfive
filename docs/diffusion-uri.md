# Diffusion URIs

A repository in Phorge is not reached through one address. It carries a list of
URIs, and each one says something different about how the repository is served,
followed or pushed. `phabfive diffusion uri` is the command set for reading and
changing that list.

## Overview

Every URI on a repository answers four questions, and they are independent of
one another:

| Dimension | Field | Values | What it decides |
| --- | --- | --- | --- |
| Origin | `Origin` | `built-in`, `external` | Whether Phorge generated the URI or somebody added it |
| I/O | `I/O` | `observe`, `mirror`, `read`, `readwrite`, `none` | What traffic flows over it, and in which direction |
| Display | `Display` | `always`, `never` | Whether the web UI offers it as a clone URL |
| Disabled | `Disabled` | `true`, `false` | Whether the URI is in service at all |

`Disabled` overrides the other three. A disabled URI neither serves clones nor
is pulled from, whatever its I/O says, so `uri list` reports its `Role` as
`disabled` rather than as what its I/O would otherwise mean:

```bash
phabfive --format=table diffusion uri list R10 --disabled
```

```
URI                                        Origin    Role      I/O           Display       Credential  Disabled
git@example.com:dynamist/throwaway375.git  external  disabled  mirror (set)  always (set)  K3          true
```

The `I/O` cell still reads `mirror`. That is the stored setting, and it is what
the URI goes back to when it is enabled again — but nothing is being mirrored
while `Disabled` is `true`.

## Listing URIs

`uri list` prints one record per URI, with all four dimensions:

```bash
phabfive diffusion uri list R11
```

```
- URI: http://phorge.localhost/diffusion/11/e2e-047acb25.git
  Origin: built-in
  Role: clone + push
  I/O:
    Raw: default
    Default: readwrite
    Effective: readwrite
  Display:
    Raw: default
    Default: never
    Effective: never
  Credential: (none)
  Disabled: false
```

`Role` is not a field Phorge stores. It is derived from the effective I/O value
(and from `Disabled`, which wins), and it is derived once, so every output
format answers "what is this URI for" the same way.

`--format=table` flattens each of `I/O` and `Display` into one cell, spelling
out whether the value was chosen or inherited:

```bash
phabfive --format=table diffusion uri list R11
```

```
URI                                                    Origin    Role          I/O                  Display           Credential  Disabled
http://phorge.localhost/diffusion/11/e2e-047acb25.git  built-in  clone + push  readwrite (default)  never (default)   (none)      false
http://phorge.localhost/source/e2e-047acb25.git        built-in  clone + push  readwrite (default)  always (default)  (none)      false
```

`--format=json`, `--format=jsonl` and `--format=yaml` keep all three levels:

```bash
phabfive --format=json diffusion uri list R11 | jq '.[0]["I/O"]'
```

```json
{
  "Raw": "default",
  "Default": "readwrite",
  "Effective": "readwrite"
}
```

A repository with no URIs to show, and one whose URIs the filters all rejected,
both print nothing and exit `0`. That includes `--format=json`, which prints no
output rather than an empty array.

`repo show --show-uris` and `repo list --show-uris` nest the same record under
each repository, so the three commands cannot disagree about the same URI. Two
differences: neither names the credential, and `repo list --format=table`
collapses the nested records to a list of addresses, having a row per repository
rather than per URI.

## What each I/O value means

### `observe`

Phorge pulls from the remote. The remote is the source of truth; the copy in
Phorge follows it and nothing is pushed back. This is what a repository that
lives on GitHub but is browsed in Diffusion uses.

### `mirror`

Phorge pushes to the remote. **This is the one phabfive setting whose effect
leaves the Phorge server and writes to somebody else's git host.** Everything
else in the tool reads or writes objects inside the instance; a mirror URI makes
the instance push refs into a repository you may not own, on a schedule nobody
at the far end asked about.

Set it deliberately, preview with `--dry-run`, and check the remote is the one
you meant before applying. See [#354](https://github.com/dynamist/phabfive/issues/354).

### `read` and `readwrite`

Phorge serves the repository itself over this address. `readwrite` accepts
pushes, `read` does not. These are the values built-in URIs carry; an external
URI cannot take them (see [Built-in URIs](#built-in-uris)).

### `none`

The URI is recorded but carries no traffic in either direction. It is how a
remote stays written down after it stops being used.

## How `default` resolves

Phorge stores three values for `I/O` and for `Display`, and phabfive publishes
all three:

- **Raw** — what is written on the URI. Often the literal string `default`.
- **Default** — what the URI would inherit, which depends on whether the
  repository is hosted and whether the URI is built-in.
- **Effective** — what is actually in force.

`Effective` alone cannot say whether a value was chosen or inherited, and `Raw`
is `default` often enough to be useless on its own, which is why the record
carries all three.

The part that surprises people is that `Default` is not a constant. It moves
when the repository changes. Here is the same pair of built-in URIs on one
repository, before and after it was turned into an observer:

```bash
# Hosted: Phorge serves the repository, so its built-in URIs default to readwrite
phabfive --format=table diffusion uri list uridoc377
```

```
URI                                                 Origin    Role          I/O                  Display           Credential  Disabled
http://phorge.localhost/diffusion/16/uridoc377.git  built-in  clone + push  readwrite (default)  never (default)   (none)      false
http://phorge.localhost/source/uridoc377.git        built-in  clone + push  readwrite (default)  always (default)  (none)      false
```

```bash
# After adding an observe URI: the repository follows a remote, so the same
# built-in URIs now default to read
phabfive --format=table diffusion uri list uridoc377 --builtin
```

```
URI                                                 Origin    Role               I/O         Display      Credential  Disabled
http://phorge.localhost/diffusion/16/uridoc377.git  built-in  clone (read-only)  read (set)  never (set)  (none)      false
http://phorge.localhost/source/uridoc377.git        built-in  clone (read-only)  read (set)  never (set)  (none)      false
```

Two different things happened between those two listings, and the three-level
record is what tells them apart. `uri create` wrote `read` onto both URIs, so
`Raw` went from `default` to `read` — that is the `(set)` in the table. Underneath
that, `Default` *also* moved from `readwrite` to `read`, because the repository is
no longer hosted. Had `uri create` not written anything, the effective value would
have changed anyway.

The same distinction shows up on `Display`, and there the two levels disagree:

```bash
phabfive --format=json diffusion uri list uridoc377 --builtin \
  | jq '.[] | {URI, Display}'
```

```json
{
  "URI": "http://phorge.localhost/diffusion/16/uridoc377.git",
  "Display": {
    "Raw": "never",
    "Default": "never",
    "Effective": "never"
  }
}
{
  "URI": "http://phorge.localhost/source/uridoc377.git",
  "Display": {
    "Raw": "never",
    "Default": "always",
    "Effective": "never"
  }
}
```

The second URI would be shown as a clone URL if it were left to inherit —
`Default` is `always` — and it is hidden because something wrote `never` onto it.
The first would be hidden either way. `Effective` says `never` for both and
cannot tell you which is which.

## Built-in URIs

Phorge generates built-in URIs from the repository's own identifiers — its ID,
its short name and its callsign — one per identifier per protocol it serves.
They are marked `Origin: built-in`, they have no credential, and they differ
from external URIs in three ways.

**They cannot be re-addressed.** The address is computed from the repository, not
stored, so there is nothing to change. `uri edit --uri` against a built-in URI is
accepted and reports the change, but the URI is unaffected:

```bash
phabfive diffusion uri edit uridoc377 \
  http://phorge.localhost/source/uridoc377.git \
  --uri http://example.com/other.git --yes
```

```
http://phorge.localhost/R16 (uridoc377):
  URI: http://phorge.localhost/source/uridoc377.git → http://example.com/other.git
```

```bash
phabfive --format=table diffusion uri list uridoc377 --builtin
```

```
URI                                                 Origin    Role               I/O         Display      Credential  Disabled
http://phorge.localhost/diffusion/16/uridoc377.git  built-in  clone (read-only)  read (set)  never (set)  (none)      false
http://phorge.localhost/source/uridoc377.git        built-in  clone (read-only)  read (set)  never (set)  (none)      false
```

The address is what it always was. phabfive does not currently warn about this.

**Renaming the repository rewrites them.** The address is derived from the
repository's *clone name*, which is its short name if it has one and its name if
it does not. Changing that name changes every built-in URI at once, and
`repo edit` names each one, with the address it has now and the address it would
get:

```bash
phabfive diffusion repo edit uridoc377 --short-name uridoc377-renamed --dry-run
```

```
[DRY RUN] Would apply to http://phorge.localhost/R16 (uridoc377):
  Short name: uridoc377 → uridoc377-renamed
  Built-in URI: http://phorge.localhost/diffusion/16/uridoc377.git → http://phorge.localhost/diffusion/16/uridoc377-renamed.git
  Built-in URI: http://phorge.localhost/source/uridoc377.git → http://phorge.localhost/source/uridoc377-renamed.git
```

A repository with a callsign carries a third one, `/diffusion/<callsign>/<name>.git`,
and it is listed too — being the stable human-readable address, it is the one most
likely to be in somebody's git remote.

Because the clone name falls back to the name, `--name` moves the URIs of a
repository that never got a short name, and `repo edit` says so there as well:

```bash
phabfive diffusion repo edit uridoc405 --name uridoc405-renamed --dry-run
```

```
[DRY RUN] Would apply to http://phorge.localhost/R24 (uridoc405):
  Name: uridoc405 → uridoc405-renamed
  Built-in URI: http://phorge.localhost/diffusion/24/uridoc405.git → http://phorge.localhost/diffusion/24/uridoc405-renamed.git
```

It carries only the one: a `/source/` address is built from the short name, so a
repository without one does not have that shape at all — another reason to read
the list off the repository rather than assume it.

The list is read off the URIs the repository actually carries, so a repository
whose view policy hides them (see the note below) gets a warning that says so
rather than a list that is quietly short:

```
  Built-in URIs: not visible on this repository, and any it has move too
```

Anyone who has cloned from an old address has a remote that no longer resolves.

**They take a different set of I/O values.** Phorge validates I/O per URI:
a built-in URI serves the repository, so it accepts `default`, `none`, `read`
and `readwrite`; an external URI carries a remote, so it accepts `default`,
`none`, `observe` and `mirror`. Asking for the wrong one is rejected by the
server:

```bash
phabfive diffusion uri edit uridoc377 git@github.com:dynamist/phabfive.git --io=read --yes
```

```
ERR-CONDUIT-CORE: Validation errors:
  - Value "read" is not a valid IO setting for this URI. Available types for this URI are: default, none, observe, mirror.
```

The rejection comes from the server rather than from phabfive, so `uri edit`
currently prints a Python traceback above that message. The last line is the
one to read.

!!! note

    Built-in URIs are only reported for protocols the instance actually serves.
    The local development instance in [Phorge Setup](phorge-setup.md) has no SSH
    port and leaves `diffusion.allow-http-auth` unset, so it serves HTTP only to
    publicly-visible repositories — and `uri list R1` on a seeded repository
    prints nothing at all. That is instance configuration, not a missing URI.
    Making a repository public is what brings its built-in URIs into view there:

    ```bash
    phabfive diffusion repo edit R10 --visible-to public --dry-run
    ```

    ```
    [DRY RUN] Would apply to http://phorge.localhost/R10 (urimatrix375):
      Visible To: All Users → Public (No Login Required)
    ```

## `uri create` demotes every other URI

Phorge publishes one clone URI per repository. `uri create` respects that by
demoting **every URI already on the repository** before adding the new one:
`display=never`, and `io=read` for built-in URIs or `io=none` for external ones.

Nothing in the arguments hints at this, so preview it. `--dry-run` names each
URI it would demote:

```bash
phabfive diffusion uri create K3 uridoc377 git@github.com:dynamist/phabfive.git \
  --observe --dry-run
```

```
[DRY RUN] Would create git@github.com:dynamist/phabfive.git:
  New URI: (none) → git@github.com:dynamist/phabfive.git
  I/O: (none) → observe
  Display: (none) → always
  Credential: (none) → K3
  Demotes http://phorge.localhost/diffusion/16/uridoc377.git: io=readwrite, display=never → io=read, display=never
  Demotes http://phorge.localhost/source/uridoc377.git: io=readwrite, display=always → io=read, display=never
```

The last demotion in that plan is the one that costs something:
`/source/uridoc377.git` was the repository's published clone URL — it is the only
URI on the repository with `display=always` — and adding an observe URI
un-publishes it.

The demotion is not atomic and cannot be — Conduit has no transaction spanning
several objects, so each demotion is a separate edit. A failure part of the way
through leaves some URIs demoted and no new URI to replace them. Read the dry
run before committing to the apply.

A URI already at the target state is left alone and is not named in the plan.

## Worked example: observing a GitHub remote

The common case: a repository that exists in Phorge should follow a remote on
GitHub instead of being served by Phorge.

**1. Create the repository, or start from an existing one.** A new repository is
hosted, and its built-in URIs default to `readwrite`:

```bash
phabfive diffusion repo create uridoc377 --yes
```

```
uridoc377:
  Name: (none) → uridoc377
  Short name: (none) → uridoc377
  VCS: (none) → git
  Status: (none) → active
  Built-in URIs: (none) → /source/uridoc377.git
```

```bash
phabfive --format=table diffusion uri list uridoc377
```

```
URI                                                 Origin    Role          I/O                  Display           Credential  Disabled
http://phorge.localhost/diffusion/16/uridoc377.git  built-in  clone + push  readwrite (default)  never (default)   (none)      false
http://phorge.localhost/source/uridoc377.git        built-in  clone + push  readwrite (default)  always (default)  (none)      false
```

**2. Check which credential Phorge will pull with.** The URI needs an SSH private
key from Passphrase, named by monogram:

```bash
phabfive passphrase search --type=ssh
```

```
- Link: http://phorge.localhost/K3
  Type: SSH Key
  Name: Observe key
  Username: git
  Created: '2026-09-20T05:43:28'
  Modified: '2026-09-20T05:43:28'
- Link: http://phorge.localhost/K2
  Type: SSH Key
  Name: Mirror key, no Conduit access
  Username: git
  Created: '2026-09-20T05:43:27'
  Modified: '2026-09-20T05:43:27'
```

The credential's secret is never read by `uri create` or `uri list` — only its
monogram is.

**3. Preview the change.** This is the step that shows the demotion:

```bash
phabfive diffusion uri create K3 uridoc377 git@github.com:dynamist/phabfive.git \
  --observe --dry-run
```

```
[DRY RUN] Would create git@github.com:dynamist/phabfive.git:
  New URI: (none) → git@github.com:dynamist/phabfive.git
  I/O: (none) → observe
  Display: (none) → always
  Credential: (none) → K3
  Demotes http://phorge.localhost/diffusion/16/uridoc377.git: io=readwrite, display=never → io=read, display=never
  Demotes http://phorge.localhost/source/uridoc377.git: io=readwrite, display=always → io=read, display=never
```

**4. Apply it.** The command prints the URI it created:

```bash
phabfive diffusion uri create K3 uridoc377 git@github.com:dynamist/phabfive.git \
  --observe --yes
```

```
git@github.com:dynamist/phabfive.git
```

**5. Confirm the result.** The GitHub remote is the observed URI and the
published clone URL; the built-in URIs are read-only and hidden:

```bash
phabfive --format=table diffusion uri list uridoc377
```

```
URI                                                 Origin    Role                    I/O            Display       Credential  Disabled
git@github.com:dynamist/phabfive.git                external  Phorge pulls from here  observe (set)  always (set)  K3          false
http://phorge.localhost/diffusion/16/uridoc377.git  built-in  clone (read-only)       read (set)     never (set)   (none)      false
http://phorge.localhost/source/uridoc377.git        built-in  clone (read-only)       read (set)     never (set)   (none)      false
```

The repository now reports itself as not hosted, which is what moved the
built-in URIs' inherited I/O from `readwrite` to `read`:

```bash
phabfive --format=json diffusion repo show uridoc377 | jq '.[0].Repository.Hosted'
```

```json
false
```

## Filtering

`uri list` takes filters that combine with AND. A filter left out is not
applied, so no arguments at all is every URI.

### `--builtin` and `--external`

Split the list by origin. The two cannot be combined with each other.

```bash
phabfive --format=table diffusion uri list uridoc377 --external
```

```
URI                                   Origin    Role                    I/O            Display       Credential  Disabled
git@github.com:dynamist/phabfive.git  external  Phorge pulls from here  observe (set)  always (set)  K3          false
```

```bash
phabfive --format=table diffusion uri list uridoc377 --builtin
```

```
URI                                                 Origin    Role               I/O         Display      Credential  Disabled
http://phorge.localhost/diffusion/16/uridoc377.git  built-in  clone (read-only)  read (set)  never (set)  (none)      false
http://phorge.localhost/source/uridoc377.git        built-in  clone (read-only)  read (set)  never (set)  (none)      false
```

### `--io`

Keep the URIs whose I/O is the value given, **either as set or as in force**.
That is deliberate: they are two true and different answers to "is this an
observe URI", and matching both is what makes `--io=default` useful at all —
`default` is never an effective value.

```bash
phabfive --format=table diffusion uri list uridoc377 --io=observe
```

```
URI                                   Origin    Role                    I/O            Display       Credential  Disabled
git@github.com:dynamist/phabfive.git  external  Phorge pulls from here  observe (set)  always (set)  K3          false
```

`--io=default` finds the URIs that inherit their I/O rather than setting it:

```bash
phabfive --format=table diffusion uri list R11 --io=default
```

```
URI                                                    Origin    Role          I/O                  Display           Credential  Disabled
http://phorge.localhost/diffusion/11/e2e-047acb25.git  built-in  clone + push  readwrite (default)  never (default)   (none)      false
http://phorge.localhost/source/e2e-047acb25.git        built-in  clone + push  readwrite (default)  always (default)  (none)      false
```

`--io=readwrite` finds the URIs that do read-write however they came by it, so
on that repository it matches the same two:

```bash
phabfive --format=table diffusion uri list R11 --io=readwrite
```

```
URI                                                    Origin    Role          I/O                  Display           Credential  Disabled
http://phorge.localhost/diffusion/11/e2e-047acb25.git  built-in  clone + push  readwrite (default)  never (default)   (none)      false
http://phorge.localhost/source/e2e-047acb25.git        built-in  clone + push  readwrite (default)  always (default)  (none)      false
```

Valid values are `default`, `observe`, `mirror`, `read`, `readwrite` and `none`.
`never` is accepted as an older spelling of `none`. Anything else is rejected
before a request is made:

```bash
phabfive diffusion uri list R3 --io=bogus
```

```
ERROR: 'bogus' is not valid. Valid IO values are 'default', 'observe', 'mirror', 'read', 'readwrite' or 'none'
```

### `--display`

The same matching, on the display dimension. Valid values are `default`,
`always` and `never`; `hidden` is accepted as an older spelling of `never`.

```bash
phabfive --format=table diffusion uri list R11 --display=always
```

```
URI                                              Origin    Role          I/O                  Display           Credential  Disabled
http://phorge.localhost/source/e2e-047acb25.git  built-in  clone + push  readwrite (default)  always (default)  (none)      false
```

### `--disabled` and `--enabled`

Split the list by whether the URI is in service. The two cannot be combined with
each other. Taking the observe URI out of service:

```bash
phabfive diffusion uri edit uridoc377 git@github.com:dynamist/phabfive.git --disable --yes
```

```
http://phorge.localhost/R16 (uridoc377):
  URI: git@github.com:dynamist/phabfive.git
  Disabled: False → True
```

```bash
phabfive --format=table diffusion uri list uridoc377 --disabled
```

```
URI                                   Origin    Role      I/O            Display       Credential  Disabled
git@github.com:dynamist/phabfive.git  external  disabled  observe (set)  always (set)  K3          true
```

```bash
phabfive --format=table diffusion uri list uridoc377 --enabled
```

```
URI                                                 Origin    Role               I/O         Display      Credential  Disabled
http://phorge.localhost/diffusion/16/uridoc377.git  built-in  clone (read-only)  read (set)  never (set)  (none)      false
http://phorge.localhost/source/uridoc377.git        built-in  clone (read-only)  read (set)  never (set)  (none)      false
```

The remaining examples in this section assume it has been enabled again with
`--enable`.

### `--clone`

Keep only the URIs the web UI offers as clone URLs, which is
`Display.Effective == always`. It predates the rest of the filters and is kept
as its own flag; `--display=always` is the general form, and differs in also
matching a URI whose *raw* display is `always`.

```bash
phabfive --format=table diffusion uri list uridoc377 --clone
```

```
URI                                   Origin    Role                    I/O            Display       Credential  Disabled
git@github.com:dynamist/phabfive.git  external  Phorge pulls from here  observe (set)  always (set)  K3          false
```

### Combining filters

Filters AND together:

```bash
phabfive --format=table diffusion uri list uridoc377 --external --io=observe --enabled
```

```
URI                                   Origin    Role                    I/O            Display       Credential  Disabled
git@github.com:dynamist/phabfive.git  external  Phorge pulls from here  observe (set)  always (set)  K3          false
```

## Editing a URI

`uri edit` changes one URI on one repository. It identifies the URI by its
address, so the repository has to be named too — two repositories can carry the
same remote. With no edit option it refuses, with `Please input minimum one
option`.

```bash
# Stop using a URI without deleting it
phabfive diffusion uri edit uridoc377 git@github.com:dynamist/phabfive.git --disable --yes

# Put it back
phabfive diffusion uri edit uridoc377 git@github.com:dynamist/phabfive.git --enable --yes

# Change which credential Phorge pulls with
phabfive diffusion uri edit uridoc377 git@github.com:dynamist/phabfive.git --cred K2 --dry-run

# Publish or un-publish it as a clone URL
phabfive diffusion uri edit uridoc377 git@github.com:dynamist/phabfive.git --display=never --dry-run
```

Disabling reports the dimension it changed and nothing else:

```
http://phorge.localhost/R16 (uridoc377):
  URI: git@github.com:dynamist/phabfive.git
  Disabled: False → True
```

An edit that would leave the URI where it already is makes no request, whether
or not `--dry-run` was given:

```bash
phabfive diffusion uri edit uridoc377 git@github.com:dynamist/phabfive.git --io=observe --yes
```

```
http://phorge.localhost/R16 (uridoc377): No changes (already at target state)
```

Unlike `uri create`, `uri edit` touches only the URI named. It does not demote
anything.

## Error messages

### `ERROR: Repository 'X' not found`

`uri list` could not resolve the repository. It accepts a monogram (`R16`), a
callsign or a short name. A repository that exists but has no URIs to show is
not this error — it is empty output and exit `0`.

### `Value "read" is not a valid IO setting for this URI`

The I/O value does not apply to that kind of URI. Phorge lists the ones that do
in the same message; see [Built-in URIs](#built-in-uris).

### `ERROR: Cannot specify both --builtin and --external`

The origin filters are a pair, and so are `--disabled` and `--enabled`. Pick one
side, or leave both out to get everything.

### `ERROR: Must specify either --observe or --mirror`

`uri create` adds a URI that carries a remote, and will not guess which
direction the traffic goes. It takes only those two; `read`, `readwrite` and
`none` belong to URIs that already exist, and are set with `uri edit --io`.

## See Also

- [Maniphest CLI](maniphest-cli.md) — the task commands
- [Phorge Setup](phorge-setup.md) — run a local Phorge instance to try this against
- [Development Guide](development.md) — set up a development environment
