# -*- coding: utf-8 -*-

from enum import Enum, auto

# https://secure.phabricator.com/w/object_name_prefixes/
MONOGRAMS = {"diffusion": "R[0-9]+", "passphrase": "K[0-9]+", "paste": "P[0-9]+"}


class EnumAutoNameLowerCase(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.lower()

    def __str__(self):
        return str(self.value)

    def __repr__(self):
        return str(self.value)


class Display(EnumAutoNameLowerCase):
    DEFAULT = auto()     # "Default: Use default behaviour"
    ALWAYS = auto()      # "Visible: Show as a clone URL"
    NEVER = auto()       # "Hidden: Do not show as a clone URL"

class Io(EnumAutoNameLowerCase):
    DEFAULT = auto()     # "Default: Use default behaviour"
    NEVER = auto()       # "No I/O: Do not perform any I/O""
    # Local
    READ = auto()        # "Read Only: Serve repository in read-only mode"
    WRITE = auto()       # "Read/Write: Serve repository in read/write mode"
    # Remote
    OBSERVE = auto()     # "Observe: Copy from a remote"
    MIRROR = auto()      # "Mirror: Push a copy to a remote"

class Vcs(EnumAutoNameLowerCase):
    GIT = auto()
    SVN = auto()
    HG = auto()

class Status(EnumAutoNameLowerCase):
    ACTIVE = auto()
    INACTIVE = auto()

REPO_STATUS_CHOICES = [Status.ACTIVE, Status.INACTIVE]
