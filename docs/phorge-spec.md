# The Phorge spec format

A **spec** is one document that describes Phorge objects: the ones to create, or
the ones to find. It is written in YAML, JSON, JSONL or TOML, it is meant to be
kept in version control beside the code it is about, and it is read by programs.

This page defines the format. It is normative and it names no program: every
rule here belongs to the format, and anything that is one reader's behaviour has
been left out on purpose. An implementation should be able to read this page and
nothing else.

```yaml
spec: phorge/v1alpha1
kind: create
metadata:
  name: rotate-host-keys
  description: The work that follows a host key rotation.
  version: "1"
  author: infrastructure

tasks:
  - title: Rotate the build server host keys
    priority: high
    assignment: tommy.svensson
    projects:
      - Security
      - Infrastructure
```

---

## Versioning

The format is named after the domain rather than after any tool, and the name
carries its revision:

```yaml
spec: phorge/v1alpha1
```

**`v1alpha1` promises nothing.** Keys may be renamed, given new meanings or
removed between releases with no deprecation cycle and no migration path. There
is deliberately no compatibility machinery: a document naming a revision the
reader does not implement is **refused by name**, never half-parsed and never
read as a best effort. That refusal is the whole compatibility story of the
alpha, and it is what makes churn cheap rather than dangerous.

`phorge/v1` is promoted only once every object type and both kinds have settled.
From `v1` on, a key that exists keeps its meaning, a key is removed only after a
release that warns about it, and a document written for `v1` is readable by every
later `v1.x` reader.

The `spec:` key is optional, so a document that leaves it out is read as the
reader's own revision. Writing it is strongly recommended, and required for any
document that outlives the session that produced it: it is the only thing that
turns "this stopped working" into "this was written for an older revision".

---

## Serializations

One format, four spellings. Whichever is used, a document parses to the same
thing: a mapping of plain scalars, lists and mappings.

| Format | One document | Several documents |
|---|---|---|
| YAML | yes | `---` separators |
| JSON | yes | a list at the root |
| JSONL | n/a | one document per line |
| TOML | yes | list keys only |

Several documents in one file is a **serialization convenience, not a second
shape**. The documents are folded into one before anything else looks at them.
The envelope is declared once — by whichever document declares it, and two
documents that disagree about an envelope key are refused rather than resolved
in favour of one. The list keys (`tasks:`, `projects:`, `pastes:`, `searches:`)
concatenate in file order. Those keys are what every one of the four
serializations can express, so the folded document is always writable in all
four.

The same spec, four ways:

```yaml
spec: phorge/v1alpha1
kind: create
tasks:
  - title: Rotate the staging database credentials
    priority: high
```

```json
{
  "spec": "phorge/v1alpha1",
  "kind": "create",
  "tasks": [
    {"title": "Rotate the staging database credentials", "priority": "high"}
  ]
}
```

```jsonl
{"spec": "phorge/v1alpha1", "kind": "create"}
{"tasks": [{"title": "Rotate the staging database credentials", "priority": "high"}]}
```

```toml
spec = "phorge/v1alpha1"
kind = "create"

[[tasks]]
title = "Rotate the staging database credentials"
priority = "high"
```

### Three limitations, documented rather than worked around

