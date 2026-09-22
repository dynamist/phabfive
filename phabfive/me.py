# -*- coding: utf-8 -*-
"""``@me``: whoever is running the command.

Every option that takes a user takes ``@me`` - ``--assigned``, ``--author``,
``--assign``, ``--subscribe``, ``--member`` and the policy options - and they
all resolve it here, so that they agree on what it means.

It is refused outright on an instance that has a user called ``me``, because
``@me`` then names two people and there is no safe way to guess which: picking
the caller assigns, subscribes or hands over what the other one should have
had, and picking the user does the same the other way round. That user is
still reachable by PHID, and the caller by their own username.
"""

# phabfive imports
from phabfive.exceptions import PhabfiveDataException

# The spelling that means whoever is running the command
ME = "@me"


def is_me(value):
    """Whether a value is ``@me``, in any case.

    Phorge usernames are case-insensitive, so ``@Me`` is not somebody else.

    Parameters
    ----------
    value : str or None
        What was typed

    Returns
    -------
    bool
    """
    return isinstance(value, str) and value.strip().casefold() == ME


def whoami_me(phab, option=None):
    """The ``user.whoami`` answer for whoever is running the command.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    option : str, optional
        The option ``@me`` came from, named in any error message

    Returns
    -------
    dict
        The ``user.whoami`` result, which is certain to carry a ``phid``

    Raises
    ------
    PhabfiveDataException
        If either lookup fails, or a user called "me" exists
    """
    where = f"{option}: " if option else ""

    try:
        result = phab.user.search(constraints={"usernames": ["me"]})
    except Exception as e:
        raise PhabfiveDataException(f"Failed to resolve {ME}: {e}")

    data = (result or {}).get("data") or []

    if data:
        raise PhabfiveDataException(
            f"{where}{ME} is ambiguous: this instance has a user called 'me' "
            f"({data[0]['phid']}). Give that user's PHID, or your own, instead"
        )

    try:
        whoami = phab.user.whoami()
    except Exception as e:
        raise PhabfiveDataException(f"Failed to resolve {ME}: {e}")

    if not (whoami or {}).get("phid"):
        raise PhabfiveDataException(f"Failed to resolve {ME}: no PHID for you")

    return whoami


def resolve_me(phab, option=None):
    """The PHID of whoever is running the command.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    option : str, optional
        The option ``@me`` came from, named in any error message

    Returns
    -------
    str
        The caller's PHID

    Raises
    ------
    PhabfiveDataException
        If either lookup fails, or a user called "me" exists
    """
    return whoami_me(phab, option=option)["phid"]


__all__ = [
    "ME",
    "is_me",
    "resolve_me",
    "whoami_me",
]
