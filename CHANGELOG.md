# Unreleased

## Upgrade Notes

* **Breaking change: `diffusion branch list` is gone.** `diffusion repo show <repo>
  --show-branches` lists a repository's branches, and `--show-tags` its tags, which nothing
  could reach before. The command is removed rather than deprecated, so the old spelling is
  a parse error and not a warning
* **Breaking change: the `R` monogram shows the repository.** `phabfive R5` expanded to
  `diffusion branch list R5`; it now expands to `diffusion repo show R5`. `R` was the only
  monogram that did not stand for a `show` command. The branches it used to print are
  `phabfive R5 --show-branches`
* **Breaking change: `diffusion uri edit` lost its short flags.** `-n`, `-i`, `-d` and `-c`
  are gone; spell out `--uri`, `--io`, `--display` and `--cred`. `-i` now means
  `--interactive`, as it does on every other edit command
* **Breaking change: `diffusion uri edit --new-uri` is now `--uri`.** It sets the URI to the
  given value; the old spelling is not accepted
* **Confirmation now depends on how many tasks you edit, not which field.** One task applies
  without asking - the single-task title/description prompt, and its
  `--force required for non-interactive mode` error, are gone. Two or more are reviewed one
  at a time at a terminal. `--dry-run` still previews anything
* **`--force` is deprecated in favour of `--yes`.** It still works, hidden, and warns
* **`+` between `maniphest create --tag` or `--subscribe` values is deprecated.** Use a
  comma, as every other list option does: `--tag=Backend,QA`. `--tag=Backend+QA` still
  names two projects, and warns on stderr. `+` is the AND of the search-filter grammar,
  which a list of values to add has no use for. `Maniphest.create_task` no longer splits
  on `+` at all: its `tags` and `subscribers` are split on commas, so a project named
  `C++` reaches it whole
* **`@me` is an error on an instance that has a user called `me`, in every option.**
  `--assigned`, `--author`, `--assign`, `--subscribe`, `--member`, `--add-member` and
  `--remove-member` - on maniphest, paste and project commands, and on `edit` - quietly
  took `@me` to mean you even when somebody else is called `me`, while the policy options
  refused it. They now refuse it the same way, naming that user's PHID, and exit 1: an
  assignment or a subscription handed to the wrong one of you is the same mistake as a
  policy. Give your own username, or that user's PHID, instead. Nothing changes on an
  instance without such a user
* **The `~/.config/phabfive.yaml` credentials deprecation is a log message.** It reads
  `WARNING - ~/.config/phabfive.yaml contains ...` rather than `WARNING: ...`, and `-q`
  now silences it
* **Breaking change for Python callers: `Edit.edit_objects` is gone.** It returned an exit
  code and printed; `Edit.plan()` and `Edit.apply()` replace it for a program, and the
  command's flow is `phabfive.cli.edit_flow.run_edit`. `phabfive.editor` moved to
  `phabfive.cli.editor`, `Maniphest.edit_task_by_id` lost its `preview` parameter and
  prints nothing, and `Maniphest.add_task_comment` and `Maniphest.get_task_info` return
  their result rather than `(True, result)`
* **`phabfive.init_logging` moved to `phabfive.cli.log_setup`.** Configuring the root
  logger is the command's decision, not the library's, so the package root no longer
  offers it
* **`--format=simple` is now `--format=value`.** The old spelling still works - it is
  rewritten in argv the way `strict` and `ndjson` are - so no script has to change. Only
  `value` is offered by `--help` and by shell completion
* **A bare search exits 2 and prints its help.** `maniphest search`, `paste search` and
  `passphrase search` given nothing to search for printed a two-line usage stub and exited
  0, which a script could not tell from an empty result. They now print the command's full
  help on stderr and exit 2, as a bare command group does. Still nothing is searched
