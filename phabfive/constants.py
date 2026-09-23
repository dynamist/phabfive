# -*- coding: utf-8 -*-
from enum import Enum


class OutputFormat(str, Enum):
    """Output format options for CLI commands."""

    rich = "rich"  # Human-readable with Rich formatting
    tree = "tree"  # Tree view with Rich Tree
    yaml = "yaml"  # Machine-readable YAML
    json = "json"  # Machine-readable JSON
    jsonl = "jsonl"  # Machine-readable newline-delimited JSON
    table = "table"  # Human-readable grid, for list commands
    value = "value"  # Bare values, no keys or decoration


# Spellings accepted for --format and PHAB_FALLBACK that are not members of
# OutputFormat. They are rewritten before anything branches on the format, so
# no display code ever has to know about them.
FORMAT_ALIASES = {
    "strict": "yaml",
    "ndjson": "jsonl",
    "simple": "value",
}

# The formats a program parses, as opposed to the ones a person reads.
# A command that writes rather than reads - create, edit, comment - answers
# one of these with the record `show` would give for the object it touched,
# and answers the rest with the human text it has always printed.
MACHINE_FORMATS = frozenset({"yaml", "json", "jsonl"})


def is_machine_format(output_format: str) -> bool:
    """Whether the caller asked for output a program is going to parse.

    Aliases resolve first, so ``strict`` and ``ndjson`` answer the same as
    the formats they name. ``value`` is deliberately not in the set: it is
    bare values for a shell pipeline, and a URL on its own already is one.

    Lives here rather than in phabfive.cli.output because phabfive.edit asks
    it too, and library code must not import the CLI - that module imports
    typer.
    """
    return FORMAT_ALIASES.get(output_format, output_format) in MACHINE_FORMATS


class AutoOption(str, Enum):
    """Auto-detect option for --ascii and --hyperlink."""

    always = "always"
    auto = "auto"
    never = "never"


# https://secure.phabricator.com/w/object_name_prefixes/
MONOGRAMS = {
    "diffusion": "R[0-9]+",
    "passphrase": "K[0-9]+",
    "paste": "P[0-9]+",
    "maniphest": "T[0-9]+",
}

# Monogram shortcuts for CLI: maps prefix letter to command expansion
MONOGRAM_SHORTCUT = {
    "T": ["maniphest", "show"],  # T123 → maniphest show T123
    "K": ["passphrase", "show"],  # K123 → passphrase show K123
    "P": ["paste", "show"],  # P123 → paste show P123
    "R": ["diffusion", "repo", "show"],  # R123 → diffusion repo show R123
}

# Apps that support "X123 'text'" → "app comment X123 'text'" shortcut
COMMENTS_SUPPORTED = ["maniphest", "paste"]

# Default languages for paste syntax highlighting (from Phabricator pygments.dropdown-choices)
# These are the languages shown in the Phabricator/Phorge Paste language dropdown by default.
# The actual list can be configured per-instance via pygments.dropdown-choices.
PASTE_LANGUAGES = [
    "apacheconf",
    "bash",
    "brainfuck",
    "c",
    "coffee-script",
    "cpp",
    "csharp",
    "css",
    "d",
    "diff",
    "django",
    "docker",
    "erb",
    "erlang",
    "go",
    "groovy",
    "haskell",
    "html",
    "http",
    "invisible",
    "java",
    "js",
    "json",
    "make",
    "mysql",
    "nginx",
    "objc",
    "perl",
    "php",
    "postgresql",
    "pot",
    "puppet",
    "python",
    "rainbow",
    "remarkup",
    "robotframework",
    "rst",
    "ruby",
    "sql",
    "tex",
    "text",
    "twig",
    "xml",
    "yaml",
]

# Phorge's I/O values for a repository URI. Which of them a given URI accepts
# depends on what kind it is - an observed URI takes neither "read" nor
# "readwrite", see demotion_io() - and only Phorge knows that, so phabfive
# refuses what is not an I/O value at all and leaves the rest to the server.
IO_URI_VALUES = ["default", "observe", "mirror", "read", "readwrite", "none"]

# `uri create` stays narrower on purpose: it adds a URI that carries a remote,
# which is what "observe" and "mirror" describe.
IO_NEW_URI_CHOICES = ["default", "observe", "mirror", "none"]

DISPLAY_CHOICES = ["default", "always", "never"]

