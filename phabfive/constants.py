# -*- coding: utf-8 -*-

from enum import Enum, auto

# https://secure.phabricator.com/w/object_name_prefixes/
MONOGRAMS = {"diffusion": "R[0-9]+", "passphrase": "K[0-9]+", "paste": "P[0-9]+"}
# IO_EDIT_URI_VALUES = ["default", "read", "write", "never"]
IO_NEW_URI_VALUES = ["default", "observe", "mirror", "never"]
DISPLAY_VALUES = ["default", "always", "hidden"]

class EnumAutoNameLowerCase(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.lower()

    def __str__(self):
        return str(self.value)

    def __repr__(self):
        return str(self.value)


class Vcs(EnumAutoNameLowerCase):
    GIT = auto()
    SVN = auto()
    HG = auto()


class Status(EnumAutoNameLowerCase):
    ACTIVE = auto()
    INACTIVE = auto()

REPO_STATUS_CHOICES = [str(Status.ACTIVE), str(Status.INACTIVE)]
