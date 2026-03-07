# -*- coding: utf-8 -*-
from enum import Enum


class OutputFormat(str, Enum):
    """Output format options for CLI commands."""

    rich = "rich"  # Human-readable with Rich formatting
    tree = "tree"  # Tree view with Rich Tree
    yaml = "yaml"  # Machine-readable YAML
    json = "json"  # Machine-readable JSON


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
    "K": ["passphrase"],  # K123 → passphrase K123
    "P": ["paste", "show"],  # P123 → paste show P123
    "R": ["diffusion", "branch", "list"],  # R123 → diffusion branch list R123
}

# Apps that support "X123 'text'" → "app comment X123 'text'" shortcut
# Currently only maniphest supports comments. Paste could be added later.
COMMENTS_SUPPORTED = ["maniphest"]
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
VALID_EXAMPLES = {"PHAB_URL": "example: http://127.0.0.1/api/"}
CONFIG_EXAMPLES = {
    "PHAB_TOKEN": "example: export PHAB_TOKEN=cli-RANDOMRANDOMRANDOMRANDOMRAND",
    "PHAB_URL": "example: echo PHAB_URL: https://dynamist.phacility.com/api/ >> ~/.config/phabfive.yaml",
}

TICKET_PRIORITY_UNBREAK = "unbreak"
TICKET_PRIORITY_TRIAGE = "triage"
TICKET_PRIORITY_HIGH = "high"
TICKET_PRIORITY_NORMAL = "normal"
TICKET_PRIORITY_LOW = "low"
TICKET_PRIORITY_WISH = "wish"

__all__ = [
    "AutoOption",
    "CONFIG_EXAMPLES",
    "CONFIGURABLES",
    "DEFAULTS",
    "DISPLAY_CHOICES",
    "IO_NEW_URI_CHOICES",
    "COMMENTS_SUPPORTED",
    "LogLevel",
    "MONOGRAM_SHORTCUT",
    "MONOGRAMS",
    "OutputFormat",
    "REPO_STATUS_CHOICES",
    "REQUIRED",
    "TICKET_PRIORITY_HIGH",
    "TICKET_PRIORITY_LOW",
    "TICKET_PRIORITY_NORMAL",
    "TICKET_PRIORITY_TRIAGE",
    "TICKET_PRIORITY_UNBREAK",
    "TICKET_PRIORITY_WISH",
    "VALID_EXAMPLES",
    "VALIDATORS",
]