# Spellings phabfive advertised before these constants matched Phorge, so
# scripts use them. Resolved before anything else sees the value, the way
# FORMAT_ALIASES is, which keeps them out of every comparison downstream.
IO_URI_ALIASES = {"never": "none"}
DISPLAY_ALIASES = {"hidden": "never"}

# What a URI actually does, in one line, keyed by its effective I/O value.
# The I/O value alone is Phorge's vocabulary; this is the answer to "so what
# is this URI for", which is the question someone reading `uri list` has.
URI_ROLES = {
    "observe": "Phorge pulls from here",
    "mirror": "Phorge pushes here",
    "readwrite": "clone + push",
    "read": "clone (read-only)",
    "none": "not in use",
}

# A disabled URI does nothing whatever its I/O says, so this overrides the
# table above rather than appearing in it.
URI_ROLE_DISABLED = "disabled"

REPO_STATUS_CHOICES = ["active", "inactive"]

# Phorge's policy keyword constants, labelled the way its web UI labels them.
# A policy can also carry a PHID - a project, or a custom policy rule - and
# those are passed through unresolved rather than guessed at.
POLICY_LABELS = {
    "public": "Public (No Login Required)",
    "users": "All Users",
    "admin": "Administrators",
    "no-one": "No One",
}

# The keywords a policy option accepts, in the order the web UI offers them.
# There is no policy.query endpoint on Phorge, so this cannot be discovered at
# runtime - and it has to be checked before anything is sent, because the API
# reads an unknown keyword as "nobody", not as a typo: --visible-to=nonsense
# is refused with the same self-lockout error as --visible-to=no-one.
POLICY_KEYWORDS = list(POLICY_LABELS)

# Where a repository record keeps each policy. The push one is spelled with
# the application prefix here, and without it in a transaction.
REPO_POLICY_FIELDS = {
    "view": "view",
    "edit": "edit",
    "push": "diffusion.push",
}

# What a repository's push policy is reported as when the repository is not
# hosted. Phorge stores and edits a push policy on every repository, but
# DiffusionRepositoryPoliciesManagementPanel declines to show one that cannot
# take effect, printing this sentence in place of the stored value:
#
#     $pushable = $repository->isHosted()
#       ? $descriptions[DiffusionPushCapability::CAPABILITY]
#       : phutil_tag('em', array(), pht('Not a Hosted Repository'));
#
# It is the string, and not null or a dropped key, so that every repository
# carries the same keys in every format and the value reads the same way in
# a terminal as it does in JSON. What a script keys on is Repository.Hosted,
# which is a boolean and is in the same record.
POLICY_NOT_HOSTED = "Not a Hosted Repository"

# The Conduit transaction types that set those policies, confirmed against the
# instance rather than guessed: diffusion.repository.edit answers an unknown
# type by listing every valid one, and these are the three it names.
#
# They are not spelled consistently, and the aliases are not interchangeable.
# PhabricatorPolicyEditEngineExtension declares the view and edit fields as
# "policy.view" and "policy.edit" but gives each an edit type key of its own
# ("view", "edit"), and it is the edit type key that Conduit takes - so
# "policy.view" is refused. DiffusionRepositoryEditEngine declares the push
# field as "policy.push" with no edit type key, so its field key stands in and
# the "push" alias is the one that is refused.
REPO_POLICY_TRANSACTIONS = {
    "view": "view",
    "edit": "edit",
    "push": "policy.push",
}

# Where a task record keeps each policy. maniphest.search reports all three on
# every task, unprefixed.
TASK_POLICY_FIELDS = {
    "view": "view",
    "interact": "interact",
    "edit": "edit",
}

# The Conduit transaction types that set a task's policies - and there are two
# of them, not three, which is the one place tasks and repositories genuinely
# differ.
#
# maniphest.edit answers an unknown type by listing every valid one, and that
# list holds "view" and "edit" and no interact type at all: "interact" and
# "policy.interact" are both refused. ManiphestTask::getPolicy is why. A task
# does not store an interact policy; it derives one, returning the view policy
# unless the task's status locks comments, in which case it returns "no-one".
# So "Can Interact With" is readable and is worth reading - it is how a locked
# task says so - but the way to change it is the task's status, not a policy.
TASK_POLICY_TRANSACTIONS = {
    "view": "view",
    "edit": "edit",
}

# Where a project record keeps each policy. project.search reports all three
# on every project, unprefixed.
PROJECT_POLICY_FIELDS = {
    "view": "view",
    "edit": "edit",
    "join": "join",
}

