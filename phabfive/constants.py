# -*- coding: utf-8 -*-

from enum import Enum

REPO_STATUS_CHOICES = ["active", "inactive"]

# https://secure.phabricator.com/w/object_name_prefixes/
MONOGRAMS = {"diffusion": "R[0-9]+", "passphrase": "K[0-9]+", "paste": "P[0-9]+"}

class Display(Enum):
    DEFAULT = "default"  # "Default: Use default behaviour"
    ALWAYS = "always"    # "Visible: Show as a clone URL"
    NEVER = "never"      # "Hidden: Do not show as a clone URL"

class Io(Enum):
    DEFAULT = "default"  # "Default: Use default behaviour"
    NEVER = "never"      # "No I/O: Do not perform any I/O""
    # Local
    READ = "read"        # "Read Only: Serve repository in read-only mode"
    WRITE = "write"      # "Read/Write: Serve repository in read/write mode"
    # Remote
    OBSERVE = "observe"  # "Observe: Copy from a remote"
    MIRROR = "mirror"    # "Mirror: Push a copy to a remote"
