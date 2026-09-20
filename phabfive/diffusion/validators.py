# -*- coding: utf-8 -*-

"""Input validation functions for Diffusion module."""

import re

from phabfive.constants import (
    DISPLAY_ALIASES,
    DISPLAY_CHOICES,
    IO_URI_ALIASES,
    IO_URI_VALUES,
    MONOGRAMS,
)
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException


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


def _quoted(choices):
    """Name the valid values the way an error message reads them out."""
    quoted = [f"'{choice}'" for choice in choices]

    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def resolve_io_value(io, choices=None):
    """Resolve a URI I/O value to what Phorge calls it.

    An accepted-but-old spelling is rewritten here, before anything compares
    the value to what the URI already carries, so nothing downstream has to
    know two names for the same thing.

    Parameters
    ----------
    io : str
        The I/O value as the caller wrote it
    choices : list, optional
        The values to accept, defaulting to every I/O value Phorge has.
        `uri create` passes a narrower list.

    Returns
    -------
    str
        The value Phorge knows it by

    Raises
    ------
    PhabfiveConfigException
        If the value is not an I/O value
    """
    choices = IO_URI_VALUES if choices is None else choices
    resolved = IO_URI_ALIASES.get(io, io)

    if resolved not in choices:
        raise PhabfiveConfigException(
            f"'{io}' is not valid. Valid IO values are {_quoted(choices)}"
        )

    return resolved


def resolve_display_value(display):
    """Resolve a URI display value to what Phorge calls it.

    Parameters
    ----------
    display : str
        The display value as the caller wrote it

    Returns
    -------
    str
        The value Phorge knows it by

    Raises
    ------
    PhabfiveConfigException
        If the value is not a display value
    """
    resolved = DISPLAY_ALIASES.get(display, display)

    if resolved not in DISPLAY_CHOICES:
        raise PhabfiveConfigException(
            f"'{display}' is not valid. Valid Display values are {_quoted(DISPLAY_CHOICES)}"
        )

    return resolved
