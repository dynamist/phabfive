# -*- coding: utf-8 -*-

"""Input validation functions for Diffusion module."""

import re

from phabfive.constants import MONOGRAMS
from phabfive.exceptions import PhabfiveDataException


def validate_repo_identifier(repo_id):
    """
    Validate repository identifier format (e.g., R123).

    Parameters
    ----------
    repo_id : str
        Repository identifier to validate

    Returns
    -------
    re.Match or None
        Match object if valid, None otherwise
    """
    return re.match(f"^{MONOGRAMS['diffusion']}$", repo_id)


def validate_credential_type(credential):
    """
    Validate credential type and extract PHID.

    Valid credential types are: ssh-generated-key, ssh-key-text, token

    Parameters
    ----------
    credential : dict
        Credential data from Phabricator API

    Returns
    -------
    str
        Credential PHID if valid

    Raises
    ------
    PhabfiveDataException
        If credential type is not valid
    """
    credential_phid = None
    valid_credential_types = ["ssh-generated-key", "ssh-key-text", "token"]

    for key in credential:
        if "PHID" in key:
            credential_phid = key
            credential_type = credential.get(key).get("type")

            if credential_type not in valid_credential_types:
                m = credential[credential_phid]["monogram"]
                t = credential[credential_phid]["type"]

                raise PhabfiveDataException(
                    f"{m} is not type of 'ssh-generated-key', 'ssh-key-text' or 'token' but type '{t}'"
                )

    return credential_phid