# The Conduit transaction types that set a project's policies. project.edit
# answers an unknown type by listing every valid one, and "view", "edit" and
# "join" are all among them - a project is the one object here whose third
# capability is settable, and it is the third member of the "-able By"
# family Phorge's web UI names: "Joinable By".
PROJECT_POLICY_TRANSACTIONS = {
    "view": "view",
    "edit": "edit",
    "join": "join",
}

# The statuses `project search --status` takes. "active" is the default and
# "any" asks for no status filter at all - the word `maniphest search
# --status=any` uses for the same thing, so both searches say "which
# statuses" alike. The deprecated --all is an alias for it.
PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_ARCHIVED = "archived"
PROJECT_STATUS_ANY = "any"
PROJECT_STATUS_CHOICES = [
    PROJECT_STATUS_ACTIVE,
    PROJECT_STATUS_ARCHIVED,
    PROJECT_STATUS_ANY,
]

# How project.search spells "any status" in its own status constraint
PROJECT_STATUS_ALL = "all"

# The icons a stock Phorge offers a project, for completion only. The set is
# instance configuration (projects.icons) and no Conduit method reports it,
# so completion adds whatever icons are actually in use, and the server is
# what validates the value sent. "milestone" is left out: Phorge gives it to
# every milestone and offers it for nothing else.
PROJECT_ICONS = [
    "project",
    "tag",
    "policy",
    "group",
    "folder",
    "timeline",
    "goal",
    "release",
    "bugs",
    "cleanup",
    "umbrella",
    "communication",
    "organization",
    "infrastructure",
    "account",
    "experimental",
]

# The icon Phorge gives every milestone, whatever icon is stored on it
PROJECT_MILESTONE_ICON = "milestone"

# The colours a project can be, in the order the web UI offers them. Unlike
# the icons this set is fixed in Phorge's code: projects.colors can relabel a
# colour or change the default, but cannot add one, and phabfive uses the
# keys, not the labels - so the list is exact. Phorge also shows "disabled"
# for an archived project, but that is a display colour nobody can choose.
PROJECT_COLORS = [
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "indigo",
    "violet",
    "pink",
    "grey",
    "checkered",
]

# The roles user.search reports on a user, in the order Phorge lists them.
# PhabricatorUser::getFieldValuesForConduit derives each from a flag on the
# account, so a role is a fact about the account rather than a group it was
# put in.
USER_ROLES = [
    "disabled",
    "bot",
    "list",
    "admin",
    "verified",
    "approved",
    "activated",
]

# `user search --role=any` requires no role at all. It is how to list every
# user on purpose, since a bare `user search` prints help instead.
USER_ROLE_ANY = "any"

# The roles user.search can filter on itself, and the constraint for each.
# The rest - verified, approved, activated - have none, so they are matched
# on the records the search returns.
USER_ROLE_CONSTRAINTS = {
    "disabled": "isDisabled",
    "bot": "isBot",
    "list": "isMailingList",
    "admin": "isAdmin",
}

CONFIGURABLES = [
    "PHAB_TOKEN",
    "PHAB_URL",
    "PHAB_SPACE",
    "PHAB_FALLBACK",
    "PHAB_CACHE",
    "PHAB_CACHE_TTL",
    "PHAB_CACHE_DIR",
    "PHAB_RETRY",
    "PHAB_BACKOFF_MAX",
    "PHAB_PACE",
]
DEFAULTS = {
    "PHAB_TOKEN": "",
    "PHAB_URL": "",
    "PHAB_SPACE": "S1",
    "PHAB_FALLBACK": "yaml",  # Output format when stdout is not a TTY (yaml, json or jsonl)
    "PHAB_CACHE": True,  # Cache API lookups that shell completion repeats
    "PHAB_CACHE_TTL": 0,  # 0 means use the per-namespace CACHE_TTLS below
    "PHAB_CACHE_DIR": "",  # Empty means appdirs.user_cache_dir("phabfive")
    "PHAB_RETRY": 3,  # How often a failed Conduit call is tried again
    "PHAB_BACKOFF_MAX": 5,  # The longest wait before a retry, in seconds
    "PHAB_PACE": 0,  # Seconds to keep between the writes of a batch edit
}

# Bumping this orphans every entry written by an older phabfive
CACHE_SCHEMA_VERSION = 1

# How long each kind of cached lookup stays fresh, in seconds. Instance
# configuration barely changes, user lists change rarely, while projects and
# their columns come and go.
CACHE_TTLS = {
    "users": 86400,  # 24 hours
    "spaces": 86400,  # 24 hours
    "priorities": 604800,  # 7 days
    "statuses": 604800,  # 7 days
    "status-map": 604800,  # 7 days
    "project-icons": 604800,  # 7 days
    "projects": 300,  # 5 minutes
    "columns": 300,  # 5 minutes
}
CACHE_TTL_DEFAULT = 300