- **YAML anchors** (`&name` / `*name`) are a YAML feature and resolve only
  there. A document that relies on them is not portable to the other three.
  [Variables](#variables) are the cross-format way to write a value once.
- **JSON and JSONL carry no comments.** A document in either has nowhere to say
  why it is the way it is, which is why `metadata.description` matters more
  there than it does in YAML.
- **TOML has no null.** An explicitly absent value is expressed by leaving the
  key out. There is no spelling of "present and empty" in TOML, and the format
  does not invent one.

---

## The envelope

Three keys describe the document itself rather than the objects in it.

<!-- BEGIN GENERATED envelope -->

| Key | Required | Meaning |
|---|---|---|
| `kind` | optional | `create` or `search`. Absent means inferred from the body keys. |
| `metadata` | optional | What the *file* is, for a person reading it. Nothing acts on it. |
| `spec` | optional | The revision of the format, `phorge/v1alpha1`. Absent means the reader's own revision is assumed. |

`metadata:` holds:

| Key | Value | Meaning |
|---|---|---|
| `name` | text | A short name for the file. Not a Phorge object's name. |
| `description` | text | What the file is for, in a sentence or two. |
| `version` | text | The file's own revision, as text. `version: 2.1` is a float. |
| `author` | text | Who maintains the file. |

Every key above is optional, and a `metadata:` key outside this table is reported as a warning rather than dropped.

Accepted `spec:` values: `phorge/v1alpha1`.

<!-- END GENERATED envelope -->

`metadata:` describes the **file**. It is never read as a property of anything
created or searched, and nothing in it reaches Phorge. The `title:` and
`description:` of a `searches:` item are a different thing that happens to share
two words: they label one search's results. Do not merge them.

### Kind inference

`kind:` may be left out, and is then inferred from which family of body keys the
document holds: `search:`/`searches:` means `search`, and
`tasks:`/`projects:`/`pastes:`/`passphrases:` means `create`. A document holding
both families is **refused**, not guessed; a document holding neither is refused
as well.

Inference is an ergonomic convenience and not a compatibility promise: it exists
so that a short document need not state the obvious. A reader may also be told
the kind out of band, by whoever is holding the document, which is the only way
to read a search document that carries nothing but `title:` and `description:` —
no body key, so nothing to infer from.

---

## The two kinds

`kind:` carries the **verb**, never the object type. That is what lets one
document create a project and the tasks tagged into it, and what lets one
document search three applications.

<!-- BEGIN GENERATED kinds -->

| `kind:` | Body keys | What it holds |
|---|---|---|
| `create` | `tasks:`, `projects:`, `pastes:` | One section per object type, each a list of items. An item creates something when it names it: a paste by its `title:`; a project by its `name:`; a task by its `title:`. |
| `search` | `searches:` | One list of items, each naming what it searches with its own `type:`: `passphrase`, `paste`, `project`, `task`. `type:` defaults to `task`. |

Refused by name rather than ignored: `passphrases:` &mdash; passphrases cannot be created: Phorge exposes no passphrase.edit endpoint. Credentials must be created in the web UI. Passphrase search specs are supported.

<!-- END GENERATED kinds -->

A reader that does not implement one of these sections **refuses it by name**.
It never ignores a section, and it never plans it as something else: a document
that asks for something is answered either by doing it or by saying what could
not be done.

---

## Create documents

A create document holds one section per object type, each a list of items:

```yaml
spec: phorge/v1alpha1
kind: create
projects:
  - name: Observability Platform
pastes:
  - title: Rollout notes
    content: One agent per host.
tasks:
  - title: Deploy the metrics collector
```

An item **creates something when it names it**: a task or a paste by its
`title:`, a project by its `name:`. An item that names nothing creates nothing —
see [anchors](#anchors) for what such an item is for.

### The structural keys

Five keys of a create item are structure rather than a value. They describe how
items relate to each other, not what an object is like.

<!-- BEGIN GENERATED create-structural -->

| Key | Where | Value | Meaning |
|---|---|---|---|
| `id` | any item | `[A-Za-z0-9][A-Za-z0-9_.-]*` | Names this item inside this document, so `$id` elsewhere in it means the object this item creates. |
| `tasks` | task items | list of task items | Child tasks. Each child is created with this item as a parent. |
| `parent` | task items | `T123` | One task this item hangs off. |
| `parents` | task items | list of `T123` | Tasks this item hangs off. |
| `subtasks` | task items | list of `T123` | Tasks that hang off this item. |

<!-- END GENERATED create-structural -->

### Nesting

`tasks:` inside a task item holds its children. Nesting is a spelling of
`parents:`, not a second mechanism: each child is created carrying the parent it
is nested under, exactly as if it had been written flat with `parents: [$id]`.

```yaml
spec: phorge/v1alpha1
kind: create
tasks:
  - id: epic
    title: Stand up the observability platform
    priority: high
    tasks:
      - title: Deploy the metrics collector
      - title: Write the alerting runbook

  # The same relationship, written flat.
  - title: Record the rollout in the change log
    parents:
      - "$epic"
```

Nesting may be any depth. An item's children are created after it, because they
name it.

### Anchors

An item that names nothing — no `title:`, no `name:` — creates nothing. Its
`parent:` or `parents:` then names the **existing** objects its children hang
off:

```yaml
spec: phorge/v1alpha1
kind: create
tasks:
  - parent: T1
    tasks:
      - title: Rotate the build server host keys
      - title: Re-run the SSH configuration audit
```

T1 is not edited, not fetched for writing and not changed in any way. Each child
is created already carrying T1 as a parent.

### `.add`, never `.set`

**This is a rule of the format, not of any reader.** Every value a create item
gives to a collection — its projects, its subscribers, its parents, its
subtasks — is *added* to that collection. A document can never be answered by
replacing a collection with what it named.

The rule exists because the alternative is unrecoverable. Setting `subtasks` on
an object that already has some discards the rest, and Phorge keeps no record of
what was discarded. On an object being created the two are indistinguishable, so
the rule costs nothing where it is not needed and saves everything where it is —
an anchor names an existing object, and that is exactly where a replacing write
would destroy something.

A create document therefore has no spelling for "remove" and none for "replace".
A document that wants either is asking for an edit, which is not this kind.

### The keys of each object type

#### task

<!-- BEGIN GENERATED keys task/create -->

| Key | Value | Repeatable | Meaning |
|---|---|---|---|
| `commits` | commit reference | yes | Commits to attach, by rCALLSIGNhash, R1:hash, a bare hash or a PHID. |
| `title` | text | &mdash; | What it is called; the web UI labels this Name. |
| `description` | text | &mdash; | The body text, in remarkup. |
| `priority` | instance enum | &mdash; | Priority name, e.g. high or needs-triage. |
| `status` | instance enum | &mdash; | Status key, e.g. open or resolved. |
| `assignment` | user reference | &mdash; | Assignee: a username, @me, or a user PHID. |
| `subscribers` | user reference | yes | Subscribers: usernames, @me, or user PHIDs. |
| `projects` | project reference | yes | Projects to tag it into, by name, #hashtag or PHID. |
| `column` | instance enum | &mdash; | Workboard column to place it in; needs projects: as well. |
| `space` | Space reference | &mdash; | The Space to create it in, by name or monogram. |
| `visible-to` | policy | &mdash; | View policy: a keyword, #project, @user or PHID. |
| `editable-by` | policy | &mdash; | Edit policy: a keyword, #project, @user or PHID. |

<!-- END GENERATED keys task/create -->

#### project

<!-- BEGIN GENERATED keys project/create -->

| Key | Value | Repeatable | Meaning |
|---|---|---|---|
| `color` | enum: `red`, `orange`, `yellow`, `green`, `blue`, `indigo`, `violet`, `pink`, `grey`, `checkered` | &mdash; | The colour the project is shown in. |
| `icon` | instance enum | &mdash; | The icon the project is shown with. |
| `description` | text | &mdash; | The body text, in remarkup. |
| `space` | Space reference | &mdash; | The Space to create it in, by name or monogram. |
| `visible-to` | policy | &mdash; | View policy: a keyword, #project, @user or PHID. |
| `editable-by` | policy | &mdash; | Edit policy: a keyword, #project, @user or PHID. |
| `name` | text | &mdash; | The project's name, which is what its hashtag is derived from. |
| `slugs` | text | yes | Additional hashtags, with or without their '#'. |
| `members` | user reference | yes | Members: usernames, @me, or user PHIDs. |
| `parent` | project reference | &mdash; | Create it as a subproject of this project. |
| `milestone-of` | project reference | &mdash; | Create it as a milestone of this project; takes no icon or slugs. |
| `joinable-by` | policy | &mdash; | Join policy: a keyword, #project, @user or PHID. |

<!-- END GENERATED keys project/create -->

#### paste

<!-- BEGIN GENERATED keys paste/create -->

| Key | Value | Repeatable | Meaning |
|---|---|---|---|
| `title` | text | &mdash; | What it is called; the web UI labels this Name. |
| `subscribers` | user reference | yes | Subscribers: usernames, @me, or user PHIDs. |
| `projects` | project reference | yes | Projects to tag it into, by name, #hashtag or PHID. |
| `visible-to` | policy | &mdash; | View policy: a keyword, #project, @user or PHID. |
| `editable-by` | policy | &mdash; | Edit policy: a keyword, #project, @user or PHID. |
| `content` | text | &mdash; | The paste's text. |
| `language` | text | &mdash; | Language for syntax highlighting, e.g. python. |

<!-- END GENERATED keys paste/create -->

#### passphrase

<!-- BEGIN GENERATED keys passphrase/create -->

No `passphrases:` section exists. Passphrases cannot be created: Phorge exposes no passphrase.edit endpoint. Credentials must be created in the web UI. Passphrase search specs are supported.

<!-- END GENERATED keys passphrase/create -->

---

## Search documents

A search document holds one `searches:` list. Each item is one search, and says
for itself what it searches.

<!-- BEGIN GENERATED search-item -->

| Key | Meaning |
|---|---|
| `type` | What this item searches: `passphrase`, `paste`, `project`, `task`. Defaults to `task`. |
| `title` | A banner for *this search's* results. Not the file's name. |
| `description` | What this search is for, shown with the banner. |
| `search` | The filters, whose keys are this `type:`'s search keys. |

<!-- END GENERATED search-item -->

```yaml
spec: phorge/v1alpha1
kind: search
searches:
  - type: task
    title: Open blockers
    description: Everything still open, most important first.
    search:
      status: open
      order: priority:desc
      limit: 25
```

The `title:` and `description:` of an item label **that search's results**. They
say nothing about the file; `metadata:` is where that goes.

Several items in one document are independent: each is run on its own, and one
finding nothing says nothing about the next.

### The keys of each object type

A `search:` mapping's keys are those of its item's `type:`.

#### task

<!-- BEGIN GENERATED keys task/search -->

| Key | Value | Repeatable | Default | Sent as | Meaning |
|---|---|---|---|---|---|
| `text_query` | text | &mdash; | &mdash; | `query` | Free-text search in the title and description. |
| `tag` | project reference | &mdash; | &mdash; | `projects` | Project, workboard, hashtag, ID or PHID; wildcards allowed. |
| `include` | monogram: `T123` | yes | &mdash; | &mdash; | Tasks to force into the results whatever the filters say. |
| `exclude` | monogram: `T123` | yes | &mdash; | &mdash; | Tasks to drop from the results even when the filters match. |
| `assigned` | user reference | &mdash; | &mdash; | `assigned` | Assignee: a username, @me, or a user PHID. |
| `author` | user reference | &mdash; | &mdash; | `authorPHIDs` | Author: a username, @me, or a user PHID. |
| `space` | Space reference | &mdash; | &mdash; | `spaces` | Space monogram, name or pattern; wildcards allowed. |
| `created-after` | time | &mdash; | &mdash; | `createdStart` | Tasks created within TIME, e.g. 1h, 7d, 2w. |
| `created-before` | time | &mdash; | &mdash; | `createdEnd` | Tasks created more than TIME ago. |
| `updated-after` | time | &mdash; | &mdash; | `modifiedStart` | Tasks updated within TIME, e.g. 1h, 7d, 2w. |
| `updated-before` | time | &mdash; | &mdash; | `modifiedEnd` | Tasks updated more than TIME ago. |
| `visible-to` | policy | &mdash; | &mdash; | &mdash; | Only tasks whose view policy is exactly this. |
| `editable-by` | policy | &mdash; | &mdash; | &mdash; | Only tasks whose edit policy is exactly this. |
| `column` | pattern | &mdash; | &mdash; | `columnPHIDs` | Column transition filter, e.g. in:Backlog or never:Done. |
| `priority` | pattern | &mdash; | &mdash; | `priorities` | Priority transition filter, e.g. in:High or from:Low. |
| `status` | pattern | &mdash; | `open` | `statuses` | open, closed, any, or transition patterns ANDed with +. |
| `all` | boolean | &mdash; | `false` | &mdash; | **Deprecated**, write `status: any` instead. Deprecated spelling of status: any. |
| `show-history` | boolean | &mdash; | `false` | &mdash; | Display transition history. |
| `show-metadata` | boolean | &mdash; | `false` | &mdash; | Display filter match metadata. |
| `show-policy` | boolean | &mdash; | `false` | &mdash; | Display each result's policies. |
| `limit` | integer | &mdash; | `100` | &mdash; | Maximum results to return, 0 for all. |
| `order` | order | &mdash; | &mdash; | &mdash; | Sort as <field>[:asc\|:desc], e.g. updated:desc. |
| `ids` | monogram: `T123` | yes | &mdash; | `ids` | Only these tasks, with every other filter still applied. |
| `phids` | text | yes | &mdash; | `phids` | Only these task PHIDs, with every other filter still applied. |
| `subscriber` | user reference | yes | &mdash; | `subscribers` | Tasks a user is subscribed to: a username, @me, or a PHID. |
| `subtype` | text | yes | &mdash; | `subtypes` | Task subtype key, e.g. 'default' or one this instance defines. |
| `parent` | monogram: `T123` | yes | &mdash; | `parentIDs` | Subtasks of these tasks. |
| `subtask` | monogram: `T123` | yes | &mdash; | `subtaskIDs` | Parents of these tasks. |
| `has-parents` | boolean | &mdash; | &mdash; | `hasParents` | Only tasks that are a subtask of something. |
| `has-subtasks` | boolean | &mdash; | &mdash; | `hasSubtasks` | Only tasks that have subtasks. |
| `closed-by` | user reference | yes | &mdash; | `closerPHIDs` | Tasks closed by a user: a username, @me, or a user PHID. |
| `closed-after` | time | &mdash; | &mdash; | `closedStart` | Tasks closed within TIME, e.g. 1h, 7d, 2w. |
| `closed-before` | time | &mdash; | &mdash; | `closedEnd` | Tasks closed more than TIME ago. |

<!-- END GENERATED keys task/search -->

#### project

<!-- BEGIN GENERATED keys project/search -->

| Key | Value | Repeatable | Default | Sent as | Meaning |
|---|---|---|---|---|---|
| `text_query` | text | &mdash; | &mdash; | `query` | Free-text search in the title and description. |
| `show-policy` | boolean | &mdash; | `false` | &mdash; | Display each result's policies. |
| `limit` | integer | &mdash; | `100` | &mdash; | Maximum results to return, 0 for all. |
| `members` | user reference | yes | &mdash; | `members` | Projects one of these users is a member of. |
| `parents` | project reference | yes | &mdash; | `parents` | Direct subprojects and milestones of these projects. |
| `ancestors` | project reference | yes | &mdash; | `ancestors` | Everything anywhere beneath these projects. |
| `milestones` | boolean | &mdash; | &mdash; | `isMilestone` | true lists only milestones, false only what is not one. |
| `status` | enum: `active`, `archived`, `any` | &mdash; | `active` | `status` | active, archived, or any. |
| `icons` | instance enum | yes | &mdash; | &mdash; | Projects shown with any of these icons. |
| `colors` | enum: `red`, `orange`, `yellow`, `green`, `blue`, `indigo`, `violet`, `pink`, `grey`, `checkered` | yes | &mdash; | &mdash; | Projects shown in any of these colours. |
| `spaces` | Space reference | yes | &mdash; | `spaces` | Space monograms, names or patterns; none means the reader's default Space. |
| `show-members` | boolean | &mdash; | `false` | &mdash; | Display each project's members. |

<!-- END GENERATED keys project/search -->

#### paste

<!-- BEGIN GENERATED keys paste/search -->

| Key | Value | Repeatable | Default | Sent as | Meaning |
|---|---|---|---|---|---|
| `text_query` | text | &mdash; | &mdash; | `query` | Free-text search in the title and description. |
| `author` | user reference | &mdash; | &mdash; | `authors` | Author: a username, @me, or a user PHID. |
| `limit` | integer | &mdash; | `100` | &mdash; | Maximum results to return, 0 for all. |

<!-- END GENERATED keys paste/search -->

#### passphrase

<!-- BEGIN GENERATED keys passphrase/search -->

| Key | Value | Repeatable | Default | Sent as | Meaning |
|---|---|---|---|---|---|
| `text_query` | text | &mdash; | &mdash; | &mdash; | Free-text search in the title and description. |
| `limit` | integer | &mdash; | `100` | &mdash; | Maximum results to return, 0 for all. |
| `type` | text | &mdash; | &mdash; | &mdash; | Credential type: password, token, key or note. |

<!-- END GENERATED keys passphrase/search -->

The **Sent as** column names the Conduit `*.search` constraint a key's value is
sent as. A key with no constraint is applied by the reader over the records that
came back, which is a cost rather than a semantic difference. The constraint
names are per application and are not interchangeable: a task's author filter is
`authorPHIDs` and a paste's is `authors`, and the wrong one is refused by Conduit
with `ERR-INVALID-CONSTRAINT`.

---

## References

One grammar names things, and it runs across every key of every document that
holds a reference. Six spellings:

| Written | Names |
|---|---|
| `T123`, `P45`, `K7`, `R9` | An existing object, by monogram. The letter says which application. |
| `#platform` | An existing project, by hashtag. |
| `@alice`, `@me` | An existing user. |
| `PHID-TASK-…` | An existing object, by PHID. Never ambiguous. |
| `Platform Engineering` | An existing object, by name. |
| `$platform` | An object **this document creates**, by its `id:`. |

Four rules go with the table.

**A name that matches more than one object is an error.** It is never resolved
to the first, the newest or the best match. The document is refused, naming the
value, and the fix is to write the hashtag or the PHID instead.

**`@me` is a keyword.** It means whoever is running the document, on every
instance, including one that has a user whose username is `me`. The `@` is what
makes it a keyword: `alice` and `@alice` are the same user everywhere else, so
the account called `me` is named by writing `me` without the sigil. In a
[policy](#grammars) value, where an unprefixed name has never been valid, that
account is named by its PHID.

**`$id` is not a variable and must not look like one.** A variable is rendered
before anything is created; a `$ref` is filled in *as* the objects are created,
because the thing it names does not exist until then. Two things that resolve at
different times are spelled differently on purpose. `$` rather than `@` or `#`
because those two already mean user and project.

**A `$ref` has to agree about what it names.** `parents:` holds tasks, so a
`$ref` there naming a project the same document creates is an error — and one
that can be found without asking the instance anything, which is why it is found
before anything is created. Declaration order does not matter; a cycle is an
error.

---

## Variables

`variables:` declares values the rest of the document interpolates. It is
neither envelope nor body: nothing is created from it and nothing is searched
with it.

```yaml
spec: phorge/v1alpha1
kind: create
variables:
  sprint: "42"
  owner: mikael.wallin
  board: "Sprint {{ sprint }}"

tasks:
  - title: "{{ board }}: write the runbook"
    assignment: "{{ owner }}"
```

### Declaring

An entry's value is the variable's value. The one exception is a mapping whose
keys are exactly `{default: …}`, which declares the variable with a default
instead:

```yaml
variables:
  sprint: {default: "42"}     # declaration, default "42"
  workgroup: {lead: alice}    # a value that happens to be a mapping
  empty: {}                   # a value: the empty mapping
  owner:                      # declared, no value: must be supplied
```

The vocabulary is one word so that the rule stays stateable. A mapping that
means to be a declaration and misspells the key is a value, which is the price
of letting a mapping be a value at all.

A declared variable with no value and no default **must be supplied from
outside** the document. A value supplied from outside beats a declared default,
and a value supplied for a name the document never declares is still usable — so
a document may read `{{ sprint }}` and leave supplying it to whoever runs it.

### Interpolation

The interpolation syntax is Jinja2's. The **portable subset**, which every
implementation supports and which a document meant to be read by more than one
reader stays inside, is:

| Written | Means |
|---|---|
| `{{ name }}` | The value of `name`. |

That is the whole portable subset: a name, and nothing else. Anything beyond it
— filters, tests, arithmetic, attribute access, `{% … %}` statements — is
implementation-defined. A reader may support it, and one does; a document that
uses it is not portable.

In particular, **a filter is not a way to make a name optional.** Whether
`{{ sprint | default(12) }}` supplies a value for an undeclared `sprint` is a
question about the interpolation engine, and the undefined-name rule below is
settled against the *names a document mentions*, before any engine runs. A
document that needs `sprint` to be optional declares it optional — see the
declaration form above — rather than writing a filter and hoping.

Interpolation applies to **every string** in the document, at every depth, in
the body and in `variables:` alike. Mapping *keys* are keys of this format
rather than user text and are never interpolated.

### Order

Variables may refer to one another in any order: they are rendered in dependency
order, so a variable that names another is rendered after it. A **cycle is an
error**.

### An undefined name is an error

`{{ sprint_numbr }}` where nothing defines `sprint_numbr` is an error naming the
variable. It is **not** the empty string.

This is the single most important rule in this section, and it is stated as a
rule rather than as a default because the alternative has a body count:
`"Sprint {{ sprint_numbr }} planning"` renders as `"Sprint  planning"` under
Jinja2's own default, and the task is created, with the wrong title, and nothing
says so.

The opt-out is the **declaration**, not the interpolation: `sprint: {default: 12}`
under `variables:` is what makes `{{ sprint }}` optional. It is written once, where
the name is introduced, so every use of the name is covered by it and a reader can
settle the whole question by reading `variables:`.

---

## Grammars

Some values are written in a small grammar of their own.

<!-- BEGIN GENERATED grammar -->

| Value | Written as |
|---|---|
| time | `<number>[<unit>]`, units `h`, `d`, `w`, `m`, `y`, days when the unit is omitted |
| policy | `public`, `users`, `admin`, `no-one`, or `#project`, `@user`, `PHID-…` |
| order | `priority`, `priority:asc`, `priority:desc`, `updated`, `updated:asc`, `updated:desc`, `created`, `created:asc`, `created:desc`, `closed`, `closed:asc`, `closed:desc`, `title`, `title:asc`, `title:desc`, `relevance` |
| monogram | `R123` (diffusion), `T123` (maniphest), `K123` (passphrase), `P123` (paste) |
| pattern (`column`) | `from:`, `to:`, `in:`, `been:`, `never:`, the keywords `backward`, `forward` |
| pattern (`status`) | `from:`, `to:`, `in:`, `been:`, `never:`, the keywords `raised`, `lowered`, `open`, `closed`, `any` |
| pattern (`priority`) | `from:`, `to:`, `in:`, `been:`, `never:`, the keywords `raised`, `lowered` |
| local id | `$[A-Za-z0-9][A-Za-z0-9_.-]*` |

<!-- END GENERATED grammar -->

A **transition pattern** asks about an object's history, not only its current
state. `in:Blocked` is a task currently in that column, `been:Blocked` one that
ever was, `never:Done` one that never has been, and `from:Low` one that moved
away from that priority. Conditions combine with `+`, which is AND, and any
condition may be negated with `not:`.

`in:` is the one condition type that is a question about the *current* state, so
it is the only one a server-side constraint can answer. Every other condition
requires reading history, which is why a pattern made of them costs a great deal
more than a filter on the current value.

---

## Validation

A document is checked in **two layers**, and which layer a check belongs to is
decided by one question: *can this be settled from the document alone?*

### Layer 1: offline

No network, no credentials, no configuration. It runs in CI, in a pre-commit
hook and in an editor, on a machine that has never seen the instance.

It settles:

- the envelope: a revision that is read, a `kind:` that exists, `metadata:` keys
  that are text;
- every key: that this object type and verb define it, and that it is not a
  deprecated spelling of another — but see below, because this one has a
  precondition;
- every value's **shape**: text where text is meant, a time that parses, a
  pattern that parses, a policy that is a keyword or a sigil-prefixed reference,
  a monogram of an application the key accepts, a value inside a statically
  known set;
- the variables: declared, non-circular, and every `{{ name }}` defined;
- the local ids: unique, well-formed, every `$ref` naming one that is declared,
  of an object type the key admits, and with no cycle;
- a section naming an object type nothing creates.

**Project colours are settled here**, which looks like it belongs to the other
layer and does not: the colour keys are fixed in Phorge's own source, where
configuration relabels a colour but cannot add one.

**`unknown-key` has a precondition.** A reader may report a key as unknown only
for an (object type, verb) pair whose key set it declares **complete**. A pair
that has some keys declared and not others cannot distinguish "this key is
misspelled" from "this key is one I have not declared yet", and reporting the
second as the first would refuse documents that are correct. So a document with
a misspelled key on a pair no reader has finished is accepted by layer 1 and
answered later — by layer 2, or by whatever creates the object. Which pairs are
complete is a property of the reader and not of the format; a reader states its
own list.

### Layer 2: online

Everything only the instance can answer. It needs credentials, and it runs only
when layer 1 found no error — there is nothing to gain by asking the instance
about a value already known to be the wrong shape.

It is the layer responsible for: users, projects (and whether a name is
ambiguous), Spaces, project icons, statuses, priorities, workboard columns,
monograms that must exist, and whether a hashtag is already taken.

Two of those — statuses and priorities — have codes in the vocabulary below
that the reference reader does not yet emit; it refuses such a value when it
comes to create the object instead, which is later and says less. See
[Implementations](#implementations).

These are **instance-defined**, which is exactly why they are not on the other
side of the line. A statuses list, a priority list, an icon set and a Space are
all configuration, and no amount of reading the document answers for them.
Guessing them offline would mean blessing a value the server never named.

An **icon** is the one whose answer is a warning rather than an error: no method
reports the configured icon set, so what can be observed is the icons projects
already carry, and an icon outside that set may be one that is configured and
not yet used.

### Two rules both layers obey

**Every check runs.** A document with six mistakes is answered with six
problems, not with the first one six times over six attempts. A report that
stops at the first error is useless to a form and useless to a CI job.

**Nothing is resolved twice.** Each distinct value is looked up once however
many items name it, and every failure is reported from that one pass. A document
naming four users that do not exist reports four problems from one run.

---

## Problems

Every failure either layer finds is one record with the same seven fields:

```json
{
  "object": "tasks[0]",
  "field": "assignment",
  "value": "alise",
  "reason": "No user 'alise' on this instance",
  "code": "unknown-user",
  "layer": "online",
  "severity": "error"
}
```

- **`object`** — which item, as a path a person can find: `tasks[0]`,
  `tasks[0].tasks[2]`, `searches[1]`, `variables`, `metadata`, or `$` for the
  document itself.
- **`field`** — the key inside it, or null when the problem is the item itself.
- **`value`** — exactly what was there.
- **`reason`** — one sentence, rendered and never parsed.
- **`code`** — a kebab-case slug from the vocabulary below. **This is what a
  program branches on**; `reason` is prose and nothing parses it.
- **`layer`** — `offline` or `online`.
- **`severity`** — `error` or `warning`. Only an error means the document was
  not usable.

A **warning never makes a document invalid.** It says "this may be a typo", and
a reader that treats it as a failure is wrong about the format.

Two things are deliberately *not* problems: a document that cannot be parsed,
and an instance that could not be asked. Neither has a document to attach a
record to, and a clean report for a check that never ran is worse than an error.

### The code vocabulary

<!-- BEGIN GENERATED codes -->

| Code | Layer | Severity | Means |
|---|---|---|---|
| `unknown-spec-version` | offline | error | `spec:` names a revision of the format the reader does not implement. |
| `unknown-key` | offline | error, warning under `metadata:` | A key that object type and verb does not define. |
| `deprecated-key` | offline | warning | The key still works; the reason names its replacement. |
| `wrong-type` | offline | error | The value is a number, list or mapping where the key takes text, or the other way round. |
| `missing-required` | offline | error | An item leaves out a key it cannot be created without. |
| `unknown-value` | offline | error | The value is outside a statically known set, such as an order or a project colour. |
| `bad-time` | offline | error | Not `<number>[h\|d\|w\|m\|y]`. |
| `bad-pattern` | offline | error | Not the transition grammar: an unknown condition type, keyword or direction. |
| `bad-policy` | offline | error | Not a policy keyword, `#project`, `@user` or PHID. |
| `bad-monogram` | offline and online | error | Not a monogram at all, or a monogram of an application this key does not accept. |
| `undefined-variable` | offline | error | A `{{ name }}` nothing declares, overrides or defaults. |
| `missing-variable` | offline | error | A declared variable with no value, no default and no override. |
| `circular-variable` | offline | error | Variables that define each other. |
| `duplicate-local-id` | offline | error | Two items declare the same `id:`. |
| `unknown-local-id` | offline | error | A `$ref` naming a local id the document never declares, or one of the wrong object type for the key. |
| `local-id-cycle` | offline | error | Local references that depend on each other, so no order creates them. |
| `bad-local-id` | offline | error | An `id:` outside `[A-Za-z0-9][A-Za-z0-9_.-]*`. |
| `unknown-user` | online | error | No such user on the instance. |
| `unknown-project` | online | error | No such project on the instance. |
| `ambiguous-project` | online | error | A name several projects answer to; use the hashtag or the PHID. |
| `unknown-space` | online | error | No such Space on the instance. |
| `unknown-commit` | online | error | No such commit on the instance. |
| `ambiguous-commit` | online | error | A bare hash more than one repository answers to; use rCALLSIGNhash or R1:hash. |
| `unknown-icon` | online | warning | An icon no project carries. The icon set is instance configuration no method reports, so this is never an error. |
| `unknown-status` | online | error | A status key this instance does not define. |
| `unknown-priority` | online | error | A priority name this instance does not define. |
| `unknown-column` | online | error | A column that is on none of the boards the item is tagged into. |
| `hashtag-taken` | online | error | A project hashtag something already answers to. |
| `not-creatable` | offline | error | A section naming an object type no endpoint creates. |
| `unknown-reference` | online | error | A reference that resolved to nothing and no more specific code fits. |
| `unreadable` | neither | error | The document could not be parsed, so there was nothing to validate. |
| `unreachable` | neither | error | The instance could not be asked, so the online layer never ran. |

<!-- END GENERATED codes -->

A code is never reused for a second meaning. Under `v1alpha1` a code may be
added, and one may be removed along with the check that emitted it.

---

## Worked examples

One complete document per object type. Every one of them is valid as written;
the user and project names are those of the development instance this page is
maintained against, and on another instance they are what needs substituting.

### Creating tasks

```yaml
spec: phorge/v1alpha1
kind: create
metadata:
  name: host-key-rotation
  description: The work that follows a host key rotation, as one epic.
  version: "1"
  author: infrastructure

variables:
  owner: tommy.svensson
  window: "the Sunday maintenance window"

tasks:
  - id: epic
    title: Rotate the build server host keys
    description: |
      Every host key on the build fleet, rotated in {{ window }}.

      The subtasks below are the order the runbook gives.
    priority: high
    status: open
    assignment: "{{ owner }}"
    subscribers:
      - "@sebastian.soderberg"
      - daniel.lindgren
    projects:
      - Security
      - Infrastructure
    visible-to: users
    editable-by: users
    tasks:
      - title: Announce the window
        description: A day ahead, on the mailing list and the status page.
        priority: normal
        assignment: "{{ owner }}"

      - title: Re-run the SSH configuration audit
        description: The audit has to pass before this epic is closed.
        priority: high
        assignment: sebastian.soderberg
        projects:
          - Security

  # Hung off the epic by naming it, rather than by nesting under it.
  - title: Record the rotation in the change log
    parents:
      - "$epic"
    assignment: deploy.bot
    projects:
      - Infrastructure
```

### Creating a project

```yaml
spec: phorge/v1alpha1
kind: create
metadata:
  name: observability-platform
  description: One project and its first milestone.
  version: "1"
  author: infrastructure

projects:
  - id: observability
    name: Observability Platform
    description: |
      Metrics, logs and traces for everything we run.

      A project's description is remarkup, the same as a task's.
    slugs:
      - observability
      - o11y
    icon: project
    color: indigo
    members:
      - mikael.wallin
      - "@viola.larsson"
    visible-to: users
    editable-by: admin
    joinable-by: users

  # A milestone is a project with a parent, so it is a `projects:` item like
  # any other. It takes `milestone-of:` instead of `slugs:` and `icon:`,
  # which Phorge derives from the project it belongs to.
  - name: Foundations
    milestone-of: "$observability"
    description: The first milestone of the platform work.
```

### Creating a paste

```yaml
spec: phorge/v1alpha1
kind: create
metadata:
  name: release-notes
  description: The release notes, as a paste tagged into the release project.
  version: "1"
  author: development

pastes:
  - title: Release notes 2026.3
    language: markdown
    content: |
      # 2026.3

      - The host key rotation is done on every build host.
      - The SSH configuration audit runs nightly.
    projects:
      - Development
    subscribers:
      - "@mikael.wallin"
    visible-to: users
    editable-by: users
```

### Searching tasks

```yaml
spec: phorge/v1alpha1
kind: search
metadata:
  name: blocked-work
  description: What is stuck, and what has been stuck before.
  version: "1"
  author: development

searches:
  - type: task
    title: Blocked now
    description: Open tasks sitting in a blocked column today.
    search:
      column: "in:Blocked"
      status: open
      order: priority:desc
      limit: 25

  - type: task
    title: Blocked at some point
    description: |
      Open tasks that have ever been blocked, whether or not they are now.
      This one reads history, so it costs more than the search above.
    search:
      column: "been:Blocked"
      status: open
      updated-after: 30d
      limit: 25
```

### Searching projects

```yaml
spec: phorge/v1alpha1
kind: search
metadata:
  name: active-projects
  description: The projects that are not milestones and not archived.
  version: "1"
  author: development

searches:
  - type: project
    title: Active projects
    description: Everything active, with each project's members listed.
    search:
      status: active
      milestones: false
      show-members: true
      limit: 50
```

### Searching pastes

```yaml
spec: phorge/v1alpha1
kind: search
metadata:
  name: my-pastes
  description: The pastes the caller wrote.
  version: "1"
  author: development

searches:
  - type: paste
    title: My pastes
    description: Written by whoever is running this.
    search:
      author: "@me"
      limit: 20
```

### Searching passphrases

```yaml
spec: phorge/v1alpha1
kind: search
metadata:
  name: deploy-credentials
  description: |
    The deploy credentials, by name. There is no passphrase.search, only the
    legacy passphrase.query, which takes no constraints at all: both filters
    below are applied over the records, so this search reads every credential
    the caller can see. The limit shortens the walk once enough have matched.
  version: "1"
  author: infrastructure

searches:
  - type: passphrase
    title: Deploy credentials
    description: SSH keys whose name mentions deploy.
    search:
      text_query: deploy
      type: key
      limit: 10
```

A passphrase document names credentials and never asks for their secret
material. The walk above reads every credential on the instance rather than only
the matching ones, so asking it for secrets would fetch the secret of each.

### One document, three applications

`kind:` carries the verb and never the object type, which is what lets one
document ask three applications in one run:

```yaml
spec: phorge/v1alpha1
kind: search
metadata:
  name: release-readiness
  description: A task, a project and a paste search, in one document.
  version: "1"
  author: development

searches:
  - type: task
    title: Open blockers
    search:
      status: open
      order: priority:desc
      limit: 25

  - type: project
    title: Where the work sits
    search:
      status: active
      milestones: false
      limit: 25

  - type: paste
    title: Release notes so far
    search:
      text_query: release notes
      limit: 10
```

---

## Implementations

phabfive reads and writes this format, and is the implementation the tables on
this page are generated from; see its own documentation for how to run a spec,
what it prints and what it exits with. Nothing on this page is about it, and a
rule that needs a particular program to be true is not a rule of the format.

Three gaps in that implementation are worth knowing about while reading the
tables above, because they are gaps in a reader and not in the format:

- `unknown-status` and `unknown-priority` are part of the code vocabulary before
  any check emits them; a status or priority the instance does not define is
  refused at create time with a sentence naming the instance's own list, rather
  than reported as a problem;
- `unknown-spec-version` is likewise in the vocabulary, but a `spec:` the reader
  does not implement is refused while the document is being read, before layer 1
  begins, so what a caller actually sees is `unreadable` carrying the same
  reason;
- none of the three create pairs (`task`, `project`, `paste`) declares its key
  set complete, so no create item is reported `unknown-key`. The precondition
  under [Layer 1](#layer-1-offline) is what that is; the four search pairs do
  declare theirs.

<!--
  The tables between BEGIN/END GENERATED markers are written by
  scripts/gen_spec_docs.py from the declarations they describe. Edit the
  declaration, then run:

      python3 scripts/gen_spec_docs.py

  tests/test_spec_docs.py fails when the checked-in page and the declarations
  disagree.
-->
