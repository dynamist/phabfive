# -*- coding: utf-8 -*-
"""Resolve what somebody typed for a user to the user it names.

Every option that takes a user - ``--assigned``, ``--author``, ``--assign``,
``--subscribe``, ``--member`` and the rest - takes the same four spellings,
because they all come through here:

==================  ====================================================
``username``        ``user.search`` by username, case-insensitively
``@username``       the same; the ``@`` is optional
``@me``             whoever is running the command (see phabfive.me)
``PHID-USER-...``   ``user.search`` by PHID, so a typo is still an error
==================  ====================================================

The policy options take ``@username`` too, but only with the ``@``, because a
bare word there is a policy keyword - see phabfive.policy.
"""

# phabfive imports
from phabfive.exceptions import PhabfiveDataException
from phabfive.me import is_me, whoami_me

USER_PHID_PREFIX = "PHID-USER-"


def _search_users(phab, constraints):
    try:
        return (phab.user.search(constraints=constraints) or {}).get("data") or []
    except Exception as e:
        raise PhabfiveDataException(f"Failed to look up users: {e}")


def resolve_user_phids(phab, values, option=None):
    """Resolve users to PHIDs, in one lookup per spelling however many there are.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    values : list
        Usernames, with or without a leading ``@``, ``@me`` for whoever is
        running the command, or user PHIDs
    option : str, optional
        The option the values came from, named in any error about ``@me``

    Returns
    -------
    dict
        What was typed to (PHID, username), in the order given

    Raises
    ------
    PhabfiveDataException
        If any of them is not a user, naming every one that is not - so a
        typo in the third of five members is reported, not the first two
        added and the rest silently dropped. ``@me`` always resolves to the
        caller; a bare ``me`` is looked up as the username it is (#496)
    """
    resolved = {}
    by_username = {}
    by_phid = []

    for value in values:
        if is_me(value):
            whoami = whoami_me(phab, option=option)
            resolved[value] = (whoami["phid"], whoami.get("userName") or "me")
        elif value.startswith(USER_PHID_PREFIX):
            by_phid.append(value)
        else:
            by_username[value] = value[1:] if value.startswith("@") else value

    missing = []

    if by_username:
        found = {
            user["fields"]["username"].casefold(): user
            for user in _search_users(
                phab, {"usernames": sorted(set(by_username.values()))}
            )
            if user.get("fields", {}).get("username")
        }

        for value, name in by_username.items():
            user = found.get(name.casefold())
            if user:
                resolved[value] = (user["phid"], user["fields"]["username"])
            else:
                missing.append(value)

    if by_phid:
        # Asked about rather than passed through: a mistyped PHID in a search
        # filter would otherwise look like "no matches", and the username is
        # what a preview shows
        found = {
            user["phid"]: user
            for user in _search_users(phab, {"phids": sorted(set(by_phid))})
        }

        for value in by_phid:
            user = found.get(value)
            if user:
                resolved[value] = (value, (user.get("fields") or {}).get("username"))
            else:
                missing.append(value)

    if missing:
        listed = ", ".join(f"'{value}'" for value in values if value in missing)
        raise PhabfiveDataException(f"No such user: {listed}")

    return {value: resolved[value] for value in values}


def resolve_user_phid(phab, value, option=None):
    """Resolve one user to a PHID and a username.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    value : str
        A username, with or without a leading ``@``, ``@me``, or a user PHID
    option : str, optional
        The option the value came from, named in any error about ``@me``

    Returns
    -------
    tuple
        (PHID, username)

    Raises
    ------
    PhabfiveDataException
        If it is not a user, or it is ``@me`` on an instance that has a user
        called "me"
    """
    return resolve_user_phids(phab, [value], option=option)[value]


__all__ = [
    "USER_PHID_PREFIX",
    "resolve_user_phid",
    "resolve_user_phids",
]
