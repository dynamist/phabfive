# -*- coding: utf-8 -*-
"""``@me``: whoever is running the command.

Every option that takes a user takes ``@me`` - ``--assigned``, ``--author``,
``--assign``, ``--subscribe``, ``--member`` and the policy options - and they
all resolve it here, so that they agree on what it means.

``@me`` is a **keyword**, and the ``@`` is what makes it one. It means the
caller on every instance, including one that has a user whose username is
``me``: a username does not get to take a keyword away from everybody else.
This is the single place in phabfive where the sigil carries meaning -
everywhere else ``alice`` and ``@alice`` are the same user - so it is
documented wherever a user value is, and :func:`is_me` is deliberately strict
about it.

To name the *account* called ``me``, write it without the sigil, which is an
ordinary username lookup::

    --author=@me     # you
    --author=me      # the user whose username is "me"

phabfive used to refuse ``@me`` outright on such an instance and demand a PHID
(#496). That was the safe reading of an ambiguity, but the cost landed on the
wrong person: every caller on the instance lost ``@me`` because of an account
they had never heard of, and looking up your own PHID is worse than the thing
it replaced.

``@none`` is the other keyword, and it follows the same rule: it means "no
assignee" even on an instance with a user called ``none``, and ``none``
without the sigil is that user (#513). Only the options where "nobody" has a
meaning take it - ``--assigned``, where it is Phorge's ``none()`` datasource
function, and ``--assign``, where it clears the owner like ``--unassign``.
Every other option refuses it through :func:`refuse_none` rather than
looking up a user called "none" behind the caller's back.
"""

# phabfive imports
from phabfive.exceptions import PhabfiveDataException, PhabfiveInputException

# The spelling that means whoever is running the command
ME = "@me"

# The spelling that means no assignee
NONE = "@none"

# What maniphest.search's `assigned` constraint takes for "no assignee". It
# mixes with user PHIDs as OR, so "@none,@me" is unassigned or mine.
NONE_DATASOURCE = "none()"


def is_me(value):
    """Whether a value is ``@me``, in any case.

    Phorge usernames are case-insensitive, so ``@Me`` is not somebody else.

    The sigil is required: a bare ``me`` is a username, and this returns
    False for it. That is what makes the account called ``me`` reachable.

    Parameters
    ----------
    value : str or None
        What was typed

    Returns
    -------
    bool
    """
    return isinstance(value, str) and value.strip().casefold() == ME


def is_none(value):
    """Whether a value is ``@none``, in any case.

    The sigil is required, as it is for :func:`is_me`: a bare ``none`` is the
    user called "none".

    Parameters
    ----------
    value : str or None
        What was typed

    Returns
    -------
    bool
    """
    return isinstance(value, str) and value.strip().casefold() == NONE


def refuse_none(option=None):
    """The error for ``@none`` given to an option that takes only users.

    Parameters
    ----------
    option : str, optional
        The option ``@none`` came from, named in the message

    Returns
    -------
    PhabfiveInputException
        To be raised by the caller
    """
    where = f"{option}: " if option else ""

    return PhabfiveInputException(
        f"{where}{NONE} means no assignee, which only --assigned and --assign "
        f"take. To name the user called 'none', leave out the @"
    )


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
        If the lookup fails, or answers without a PHID
    """
    where = f"{option}: " if option else ""

    try:
        whoami = phab.user.whoami()
    except Exception as e:
        raise PhabfiveDataException(f"{where}Failed to resolve {ME}: {e}")

    if not (whoami or {}).get("phid"):
        raise PhabfiveDataException(f"{where}Failed to resolve {ME}: no PHID for you")

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
        If the lookup fails, or answers without a PHID
    """
    return whoami_me(phab, option=option)["phid"]


__all__ = [
    "ME",
    "NONE",
    "NONE_DATASOURCE",
    "is_me",
    "is_none",
    "refuse_none",
    "resolve_me",
    "whoami_me",
]
