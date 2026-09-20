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
    Validate a credential record and return its PHID.

    Valid credential types are: ssh-generated-key, ssh-key-text, token

    Parameters
    ----------
    credential : dict
        A credential record from Passphrase.get_credential_record, with
        'phid', 'type' and 'monogram' keys

    Returns
    -------
    str
        Credential PHID if valid

    Raises
    ------
    PhabfiveDataException
        If credential type is not valid
    """
    valid_credential_types = ["ssh-generated-key", "ssh-key-text", "token"]
    credential_type = credential.get("type")

    if credential_type not in valid_credential_types:
        monogram = credential.get("monogram") or credential.get("phid")

        raise PhabfiveDataException(
            f"{monogram} is not type of 'ssh-generated-key', 'ssh-key-text' or 'token' "
            f"but type '{credential_type}'"
        )

    return credential.get("phid")
