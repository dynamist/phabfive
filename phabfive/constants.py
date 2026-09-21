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

# The colours a project can be, in the order the web UI offers them. Unlike
# the icons this set is fixed in Phorge's code, not configuration, so the
# list is exact.
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

CONFIGURABLES = [
    "PHAB_TOKEN",
    "PHAB_URL",
    "PHAB_SPACE",
    "PHAB_FALLBACK",
    "PHAB_CACHE",
    "PHAB_CACHE_TTL",
    "PHAB_CACHE_DIR",
]
DEFAULTS = {
    "PHAB_TOKEN": "",
    "PHAB_URL": "",
    "PHAB_SPACE": "S1",
    "PHAB_FALLBACK": "yaml",  # Output format when stdout is not a TTY (yaml, json or jsonl)
    "PHAB_CACHE": True,  # Cache API lookups that shell completion repeats
    "PHAB_CACHE_TTL": 0,  # 0 means use the per-namespace CACHE_TTLS below
    "PHAB_CACHE_DIR": "",  # Empty means appdirs.user_cache_dir("phabfive")
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
    "project-icons": 604800,  # 7 days
    "projects": 300,  # 5 minutes
    "columns": 300,  # 5 minutes
}
CACHE_TTL_DEFAULT = 300
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

# Every spelling the parser accepts, for error messages and docs. Both
# directions exist for every directional field, so nothing is implicit-only.
MANIPHEST_ORDER_CHOICES = [
    value
    for field in MANIPHEST_ORDER_FIELDS
    for value in (
        [field]
        if MANIPHEST_ORDER_DIRECTIONS[field] is None
        else [field, f"{field}:asc", f"{field}:desc"]
    )
]

__all__ = [
    "AutoOption",
    "MISSING_CONFIG_HINTS",
    "CACHE_SCHEMA_VERSION",
    "CACHE_TTL_DEFAULT",
    "CACHE_TTLS",
    "CONFIGURABLES",
    "DEFAULTS",
    "DISPLAY_ALIASES",
    "DISPLAY_CHOICES",
    "FORMAT_ALIASES",
    "IO_NEW_URI_CHOICES",
    "IO_URI_ALIASES",
    "IO_URI_VALUES",
    "COMMENTS_SUPPORTED",
    "MANIPHEST_ORDER_CHOICES",
    "MANIPHEST_ORDER_DEFAULT",
    "MANIPHEST_ORDER_DIRECTIONS",
    "MANIPHEST_ORDER_FIELDS",
    "MONOGRAM_SHORTCUT",
    "MONOGRAMS",
    "OutputFormat",
    "PASTE_LANGUAGES",
    "POLICY_KEYWORDS",
    "POLICY_LABELS",
    "POLICY_NOT_HOSTED",
    "PRIORITY_VALUES",
    "PRIORITY_DEFAULT",
    "REPO_POLICY_FIELDS",
    "REPO_POLICY_TRANSACTIONS",
    "REPO_STATUS_CHOICES",
    "REQUIRED",
    "TASK_POLICY_FIELDS",
    "TASK_POLICY_TRANSACTIONS",
    "URI_ROLES",
    "URI_ROLE_DISABLED",
    "VALIDATION_HINTS",
    "VALIDATORS",
]