# The whole maniphest.querystatuses answer, which the commands validate and
# display statuses with - unlike "statuses", which is the list of keys that
# completion offers
STATUS_MAP_CACHE_NAMESPACE = "status-map"
REQUIRED = ["PHAB_TOKEN", "PHAB_URL"]
VALIDATORS = {
    "PHAB_URL": r"^http(s)?://([a-zA-Z0-9._-]+|\[[a-fA-F0-9:\.]+\])(:[0-9]+)?/api(/)?$",
    "PHAB_TOKEN": "^[a-zA-Z0-9-]{32}$",
    "PHAB_FALLBACK": "^(yaml|json|jsonl|ndjson)$",
}
VALIDATION_HINTS = {"PHAB_URL": "example: https://we.phorge.it/api/"}
MISSING_CONFIG_HINTS = {
    "PHAB_TOKEN": "add token to ~/.arcrc or run: phabfive user setup",
    "PHAB_URL": 'create .arcconfig with: {"phabricator.uri": "https://we.phorge.it/"} or run: phabfive user setup',
}

PRIORITY_DEFAULT = "normal"

# Priority name to API numeric value mapping
# Used by the Phabricator/Phorge API for task priorities
PRIORITY_VALUES = {
    "unbreak": 100,
    "triage": 90,
    "high": 80,
    "normal": 50,
    "low": 25,
    "wish": 0,
}

# Result ordering for `maniphest search`, expressed as "<field>[:asc|:desc]".
#
# Listed in the same order as Phorge's own "Order" dropdown for Maniphest, so
# --help and shell completion read like the web UI.
MANIPHEST_ORDER_FIELDS = [
    "priority",
    "updated",
    "created",
    "closed",
    "title",
    "relevance",
]

# The direction a bare field means, i.e. the one people usually want: highest
# or newest first for the numeric fields, A-Z for the title. None marks a field
# that takes no direction at all.
MANIPHEST_ORDER_DIRECTIONS = {
    "priority": "desc",
    "updated": "desc",
    "created": "desc",
    "closed": "desc",
    "title": "asc",
    "relevance": None,
}

# Phorge picks the first builtin order when the API is given none, and for
# Maniphest that is "priority" -- the same default as the web UI.
MANIPHEST_ORDER_DEFAULT = "priority"


def _order_choices(fields, directions):
    """Every spelling an app's order parser accepts, for help and completion.

    Both directions for every directional field, so nothing is available
    only by implication, and the bare name alone for a directionless one.
    """
    return [
        value
        for field in fields
        for value in (
            [field]
            if directions[field] is None
            else [field, f"{field}:asc", f"{field}:desc"]
        )
    ]


# Every spelling the parser accepts, for error messages and docs. Both
# directions exist for every directional field, so nothing is implicit-only.
# Built by the same helper as every other app's table - four order tables and
# one rule, so a change to what a spelling is reaches all of them.
MANIPHEST_ORDER_CHOICES = _order_choices(
    MANIPHEST_ORDER_FIELDS, MANIPHEST_ORDER_DIRECTIONS
)


# Result ordering for the other searches. One table per app, because the
# orders are per app: `project.search` can order by name and `paste.search`
# cannot, and offering a field the endpoint has no order for would mean
# reading the whole instance to sort it here - which is what `--limit` then
# takes an arbitrary subset of.
#
# Every field below maps to something PhabricatorProjectQuery,
# PhabricatorPasteQuery or PhabricatorPeopleQuery can actually order by, as
# a builtin order or as a column vector; see each app's *_API_ORDERS.

# project.search: builtin orders name, newest, created, oldest, relevance,
# plus the "name" column, which takes a "-" for Z-A.
PROJECT_ORDER_FIELDS = ["name", "created", "relevance"]
PROJECT_ORDER_DIRECTIONS = {"name": "asc", "created": "desc", "relevance": None}
# A-Z, which is the order `project search` has always printed and the order
# Phorge's own project list opens in.
PROJECT_ORDER_DEFAULT = "name"
PROJECT_ORDER_CHOICES = _order_choices(PROJECT_ORDER_FIELDS, PROJECT_ORDER_DIRECTIONS)
PROJECT_ORDER_SUGGESTIONS = {
    "newest": "created",
    "oldest": "created:asc",
    "id": "created",
    "title": "name",
}

