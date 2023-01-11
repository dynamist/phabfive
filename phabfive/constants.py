# -*- coding: utf-8 -*-
# https://secure.phabricator.com/w/object_name_prefixes/
MONOGRAMS = {
    "diffusion": "R[0-9]+",
    "passphrase": "K[0-9]+",
    "paste": "P[0-9]+",
    "maniphest": "T[0-9]+",
}
# IO_EDIT_URI_VALUES = ["default", "read", "write", "never"]
IO_NEW_URI_CHOICES = ["default", "observe", "mirror", "never"]
DISPLAY_CHOICES = ["default", "always", "hidden"]
REPO_STATUS_CHOICES = ["active", "inactive"]

CONFIGURABLES = ["PHABFIVE_DEBUG", "PHAB_TOKEN", "PHAB_URL"]
DEFAULTS = {"PHABFIVE_DEBUG": False, "PHAB_TOKEN": "", "PHAB_URL": ""}
REQUIRED = ["PHAB_TOKEN", "PHAB_URL"]
VALIDATORS = {
    "PHAB_URL": "^http(s)?://[a-zA-Z0-9._-]+/api/$",
    "PHAB_TOKEN": "^[a-zA-Z0-9-]{32}$",
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
