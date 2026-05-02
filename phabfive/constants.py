# -*- coding: utf-8 -*-
from enum import Enum


class OutputFormat(str, Enum):
    """Output format options for CLI commands."""

    rich = "rich"  # Human-readable with Rich formatting
    tree = "tree"  # Tree view with Rich Tree
    yaml = "yaml"  # Machine-readable YAML
    json = "json"  # Machine-readable JSON
    simple = "simple"  # Minimal output (e.g., just the secret for passphrase)


class AutoOption(str, Enum):
    """Auto-detect option for --ascii and --hyperlink."""

    always = "always"
    auto = "auto"
    never = "never"


class LogLevel(str, Enum):
    """Log level options."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


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
    "R": ["diffusion", "branch", "list"],  # R123 → diffusion branch list R123
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

# IO_EDIT_URI_VALUES = ["default", "read", "write", "never"]
IO_NEW_URI_CHOICES = ["default", "observe", "mirror", "never"]
DISPLAY_CHOICES = ["default", "always", "hidden"]
REPO_STATUS_CHOICES = ["active", "inactive"]

CONFIGURABLES = [
    "PHABFIVE_DEBUG",
    "PHAB_TOKEN",
    "PHAB_URL",
    "PHAB_SPACE",
    "PHAB_FALLBACK",
]
DEFAULTS = {
    "PHABFIVE_DEBUG": False,
    "PHAB_TOKEN": "",
    "PHAB_URL": "",
    "PHAB_SPACE": "S1",
    "PHAB_FALLBACK": "yaml",  # Output format when stdout is not a TTY (yaml or json)
}
REQUIRED = ["PHAB_TOKEN", "PHAB_URL"]
VALIDATORS = {
    "PHAB_URL": r"^http(s)?://([a-zA-Z0-9._-]+|\[[a-fA-F0-9:\.]+\])(:[0-9]+)?/api(/)?$",
    "PHAB_TOKEN": "^[a-zA-Z0-9-]{32}$",
    "PHAB_FALLBACK": "^(yaml|json)$",
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

__all__ = [
    "AutoOption",
    "MISSING_CONFIG_HINTS",
    "CONFIGURABLES",
    "DEFAULTS",
    "DISPLAY_CHOICES",
    "IO_NEW_URI_CHOICES",
    "COMMENTS_SUPPORTED",
    "LogLevel",
    "MONOGRAM_SHORTCUT",
    "MONOGRAMS",
    "OutputFormat",
    "PASTE_LANGUAGES",
    "PRIORITY_VALUES",
    "PRIORITY_DEFAULT",
    "REPO_STATUS_CHOICES",
    "REQUIRED",
    "VALIDATION_HINTS",
    "VALIDATORS",
]