# paste.search: builtin orders newest, created, oldest, relevance. There is
# no title order - PhabricatorPasteQuery answers order key "title" with
# "does not support sorting by order key" - so `title` is not offered.
PASTE_ORDER_FIELDS = ["created", "relevance"]
PASTE_ORDER_DIRECTIONS = {"created": "desc", "relevance": None}
PASTE_ORDER_DEFAULT = "created"
PASTE_ORDER_CHOICES = _order_choices(PASTE_ORDER_FIELDS, PASTE_ORDER_DIRECTIONS)
PASTE_ORDER_SUGGESTIONS = {
    "newest": "created",
    "oldest": "created:asc",
    "id": "created",
}

# user.search: builtin orders newest, created, oldest, relevance, plus the
# "username" column both ways. Sorted by username is what `user search` has
# always printed, and it is now what the server is asked for, so a --limit
# keeps the first N usernames rather than an arbitrary N sorted afterwards.
USER_ORDER_FIELDS = ["username", "created", "relevance"]
USER_ORDER_DIRECTIONS = {"username": "asc", "created": "desc", "relevance": None}
USER_ORDER_DEFAULT = "username"
USER_ORDER_CHOICES = _order_choices(USER_ORDER_FIELDS, USER_ORDER_DIRECTIONS)
USER_ORDER_SUGGESTIONS = {
    "newest": "created",
    "oldest": "created:asc",
    "id": "created",
    "name": "username",
    "realname": "username",
}

# The statuses `paste search --status` takes, which are the two values
# paste.search's "statuses" constraint knows. An unknown one is answered
# with an empty result rather than an error, so phabfive refuses it first.
PASTE_STATUS_ACTIVE = "active"
PASTE_STATUS_ARCHIVED = "archived"
PASTE_STATUS_CHOICES = [PASTE_STATUS_ACTIVE, PASTE_STATUS_ARCHIVED]


__all__ = [
    "AutoOption",
    "MISSING_CONFIG_HINTS",
    "CACHE_SCHEMA_VERSION",
    "CACHE_TTL_DEFAULT",
    "CACHE_TTLS",
    "STATUS_MAP_CACHE_NAMESPACE",
    "CONFIGURABLES",
    "DEFAULTS",
    "DISPLAY_ALIASES",
    "DISPLAY_CHOICES",
    "FORMAT_ALIASES",
    "IO_NEW_URI_CHOICES",
    "IO_URI_ALIASES",
    "IO_URI_VALUES",
    "MACHINE_FORMATS",
    "COMMENTS_SUPPORTED",
    "MANIPHEST_ORDER_CHOICES",
    "MANIPHEST_ORDER_DEFAULT",
    "MANIPHEST_ORDER_DIRECTIONS",
    "MANIPHEST_ORDER_FIELDS",
    "MONOGRAM_SHORTCUT",
    "MONOGRAMS",
    "OutputFormat",
    "PASTE_LANGUAGES",
    "PASTE_ORDER_CHOICES",
    "PASTE_ORDER_DEFAULT",
    "PASTE_ORDER_DIRECTIONS",
    "PASTE_ORDER_FIELDS",
    "PASTE_ORDER_SUGGESTIONS",
    "PASTE_STATUS_ACTIVE",
    "PASTE_STATUS_ARCHIVED",
    "PASTE_STATUS_CHOICES",
    "POLICY_KEYWORDS",
    "POLICY_LABELS",
    "POLICY_NOT_HOSTED",
    "PRIORITY_VALUES",
    "PRIORITY_DEFAULT",
    "PROJECT_ORDER_CHOICES",
    "PROJECT_ORDER_DEFAULT",
    "PROJECT_ORDER_DIRECTIONS",
    "PROJECT_ORDER_FIELDS",
    "PROJECT_ORDER_SUGGESTIONS",
    "REPO_POLICY_FIELDS",
    "REPO_POLICY_TRANSACTIONS",
    "REPO_STATUS_CHOICES",
    "REQUIRED",
    "TASK_POLICY_FIELDS",
    "TASK_POLICY_TRANSACTIONS",
    "URI_ROLES",
    "USER_ORDER_CHOICES",
    "USER_ORDER_DEFAULT",
    "USER_ORDER_DIRECTIONS",
    "USER_ORDER_FIELDS",
    "USER_ORDER_SUGGESTIONS",
    "URI_ROLE_DISABLED",
    "VALIDATION_HINTS",
    "VALIDATORS",
    "is_machine_format",
]
