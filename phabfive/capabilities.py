# -*- coding: utf-8 -*-

"""What the server's Conduit accepts, found out by asking it.

phabfive talks to Phabricator, frozen at its end of life, and to Phorge,
which keeps adding to the API. Neither reports a version over Conduit, so a
difference between them can only be found by trying: `conduit.query` lists
the methods, while a `*.search` method's constraints are described only as
``map<string, wild>``, and the one way to learn whether one is known is to
send it and be answered ``Constraint "x" is not a valid constraint for this
query.``

What is learned is remembered per client, for the life of the process - an
in-memory memo, not `phabfive.cache`, which is for shell completion only.
Siblings built by `Phabfive._from_parent` share a client and so share what
it learned.

A caller that already sends the constraint should not probe first: it sends
its real request, and on `rejects_constraint` records the answer with
`remember_constraint` and falls back. That keeps a server that has the
constraint answering exactly the requests it always did.
"""

import logging
import weakref

from phabfive.exceptions import PhabfiveAPIException

log = logging.getLogger(__name__)

#: What each client is known to accept: ``(method, constraint)`` to a bool,
#: and ``(method, None)`` for whether a method exists.
_known: "weakref.WeakKeyDictionary[object, dict[tuple[str, str | None], bool]]" = (
    weakref.WeakKeyDictionary()
)


def _memo(phab):
    """The memo of one client, or a throwaway one if it cannot be keyed."""
    try:
        return _known.setdefault(phab, {})
    except TypeError:
        return {}


def _endpoint(phab, method):
    """``"project.search"`` as ``phab.project.search``."""
    endpoint = phab
    for part in method.split("."):
        endpoint = getattr(endpoint, part)
    return endpoint


def rejects_constraint(error, key):
    """Whether `error` is the server saying it has no constraint `key`.

    Parameters
    ----------
    error : Exception
        What a ``*.search`` call raised
    key : str
        The constraint it was sent

    Returns
    -------
    bool
    """
    return isinstance(error, PhabfiveAPIException) and (
        f'Constraint "{key}" is not a valid constraint' in str(error.message)
    )


def known_constraint(phab, method, key):
    """What is known about `method` taking the constraint `key`.

    Returns
    -------
    bool or None
        None when it was never asked
    """
    return _memo(phab).get((method, key))


def remember_constraint(phab, method, key, supported):
    """Record whether `method` takes the constraint `key`."""
    memo = _memo(phab)

    if memo.get((method, key)) != supported:
        log.debug(f"{method} {'takes' if supported else 'has no'} constraint '{key}'")

    memo[(method, key)] = supported


def supports_constraint(phab, method, key, value):
    """Whether the ``*.search`` method `method` takes the constraint `key`.

    Asked once per client, with a one-row search constrained on `key` alone.

    Parameters
    ----------
    phab : Conduit
        The client
    method : str
        e.g. ``"project.search"``
    key : str
        The constraint, e.g. ``"status"``
    value
        A value the constraint takes where it exists, since a server that
        has it may refuse a value it does not know

    Returns
    -------
    bool

    Raises
    ------
    PhabfiveRemoteException
        If the probe failed for any other reason, which says nothing either
        way and is not remembered
    """
    known = known_constraint(phab, method, key)
    if known is not None:
        return known

    try:
        _endpoint(phab, method)(constraints={key: value}, limit=1)
    except PhabfiveAPIException as e:
        if not rejects_constraint(e, key):
            raise
        supported = False
    else:
        supported = True

    remember_constraint(phab, method, key, supported)

    return supported


def supports_method(phab, method):
    """Whether the server has the Conduit method `method`.

    One `conduit.query` per client answers every method.

    Parameters
    ----------
    phab : Conduit
        The client
    method : str
        e.g. ``"conpherence.search"``

    Returns
    -------
    bool
    """
    memo = _memo(phab)

    # conduit.query lists itself, so its entry is what says the list was read
    if ("conduit.query", None) not in memo:
        for name in phab.conduit.query() or {}:
            memo[(name, None)] = True
        memo[("conduit.query", None)] = True

    return memo.get((method, None), False)


__all__ = [
    "known_constraint",
    "rejects_constraint",
    "remember_constraint",
    "supports_constraint",
    "supports_method",
]
