# -*- coding: utf-8 -*-
from enum import Enum


class OutputFormat(str, Enum):
    """Output format options for CLI commands."""

    rich = "rich"  # Human-readable with Rich formatting
    tree = "tree"  # Tree view with Rich Tree
    yaml = "yaml"  # Machine-readable YAML
    json = "json"  # Machine-readable JSON
    jsonl = "jsonl"  # Machine-readable newline-delimited JSON
    simple = "simple"  # Minimal output (e.g., just the secret for passphrase)


# Spellings accepted for --format and PHAB_FALLBACK that are not members of
# OutputFormat. They are rewritten before anything branches on the format, so
# no display code ever has to know about them.
FORMAT_ALIASES = {
    "strict": "yaml",
    "ndjson": "jsonl",
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
    "POLICY_LABELS",
    "PRIORITY_VALUES",
    "PRIORITY_DEFAULT",
    "REPO_STATUS_CHOICES",
    "REQUIRED",
    "VALIDATION_HINTS",
    "VALIDATORS",
]