* **Breaking change to machine-readable output: `paste search` and `paste show` emit one
  record, shaped like a task's.** `paste search` emitted `{"id": "P1", "title": ...}` and
  `paste show` a flat `{"Link", "Name", "Author", ...}`. Both now emit
  `{"Link": ..., "Paste": {"Name", "Author", "Language", "Status", "Created",
  "Modified"}, "Space": ...}`, `show` adding `Content` inside the `Paste` section, so
  `jq '.title'` and `jq '.Name'` become `jq '.Paste.Name'`. This covers `json`, `jsonl`,
  `yaml` and the records `paste create/edit/comment` answer with (#450). The human
  formats follow: `paste search` prints the record for `rich` and `tree` rather than
  `P1 title` lines, and its `table` has the Paste fields as columns. `--format=value`
  still prints a paste's content, and prints its monogram when it has no content to
  give - every `paste search` result, and `paste show --no-content` - one per line as
  `passphrase search` does, so `paste search | xargs paste show` works
* **Breaking change: `passphrase show` and `passphrase search` records have a `Credential`
  section.** With `--format=yaml`, `json` or `jsonl` a record is now `Link` and a
  `Credential` section holding `Name`, `Type`, `Username`, `Secret`, `PublicKey`, `Created`
  and `Modified`, the way a task is `Link` and `Task`. `jq '.Secret'` becomes
  `jq '.Credential.Secret'`, and `.Name` becomes `.Credential.Name`. Which secrets are
  printed, and when, is unchanged; `--format=value`, `rich` and `tree` are unchanged too.
  Closes #451

* **A write whose answer is lost is no longer sent again.** The `phabricator` library
  retried every request after a read timeout, and Conduit is always POST, so a slow
  `maniphest comment` could post its comment twice. Such a write now fails with an error
  saying it may have been applied, and is not repeated. So does one answered with a 5xx,
  which from a gateway usually means the server carried on working. An edit that only sets fields -
  status, priority, policies and the like - is still retried, since arriving twice changes
  nothing (#422)

## New Features

### Using phabfive as a Library
* **The library surface is exported from the top level** - `from phabfive import
  Maniphest` works without knowing the module layout. `__all__` is the supported
  promise: `Phabfive`, `Maniphest`, `Paste`, `Diffusion`, `Passphrase`, `Project`,
  `User`, the five exception types and `__version__`. Each module also declares its own
  `__all__`; a name listed there but not at the top level is public and importable from
  its module, just not part of the narrower promise. Existing submodule imports are
  unchanged. Closes #438
* **`import phabfive` is lazy** - every public name is resolved on first use (PEP 562),
  so the import loads no third-party module and costs about 2ms rather than 75ms, and
  nothing that merely touches an attribute on `phabfive` can reach the CLI or its
  `TYPER_USE_RICH` environment variable
* **`phabfive.__version__`** - read on first access, and falls back to `0.0.0+unknown`
  on a source tree that was never installed rather than raising
* **`py.typed`** - the package is marked as typed for consumers' type checkers
* **Configure a class from arguments** - `Maniphest(url=..., token=...)`, with the other
  settings in `config={...}`, configures an instance from those alone and discovers
  nothing, so a program behaves the same whoever runs it and wherever. With no arguments
  the configuration is discovered as before. Closes #439
* **Constructing makes no request** - the Conduit client is built on the first call, which
  used to cost two round trips per instance and four for `Diffusion` and `Edit`, which
  each built a second app. `verify=True` checks the connection at once; the command always
  asks for that, so it still fails before doing anything. Apps used internally now share
  their parent's client
* **Plan an edit, then apply it** - `Edit.plan("T1,T2", status="resolved")` returns an
  `EditPlan` of `TaskEdit`s (the transactions and the changes each would make) and
  `EditFailure`s, and changes nothing; `Edit.apply()` makes one edit. Validation is all or
  nothing and raises `PhabfiveValidationException` naming every task that failed.
  `Maniphest.create_tasks_from_config()` creates from a creation template held as data.
  Closes #442
* **Errors a program can catch without the client's libraries** - Conduit errors are
  `PhabfiveAPIException`, with the `.code` and `.message` of the `phabricator.APIError`
  they replace, and an unreachable server is `PhabfiveConnectionException`; both are
  `PhabfiveRemoteException`s. A bad argument value is `PhabfiveInputException`, also a
  `ValueError`, and an object that does not exist is `PhabfiveNotFoundException`, also a
  `LookupError`. Nothing phabfive raises on purpose is a bare `ValueError` any more.
  Closes #440, #441
* **The library never prompts, prints or writes the environment** - choosing between
  several `~/.arcrc` hosts goes through a `select_host` callback, which the command answers
  with its prompt; reading the configuration no longer rewrites `XDG_CONFIG_DIRS`; building
  an instance no longer sets the process-wide `PHAB_FALLBACK` format; returned records hold
  plain strings rather than `rich` hyperlinks; and `rich` is not imported until something
  displays

### Showing Repositories
* **`diffusion repo show`** - Diffusion had no show command. `diffusion repo show R5 R6`
  answers with the repository and a link to it, and - only when asked for -
  `--show-uris`, `--show-branches`, `--show-tags`, `--show-metadata` and
  `--show-policy`. It takes several repositories, space- or comma-separated, and a
  repository that does not exist is a failed lookup rather than an empty result, the
  rule `maniphest show` follows
* **`--show-tags`** - A repository's tags were unreachable: the branch listing asked
  `diffusion.branchquery` and then dropped every ref that was not a branch, and nothing
  called `diffusion.tagsquery` at all

### Repository Policies
* **`diffusion repo edit --visible-to`, `--editable-by` and `--can-push`** - phabfive
  had no policy handling anywhere, in any app, while every search response carried the
  policies and discarded them. Each option takes a keyword (`public`, `users`, `admin`,
  `no-one`), a `#project`, an `@user` or a PHID, and is named after the capability it
  sets. A value outside that grammar is refused before a call is made: Conduit reads one
  it does not recognise as a policy nobody satisfies, so `--visible-to=nonsense` would
  otherwise be answered as a permissions error rather than a spelling one
* **Policy PHIDs are resolved to names** - `repo show` and `repo list` printed a raw
  `PHID-PROJ-...` where a policy named a project. Both now name it under
  `--show-policy` / `-P`, in the same spelling the options take, so what a policy is
  shown as can be typed straight back in. They are
  reported under `Visible To`, `Editable By` and `Can Push` - Phorge's own labels, which
  come from one rule rather than from wherever a label happened to be read off a page.
  `AphrontFormPolicyControl` special-cases exactly three capabilities into the `-able By`
  family, `CAN_VIEW`, `CAN_EDIT` and `CAN_JOIN`, and names every other one after the
  capability itself. Push is not one of the three, so `DiffusionPushCapability` names it
  and that is `Can Push`. `Pushable By` is a label `DiffusionRepositoryPoliciesManagementPanel`
  applies to its own row and nothing else in Phorge uses. It is the same rule that makes a
  task's third policy `Can Interact`
* **A push policy on a repository Phorge does not host says so** - a repository that
  follows a remote stores a push policy that nothing ever consults, and phabfive printed
  it as though it were in force. `repo show --show-policy` and
  `repo list --show-policy` now report it as
  `Not a Hosted Repository`, which is the sentence Phorge's own Policies panel prints in
  place of the value. It is that string in every format rather than a null or a dropped
  key, so every repository carries the same keys and the value reads the same way in a
  terminal as in JSON; `Repository.Hosted`, in the same record, is the boolean to test.
  `--can-push` still sets a policy there - Phorge permits it, and a repository can be
  made hosted later - but warns on stderr that nothing will consult it yet
* **A self-lockout is a sentence, not a traceback** - Phorge refuses a policy that would
  stop you seeing or editing the repository yourself, and `repo edit` now reports the
  sentence it answered with
* **The `Policy` section is opt-in, in both apps** - `--show-policy` / `-P` on
  `diffusion repo show`, `diffusion repo list` and `maniphest show`, and
  `--show-policy` on `maniphest search`, which puts policy in the same `--show-*`
  family as branches, tags, URIs, metadata, history and comments rather than leaving
  it the one optional section nobody could decline. It is also what it costs: naming a
  policy that points at a project, a user or a custom rule is a `phid.query`, which
  `repo list` was paying once for a whole instance to render a section most callers
  never asked for. The gate is in the record builder rather than in each renderer, so
  every format agrees by construction, and it covers the resolution as well as the
  rendering - without the flag no `phid.query` is made at all. Setting a policy is
  unaffected: `--visible-to`, `--editable-by` and `--can-push` need no `--show-policy`

### Task Policies
* **`maniphest show --show-policy` reports a task's policies** - Every search response
  carried them and every display builder discarded them. Asked for, the record holds a
  `Policy` section with all three: `Visible To`, `Editable By` and `Can Interact`. Those
  are Phorge's own labels rather than the API's field names, and a policy naming a
  project or a user is shown in the same spelling the options take, so what a policy is
  shown as can be typed straight back in. `maniphest search` takes the flag too
* **`maniphest edit` and `maniphest create` take `--visible-to` and `--editable-by`** -
  Named after the labels Phorge's own form uses, and taking the same grammar repository
  policies take, from the same module: a keyword, a `#project`, an `@user` or a PHID,
  refused client-side when it is none of those. `phabfive edit` takes them too, so a
  `maniphest show | phabfive edit` pipeline can set a policy
* **There is no `--can-interact`, deliberately** - `Can Interact` is readable and worth
  reading, but a task does not store it. `ManiphestTask::getPolicy` derives it from the
  view policy, answering `No One` while the task's status locks comments, and
  `maniphest.edit` has no interact transaction in any spelling. So `Can Interact`
  differing from `Visible To` is how a locked task says so, and the way to move it is the
  task's status
* **A policy change needs `--yes` in a batch without a terminal** - It joins title and
  description behind that guard rather than applying unreviewed. It is the more
  consequential of the two and the harder to notice: a retitled task is still where it
  was, while one whose view policy has narrowed has simply gone, for everybody the new
  policy leaves out
* **A self-lockout is a sentence, not a traceback** - As for repositories, and
  `maniphest edit` was not translating it at all

### Reviewing Edits
* **Per-task review for batch edits** - Editing two or more tasks at a terminal shows each
  task's changes and asks `[y,n,a,q,?]`: apply it, skip it, apply all the rest, or quit.
  Skipping and quitting are not failures, and nothing skipped is touched
* **`--yes` / `-y`** - Apply without confirming. Replaces `--force`
* **`--interactive` / `-i`** - Review the change and confirm, for a single task or for input
  piped from `maniphest search`, where it reads `/dev/tty`. With no terminal to show the
  changes on it fails rather than applying unreviewed
* **`diffusion uri edit --dry-run`** - The command could rewrite a URI, flip its I/O mode,
  disable it or swap its SSH credential with no preview and no confirmation. It now has
  `--dry-run`, `--yes` and `--interactive`, and reports each value it would change and what
  it would change it from. A credential is named by its monogram; its secret is never printed

### Bare Values
* **`--format=value`** - The format that prints values with no keys, no header and no
  decoration, for piping. It is what `--format=simple` has always been; `simple` named
  nothing, while `rich`, `tree` and `table` name a shape and `yaml`, `json` and `jsonl`
  name a syntax
* **Which value is the command's choice** - `passphrase show` prints the secret,
  `passphrase search` one monogram per line, and `paste show` the content - or, for a
  paste without content, as from `paste search`, its monogram. No other app
  has a bare value to offer, so `value` falls back to `rich` there, which `--help` says.
  Like `table` it is a human format and is not accepted for `PHAB_FALLBACK`
* **`--format=simple`** - Kept as a spelling of `value`, alongside `strict` for `yaml` and
  `ndjson` for `jsonl`

### Table Output
* **`--format=table`** - A grid, one row per record and one column per field, for the
  commands whose answer is a list: `diffusion repo list`, `diffusion uri list`,
  `maniphest search`, `maniphest parents`, `maniphest subtasks` and `paste search`. A
  `show` command has one record and no grid to make of it, so `--format=table` falls
  back to `rich` there, which `--help` says
* **Columns come from the records, not from the command** - A nested section
  contributes its leaves (`Repository: {Name: ...}` is a `Name` column), a list
  becomes one comma-joined column, a column empty in every row is dropped, and a
  `Link` becomes the row's terminal hyperlink rather than a column of URLs. So an app
  adopting the format writes no column list. Selecting columns with `--columns` is
  not implemented
* **One row is one line** - Cells are cut to 60 characters, and on a terminal the
  table is fitted to its width by shrinking the widest column first. Piped, every
  cell arrives whole. `table` is a human format and is deliberately not accepted for
  `PHAB_FALLBACK`

### Text Search Hints
* **An empty text search explains itself** - `maniphest search igrat` found
  nothing and printed nothing, because Phorge's full-text search matches whole
  words and "igrat" is only part of one. `maniphest search`, `paste search` and
  `project search` now say on stderr that nothing was found, that text matches
  whole words, and suggest the query with `~` in front of each plain word - `~igrat`
  - which is Phorge's own operator for part of a word. The text is still sent as it
  stands, so the operators the web UI takes (`~word`, `"a phrase"`, `-word`,
  `title:word`) work here too. stdout stays empty

### Users
* **`user search`** - phabfive could not list users (#428). `user search` answers
  with `Username`, `Name` and `Roles` for each one, the record
  `project show --show-members` gives for a member, so the two compare directly.
  `--role` keeps users with every role named and `--not-role` drops users with any
  of them; `--not-role=bot,list,disabled` is every person who can use the instance.
  `bot`, `disabled`, `list` and `admin` are filtered by `user.search` itself, the
  rest on the records it returns. The text argument finds any part of a username
  or real name, ignoring case, so `user search holm` finds rholm and hholm.
  `--username` and `--realname` each search one of the two fields alone. A bare
  `user search` prints its help and exits 2 rather than reading every user on the
  instance; `--role=any` lists them all on purpose
* The local Phorge seed has a bot, deploy.bot, and a disabled account,
  former.employee
* **Every option that takes a user takes a username, `@username`, `@me` or a user
  PHID.** `maniphest search --assigned/--author`, `maniphest create/edit` and `edit`
  `--assign/--subscribe`, and `paste search --author` took a bare username or `@me` only:
  `--assigned=@admin` and `--assigned=PHID-USER-...` both failed with `User ... not
  found`. They now resolve through one place, as `project --member` already did, so the
  PHIDs the `@me` ambiguity error lists can be pasted into any of them. A PHID is looked
  up rather than passed through, so a mistyped one is `No such user` instead of a search
  that quietly matches nothing, and a preview names the user rather than printing the
  PHID. `paste create/edit --subscribe` now refuses an unknown user before anything is
  sent

### Projects
* **`project show`, `project search`, `project create` and `project edit`** - phabfive
  had no way to manage a project (#418). A project is named by `#hashtag`, bare
  hashtag, ID, PHID or exact name; a name two projects share, as every team's
  "Sprint 1" milestone does, is refused with the ID of each rather than one being
  picked
* **`--show-members`** - Lists each member with their `Username`, `Name` and
  `Roles`, the roles passed through from Phorge unchanged - `bot`, `admin`,
  `disabled` - which is what lets a script tell a bot account from a person
* **Subprojects and milestones** - `project create --parent` and `--milestone-of`,
  and `project search --parent`, `--ancestor` and `--milestones`
* **Project policies** - `--visible-to`, `--editable-by` and `--joinable-by` on
  `project create` and `project edit`, in the shared policy grammar, and
  `--show-policy` reports them as `Visible To`, `Editable By` and `Joinable By`
* **Auditing every project** - `phabfive --format=jsonl project search --status=any
  --space='*' --show-policy -l 0` lists every project on the instance with its
  policies, one per line, at the cost of one extra lookup for the whole listing.
  `project search` looks in `PHAB_SPACE` unless `--space` says otherwise, as
  `maniphest search` does, and `--status` takes `active`, `archived` or `any`
* **Hashtags are kept** - `project edit --add-slug` reads the full list of hashtags
  before adding one, because Phorge's hashtag transaction replaces the list and
  `project.search` reports only the primary one
* **Repeatable and comma-separated** - `--member @a,@b --member @c` names three
  members, and every list option on the project commands reads that way.
  `maniphest edit --subscribe` goes through the same rule, and now drops a name
  given twice
* **`--icon` completion** - Offers Phorge's stock icons plus every icon in use on
  the instance, cached for a week in the new `project-icons` namespace, since the
  icon set is instance configuration no Conduit method reports. A project write
  drops the cached project names, so a new or renamed project completes at once
* **A misspelled `--icon` is warned about** - `project search` and a `project create`
  or `edit --dry-run` warn about an icon that Phorge does not ship and no project on
  the instance carries, since a search answers it with nothing and a dry run never
  reaches the server. A warning, not an error: a configured icon no project uses yet
  looks the same. The icons come from the `project-icons` cache completion fills,
  and with caching off nothing is checked, rather than fetching every project on
  every run (#421)
* A project cannot be archived, unarchived or deleted through Conduit, so none of
  these commands can

### Remembered Task Statuses
* **The status map is cached for a week** - `maniphest create`, `edit` and `search`
  and `phabfive edit` asked `maniphest.querystatuses` on every run, so a script
  editing in a hundred batches asked a hundred times. The answer is now kept in the
  new `status-map` cache namespace, and every run after the first saves the round
  trip. A status the remembered map does not know is asked of the server once more
  before it is refused, so a newly configured status can be set at once;
  `phabfive cache clear status-map` refreshes the rest. A failed lookup still falls
  back to the standard statuses, and those are never written down. Only the command
  caches: a program using phabfive as a library asks every time (#421)

### Newline-Delimited JSON Output
* **`--format=jsonl`** - Emits one JSON object per line with no wrapping array
  ([JSON Lines](https://jsonlines.org/)), written and flushed per record. Works
  everywhere `--format=json` does: `maniphest show`/`search`, `paste show`/`search`,
  `passphrase show`/`search`, `user whoami` and `cache info`
* **`--format=ndjson`** - Accepted as a spelling of `jsonl`, the same way `strict`
  is accepted for `yaml`
* **`PHAB_FALLBACK=jsonl`** - The non-TTY default format now accepts `jsonl` alongside
  `yaml` and `json`

### Retries and Pacing
* **Failed Conduit calls are retried with backoff** - A connection error, timeout, HTTP 429
  or 5xx is tried again after an exponential backoff with full jitter, and a `Retry-After`
  is honoured. Every retry is announced on stderr with the wait it chose, e.g.
  `WARNING - maniphest.search: HTTP 503, retry 1 of 3 in 0.2s`. A call that succeeds
  costs nothing extra; with the defaults a failing one waits under two seconds in total
* **Reads are retried freely, writes only when that is safe** - `*.search`, `*.query`,
  `phid.lookup`, `user.whoami` and the other reads are retried on any of those failures. A
  write is retried only when the connection was never made, unless it sets fields of an
  existing object; `maniphest edit` marks its edits that way whenever they carry no comment
* **A paged search resumes from the page that failed** - One bad page of a
  `maniphest search --status=any -l 0` used to fail the whole command; the page is now asked
  for again with the same cursor, and no page already read is fetched twice
* **`PHAB_RETRY` and `PHAB_BACKOFF_MAX`** - How often a failed call is retried (default
  3, `0` turns retrying off) and the longest single wait in seconds (default 5, which caps
  `Retry-After` too). A bulk job that should ride out a restart can raise both. They and
  `PHAB_PACE` are checked when an app is constructed, so a value that is not a finite,
  non-negative number fails before any work is done
* **`PHAB_PACE`** - Seconds to keep between the writes of a batch edit, so a batch does
  not carry on at full speed against a struggling server. Off by default. See
  [Retries](docs/retries.md)

## Bug Fixes

* **A bare search exited 1 with a traceback on typer 0.27.** `maniphest search`, `paste search`,
  `passphrase search` and `user search` with nothing to search for raised click's
  `NoArgsIsHelpError`, which typer 0.27 no longer catches because its `Context` is its own
  vendored click. They now print the help on stderr and exit 2 on every typer version
* **Errors past the first request printed a traceback.** A token the server refuses,
  a server that stops answering mid-command and a malformed `--created-after`
  value all ended in Python tracebacks. Each is now one line on stderr and exit
  status 1, e.g. `Error: ERR-INVALID-AUTH: API token "..." is not valid.`
* **`diffusion uri edit` printed a traceback when Phorge rejected the edit.** It now
  reports the validation error as `ERROR: ...` and exits 1, like every other apply path.
  Fixes #394
* **A batch dry run said "Edited N/N tasks".** It now says `Would edit N/N tasks (dry run)`,
  and in any run a task already at the target is counted apart, as `N already at target`.
  Closes #423
* **`--tag` and `--subscribe` split differently from one command to the next.**
  `--subscribe=@a,@b` meant two people on `maniphest edit`, a user named `@a,@b` on
  `maniphest create`, and `paste create --tag=a,b` added one project named `a,b`. Every
  option that adds values is now repeatable and comma-separated, through
  `phabfive.options.split_list_option`: `maniphest create --tag/--subscribe` and
  `paste create/edit --tag/--subscribe` join `maniphest edit --subscribe` and the
  `project` options. Search filters keep their own grammar, where `,` is OR and `+` is
  AND. Tab completion follows: these options complete the value after the last comma,
  and `maniphest create --tag=Development,QA --column=<TAB>` offers the Development
  board's columns, where it offered none. Fixes #429
* **`maniphest create --with` exited 0 when the template file did not exist.** It is an
  error now, and exits 1
* **Interrupting phabfive printed a traceback of its own.** `cli_entrypoint` raised
  `typer.Exit` outside click's main loop; it exits 130 now
* **`passphrase show` and `passphrase search` ignored `--hyperlink` and `--ascii`.** They
  never applied the output options every other command does
* **`diffusion repo edit` understated which built-in URIs a rename rewrites.** The
  warning is the only signal that a rename breaks existing clones, and it was wrong
  three ways: it fired only on `--short-name`, although Phorge builds the clone URIs
  from the *name* of a repository that has no short name, so `--name` moved them
  silently; it rendered an absent short name as the literal `None`, printing
  `/source/None.git`; and it named one URI where a repository carries up to three -
  `/source/<name>.git`, `/diffusion/<id>/<name>.git` and, with a callsign,
  `/diffusion/<callsign>/<name>.git`, the form most likely to be in somebody's git
  remote. It is now read off the built-in URIs the repository actually carries and
  names each one's current and new address. A repository whose view policy hides its
  URIs is told that rather than given a list that is quietly short

* **`@me` in a policy option looked up a user called "me".** `--visible-to`,
  `--editable-by`, `--can-push` and `--joinable-by` - on `maniphest search`, on the
  create and edit commands of tasks, repositories and projects, and on `edit` - answered
  `@me` with `User '@me' does not exist`. It now resolves to you, through `user.whoami`,
  as `--assigned=@me` does, and the help text lists it and tab completion offers it. On
  an instance that has a user called `me`, `@me` is an error and exits 1 rather than
  guessing which of you is meant; give a PHID instead. Fixes #435

## Other Notes

* mypy now type checks `phabfive/`, in the `Tests` lint step and in the pre-commit hook.
  The hook matched `^(app/|cli/|lib/)`, which is no path in this repository, so it had
  never checked a file and the `py.typed` marker promised types nothing verified. It now
  runs `uv run mypy` against `[tool.mypy]` in `pyproject.toml`, with
  `no_implicit_reexport` - what a consumer's mypy applies to `from phabfive import X` - so
  the public API's `TYPE_CHECKING` block is checked too. The 30 errors it found were
  missing annotations, two internal re-exports that did not say so, and code mypy
  could not follow; fixing them changed no behaviour
* `--format=rich` now quotes the values YAML would quote. Diffusion's rich output is
  YAML-shaped and is read back as YAML, and it printed every scalar bare - so a value
  like `#security` read back as an empty field followed by a comment, and one like
  `@admin` failed to parse at all. Reachable with ordinary data once a policy could
  name a project or a user. The helper moved to `phabfive/display.py` when
  Maniphest's `Policy` section became its second caller; the older task fields around
  that section still print bare, so a task's rich output is not YAML in general
* `scripts/smoke.py --venv` now also imports phabfive as a library in the venv's own
  interpreter, and asserts every `__all__` name resolves without pulling in the CLI. Both
  that check and `--version` reject the `0.0.0+unknown` fallback, so a release artifact
  that lost its dist-info still fails - a fallback matches the version pattern, and would
  otherwise pass a run given no `--expect-version`. The PyInstaller builds copy
  phabfive's metadata explicitly
* All JSON serialization now goes through `phabfive/json_output.py`, so `json` and
  `jsonl` are built from the same record builders and cannot drift apart
* Paste and passphrase JSON output gained builder/printer splits, matching what
  maniphest and user already had
* The dev Phorge instance seeds its sample data with `phorge/seed/`, a PHP seeder
  that writes through Phorge's own editors and Conduit rather than raw SQL, in
  place of the bash scripts in `phorge/lib/`. One module and one JSON data file
  per kind of data, selected with `PHORGE_SEED`, documented in
  [docs/phorge-setup.md](docs/phorge-setup.md#sample-data)
* The dev Phorge now has eight teams and ten Spaces, most of them readable only
  by their team. The admin token still sees S1, S3 and S10, but the gaps between
  them are access rules rather than skipped numbers, which is what probing for
  Spaces meets on a real instance
* The dev Phorge banner (`make creds`) printed `phabfive whoami`, which is not a
  command - it now prints `phabfive user whoami`. Its user, project and space
  lists are read from the database instead of the static arrays in
  `phorge/lib/common.sh`, so they describe the instance that is running rather
  than what the current branch would seed

# 0.9.0 (2026-05-04)

## Prelude

This release completes the unified create/edit command UX for both Maniphest and Paste applications. Single task creation is now feature complete with consistent patterns across all object types.

## New Features

### Unified Edit Command
* **Batch editing** - Edit multiple tasks at once with comma-separated IDs (e.g., `phabfive maniphest edit T123,T124,T125 --status=resolved`)
* **Description editing** - New `--description` option with `$EDITOR` support, unified diff display, and confirmation prompts
* **Title editing** - Positional title argument for quick renames (e.g., `phabfive maniphest edit T123 "New Title"`) with diff display
* **Subscriber management** - New `--subscribe` option to add subscribers during edit (`--subscribe=@me`)
* **Diff confirmation** - All text changes (title, description, content) show unified diffs and prompt for confirmation

### Shell Completion Enhancements
* **Context-aware column completion** - `--column` completion fetches actual column names from the board specified by `--tag`
* **Tag completion** - `--tag` option now completes project names from API
* **Pattern prefix support** - Completions support filter prefixes like `in:`, `not:in:`, `from:`, `to:`

### Paste App Modernization
* **Edit command** - New `phabfive paste edit P1` with `--content`, `--language`, `--tag`, `--subscribe` options
* **Comment command** - New `phabfive paste comment P1 "text"` for adding comments
* **Free-text search** - `phabfive paste search "query"` searches paste titles
* **Positional title** - Quick title changes with `phabfive paste edit P1 "New Title"`

### UX Improvements
* **Unified dry-run output** - Consistent `[DRY RUN]` format across create and edit commands
* **Editor temp file prefixes** - Temp files use descriptive prefixes (e.g., `description-T123.remarkup`)
* **Graceful error handling** - API connection errors show clean messages without tracebacks

## Bug Fixes

* Fix priority value comparison using shared constants
* Handle statusMap values that can be strings or dicts
* Remove traceback output for user-facing errors

## Other Notes

* Restructured `edit.py` into `edit/` package for better organization
* Removed deprecated `paste list` command (use `paste search` instead)
* Added pre-commit checks documentation to AGENTS.md
* Various dependency updates via Dependabot


# 0.8.0 (2026-03-30)

## Prelude

Major release featuring a migration from docopt-ng to Typer for the CLI framework, Arcanist-compatible configuration, and numerous new features including shell completion, JSON output, parent/subtask support, and search metadata embedding.

## Upgrade Notes

* **Breaking change:** Configuration now uses Arcanist-compatible `.arcrc` format. Existing `~/.config/phabfive.yaml` configurations need to be migrated. Run `phabfive user setup` for guided setup.
* **CLI framework migrated from docopt-ng to Typer** - Command syntax is unchanged, but help output and error messages may differ.
* **`--format=strict` renamed to `--format=yaml`** - The `strict` alias is still accepted for backwards compatibility.

## New Features

### CLI & Shell Completion
* **Typer-based CLI** - Migrated from docopt-ng to Typer for better argument parsing, type safety, and help output
* **Smart shell completion** - Tab completion for `--priority`, `--status`, `--column` values, and all other option values
* **JSON output format** - New `--format=json` option for machine-readable output

### Maniphest Enhancements
* **Multi-task show** - `maniphest show` now accepts multiple task IDs (e.g., `phabfive maniphest show T123 T456`)
* **Parents and subtasks** - New `maniphest parents` and `maniphest subtasks` commands, plus Parents/Subtasks fields in task output
* **Search metadata Query embedding** - `--show-metadata` now includes a `Query` section with original search parameters, enabling downstream tools to access search context without duplicate arguments

### Configuration
* **Arcanist-compatible configuration** - Reads credentials from `.arcrc` files, compatible with Arcanist/Phorge tooling
* **Interactive server selector** - When multiple hosts are configured in `.arcrc`, presents an interactive selection prompt

### Other
* **Passphrase structured output** - `passphrase show` now outputs structured data
* **Optional ptpython REPL** - Enhanced REPL experience when ptpython is installed

## Bug Fixes

* Fix JSON output producing invalid JSON array for multiple tasks
* Fix monogram shortcuts not working with global options
* Improve error handling and standardize error format
* Fix passphrase show subcommand for monogram shortcuts
* Fix PyInstaller entry point for Typer CLI package structure
* Fix insecure temp file reuse vulnerability in requests (upgrade to 2.33.0)

## Other Notes

* Replaced flake8 with ruff for linting
* Updated license to SPDX identifier format
* Removed unused MANIFEST.in
* Updated pre-commit hooks
* Cleaned up constants.py
* Added `exclude-newer` to `[tool.uv]` for supply chain protection


# 0.7.1 (2026-03-07)

## Bug Fixes

* Fix standalone executables exiting silently without output


# 0.7.0 (2026-03-07)

## Prelude

This release adds interactive first-run configuration, standalone executables for all major platforms, and improved developer experience with Sigstore-signed releases.

## New Features

### Interactive Setup
* **First-run configuration** - New `phabfive user setup` command guides users through initial configuration with interactive prompts
* **Secure token input** - API token input is masked with dots for security
* **Smart reconfiguration** - Warns before overwriting existing working configuration

### Release Artifacts
* **Standalone executables** - Pre-built binaries for Linux, macOS, and Windows (AMD64 and ARM64)
* **Sigstore signing** - All executables are cryptographically signed for verification
* **RC tag support** - Release candidates skip PyPI for testing the release process

## Bug Fixes

* Fix Rich markup escaping for square brackets in user content
* Fix project lookup failing when more than 100 projects exist

## Other Notes

* Version now sourced solely from `pyproject.toml` using `importlib.metadata`
* Added `AGENTS.md` for AI coding agent guidance
* Removed unused `__author__`, `__email__`, `__url__` module attributes


# 0.6.0 (2026-03-07)

## Prelude

This release brings significant internal refactoring, new search filters, and automated release infrastructure. The codebase has been restructured with maniphest, diffusion, and transitions modules converted to package structure for better maintainability.

## Upgrade Notes

* **Breaking change:** Closed tasks are now hidden by default in `maniphest search`. Use `--all` to include closed tasks.

## New Features

### Search Enhancements
* **Space filtering** - New `--space` flag for filtering tasks by Phabricator Space
* **Assignee filtering** - New `--assigned` filter with `@me` shortcut and OR logic support (comma-separated values)
* **Date range filters** - New `--created-before` and `--updated-before` filters
* **Time unit support** - Date filters now accept time units (h, d, w, m, y) e.g., `--created-after 2d`
* **Auto-detect strict format** - `--format=strict` is automatically enabled when output is piped (#131)

### Configuration
* **Arcanist support** - Added support for reading configuration from `.arcrc` files
* **IPv6 and port support** - `PHAB_URL` validation now accepts IPv6 addresses and port numbers

### User Experience
* **Syntax hints** - Helpful hints displayed for invalid `--status`, `--priority`, and `--column` values

## Bug Fixes

* Fix project lookup failing when more than 100 projects exist (#140)
* Fix Rich markup issues with square brackets in user content
* Handle deleted working directory gracefully
* Handle KeyboardInterrupt gracefully in CLI
* Improve Conduit access denied error message for passphrase
* Fix formatting issues on readthedocs.io
* Skip permission checks on Windows

## Refactoring

* Convert maniphest module to package structure
* Convert diffusion module to package structure
* Consolidate transition modules into package
* Move display logic from library to CLI layer
* Improve method naming across modules
* Rename TransitionPattern to ColumnPattern for consistency

## Other Notes

* Added GitHub Actions workflow for automated PyPI releases using trusted publishing (OIDC)
* Applied ruff formatting across codebase


# 0.5.0 (2026-01-10)

## Prelude

Feature release focused on improved output formatting, enhanced task management, and better user experience. This release introduces multiple output formats, clickable hyperlinks, CLI-based task creation, and search templates.

## Upgrade Notes

* **New dependency:** `rich>=13.0.0` added for enhanced terminal output formatting

## New Features

### Output Formatting
* **Multiple output formats** - New `--format` option supporting `rich` (default), `tree`, and `strict` modes
* **Clickable hyperlinks** - Terminal hyperlinks for task IDs, column names, assignees, and board names with `--hyperlink` option
* **ASCII mode** - Use `--ascii` flag for terminals without Unicode support (uses hyphens instead of bullets)

### Maniphest Enhancements
* **CLI-based task creation** - Create tasks directly from command line with `maniphest create`
* **Show task comments** - New `--show-comments` flag to display comments when viewing tasks
* **Comment shorthand** - Simplified syntax for adding comments to tasks
* **Assignee display** - Task views now show assignee information and history
* **YAML search templates** - Define reusable search queries with multi-document YAML support
* **Enhanced free-text search** - More flexible filtering options for task searches

### Developer Experience
* **Modernized Phorge environment** - Updated Docker development setup with configurable environment variables
* **Improved Makefile** - Added `lock` and `upgrade` targets for dependency management

## Bug Fixes

* Fixed comments not displaying in task show output
* Fixed ASCII bullet character (now uses hyphen instead of asterisk)
* Fixed input validation and logging configuration issues
* Improved UX and logging for maniphest search command
* Enhanced error handling and code clarity

## Other Notes

* Normalized error message format to use "ERROR - " prefix consistently
* Updated CLI option style from `--option=<style>` to `--option=STYLE` for consistency with docopt conventions
* Improved test coverage for maniphest task search functionality


# 0.4.0 (2025-11-12)

## Prelude

Major feature release focused on significantly expanding Maniphest capabilities and modernizing the project infrastructure. This release introduces advanced task filtering, search patterns, template v2 system, and comprehensive developer tooling with Phorge Docker setup.

## Upgrade Notes

* **Python support bumped to minimum version 3.10** (adds support for 3.13 and 3.14)
* **Project management migrated to modern `pyproject.toml`** - replaced `setup.py` with PEP 621 compliant configuration
* **Switched to `uv` for dependency management** - faster, more reliable package management
* **Dependency updates:**
  - `docopt` → `docopt-ng` for improved Python 3 support
  - Added `ruamel-yaml>=0.18.16`
  - Updated `mkdocs>=1.6.1`

## New Features

### Maniphest Enhancements
* **Advanced filtering system** - Filter tasks by status, priority, and projects with complex logic
* **Wildcard project search** - Search and resolve projects using pattern matching
* **Search negation support** - Exclude items from search results with negation patterns
* **Pagination for large result sets** - Automatically handles API pagination for projects and tasks
* **Template v2 system** - Complete rewrite with variable dependency resolution and improved structure
* **Task transition management** - Advanced filtering for status, priority, and project transitions
* **Project column inspection** - Query project boards to see columns and associated tasks
* **Monogram support** - View tasks using T123 format directly from CLI
* **YAML output improvements** - Proper formatting using yaml libraries

### Developer Experience
* **Phorge Docker environment** - Automated local Phorge setup for testing and development
* **Enhanced Makefile** - Dependency checks, Phorge management commands, improved build targets
* **REPL tab completion** - Navigate commands more efficiently in interactive mode
* **Comprehensive documentation:**
  - Detailed maniphest CLI guide (`docs/maniphest-cli.md`)
  - Phorge setup instructions (`docs/phorge-setup.md`)
  - Release process documentation (`docs/releasing.md`)
* **ReadTheDocs integration** - Hosted documentation at readthedocs.org

### Testing & Quality
* **Windows CI support** - Cross-platform testing in CI matrix

## Bug Fixes
* Fixed logging output to use stderr instead of stdout
* Improved URL validation and parsing logic
* Corrected YAML output formatting issues

## Other Notes
* Added `.editorconfig` for consistent code style
* Enhanced `.flake8` configuration
* Added `dependabot` support for automated dependency updates


# 0.3.0 (2023-01-13)

## Prelude

Maintenance release where we focus more on updating the current code and less on new features

The main new features to look for is the updated docker-compose.yml solution

Second major feature is the new maniphest app where we can query, add comment and create a batch of tasks from config file


## Upgrade notes

* Python support bumped up to minimum version of python 3.9


## New features

* Add in dependabot support to check for new python packages
* [#51](https://github.com/dynamist/phabfive/pull/51) - Add support for rendering a batch of tickets and bulk create tickets at one time
* Update support and logging feature to be more modern and better configurable from CLI
* Added new dependency jinja2


# 0.2.0 (2022-03-17)

## Prelude

Update to accommodate new Python versions and updated dependencies.

## Upgrade Notes

* Python 2.7 support has been dropped. The minimum version of Python now supported by Phabfive is version 3.8.

## Bug Fixes

* [#40](https://github.com/dynamist/phabfive/pull/40) - Update to anyconfig API >= 0.10.0

# 0.1.0 (2019-11-01)

## Prelude

Initial release of Phabfive.

Supported Phabricator app endpoints:

 - passphrase
 - diffusion
 - paste
 - user

## New Features

* [#23](https://github.com/dynamist/phabfive/pull/23) - Function to get clone uri(s) from repo
* [#22](https://github.com/dynamist/phabfive/pull/22) - Functionality to create Paste
* [#21](https://github.com/dynamist/phabfive/pull/21) - Raise exception when Conduit access is not accepted for Passphrase
* [#20](https://github.com/dynamist/phabfive/pull/20) - Add functionality to edit URI
* [#19](https://github.com/dynamist/phabfive/pull/19) - Feature/edit uri
* [#16](https://github.com/dynamist/phabfive/pull/16) - Feature/observe repositories
* [#14](https://github.com/dynamist/phabfive/pull/14) - Print data from user.whoami
* [#12](https://github.com/dynamist/phabfive/pull/12) - Errors now print to stderr
* [#11](https://github.com/dynamist/phabfive/pull/11) - Default to only listing active repositories
* [#10](https://github.com/dynamist/phabfive/pull/10) - Adding shortName
* [#9](https://github.com/dynamist/phabfive/pull/9) - Feature/get specified paste
* [#8](https://github.com/dynamist/phabfive/pull/8) - Repositories can now be created
* [#6](https://github.com/dynamist/phabfive/pull/6) - Avoid string default
* [#5](https://github.com/dynamist/phabfive/pull/5) - Pastes can now be listed, sort based on title
* [#3](https://github.com/dynamist/phabfive/pull/3) - Added Paste app

## Other Notes

* [#24](https://github.com/dynamist/phabfive/pull/24) - Enable RTD build and docs updates
* [#18](https://github.com/dynamist/phabfive/pull/18) - Add code coverage to tox
* [#17](https://github.com/dynamist/phabfive/pull/17) - Proper flake8 with Black
* [#4](https://github.com/dynamist/phabfive/pull/4) - Add encrypted notification config to .travis.yml
* [#2](https://github.com/dynamist/phabfive/pull/2) - Black-linting
* [#1](https://github.com/dynamist/phabfive/pull/1) - Added travis
