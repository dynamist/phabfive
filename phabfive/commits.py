# -*- coding: utf-8 -*-
"""Resolve what somebody typed for a commit to the commit it names.

``--attach`` and ``--detach`` on ``maniphest edit``, ``--attach`` on
``maniphest create`` and ``commits:`` in a create spec take the spellings
Diffusion itself accepts, because they all go through
``diffusion.commit.search``:

=========================  ================================================
``rGUNNAR7d7fc2c3e002``    callsign and hash
``R1:7d7fc2c3e002``        repository ID and hash
``7d7fc2c3e002``           a bare hash, looked for in every repository
``PHID-CMIT-...``          a commit PHID, asked about so a typo is an error
=========================  ================================================

A bare hash is the one that can name more than one commit - a fork, or an
observed mirror of a hosted repository - and that is an error listing the
candidates rather than a guess.
"""

# python std lib
import re

# phabfive imports
from phabfive.exceptions import PhabfiveInputException, PhabfiveNotFoundException

COMMIT_PHID_PREFIX = "PHID-CMIT-"

#: The spellings, as an option's help text names them
COMMIT_GRAMMAR = "rCALLSIGN<hash>, R1:<hash>, a bare hash or a commit PHID"

# Shorter than this, the instance answers a hash with nothing at all (probed
# on Phorge: 7 characters resolve, 6 do not), so the error says why.
MIN_HASH_LENGTH = 7
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def _query_handles(phab, phids):
    """The handles of commit PHIDs, keyed by PHID, leaving out non-commits."""
    if not phids:
        return {}

    result = phab.phid.query(phids=sorted(set(phids))) or {}

    return {
        phid: handle
        for phid, handle in result.items()
        if (handle or {}).get("type") == "CMIT"
    }


def lookup_commits(phab, values):
    """Every commit each value could name, without judging the answer.

    `resolve_commit_phids` is the strict form, and what the commands use.
    This is for a caller that reports each value on its own rather than
    raising at the first - the spec validator, which lists every problem in
    a file at once.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    values : list
        Commit monograms (``rXYZ<hash>``, ``R1:<hash>``), bare hashes, or
        commit PHIDs

    Returns
    -------
    dict
        Each distinct value to a list of (PHID, name), in the order given:
        empty when it names no commit, longer than one when a bare hash is
        in several repositories
    """
    phids = {}

    for value in values:
        if value in phids:
            continue

        if value.startswith(COMMIT_PHID_PREFIX):
            phids[value] = [value]
            continue

        # One search per value: a result does not say which identifier it
        # matched, so several values in one call could not be told apart
        result = phab.diffusion.commit.search(constraints={"identifiers": [value]})
        phids[value] = [commit["phid"] for commit in (result or {}).get("data", [])]

    handles = _query_handles(phab, [phid for found in phids.values() for phid in found])

    return {
        value: [(phid, handles[phid]["name"]) for phid in found if phid in handles]
        for value, found in phids.items()
    }


def is_too_short(value):
    """Whether a value is a bare hash shorter than the instance will match."""
    return bool(_HEX_RE.match(value)) and len(value) < MIN_HASH_LENGTH


def resolve_commit_phids(phab, values, option=None):
    """Resolve commits to PHIDs and the names Diffusion displays them by.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    values : list
        Commit monograms (``rXYZ<hash>``, ``R1:<hash>``), bare hashes, or
        commit PHIDs
    option : str, optional
        The option the values came from, named in any error

    Returns
    -------
    dict
        What was typed to (PHID, name), in the order given. The name is the
        canonical monogram, e.g. ``rGUNNAR7d7fc2c3e002``, whichever spelling
        was typed

    Raises
    ------
    PhabfiveNotFoundException
        If any of them is not a commit, naming every one that is not
    PhabfiveInputException
        If a value matches more than one commit, naming the candidates
    """
    found = lookup_commits(phab, values)
    missing = [value for value, commits in found.items() if not commits]
    ambiguous = {
        value: sorted(name for _, name in commits)
        for value, commits in found.items()
        if len(commits) > 1
    }

    where = f" for {option}" if option else ""

    if missing:
        listed = ", ".join(f"'{value}'" for value in missing)
        hint = ""
        if any(is_too_short(value) for value in missing):
            hint = f" (a hash needs at least {MIN_HASH_LENGTH} characters)"
        raise PhabfiveNotFoundException(f"No such commit{where}: {listed}{hint}")

    if ambiguous:
        listed = "; ".join(
            f"'{value}' matches {', '.join(names)}"
            for value, names in ambiguous.items()
        )
        raise PhabfiveInputException(
            f"Ambiguous commit{where}: {listed}. Name the repository, "
            "e.g. rCALLSIGN<hash> or R1:<hash>"
        )

    return {value: found[value][0] for value in values}


def fetch_commit_handles(phab, phids):
    """Describe commits for display, in the order given.

    A PHID the viewer cannot see is left out, as a related task the viewer
    cannot see is.

    Returns
    -------
    list
        ``{"Link": uri, "Commit": {"Identifier": name, "Summary": summary}}``
        per commit
    """
    handles = _query_handles(phab, phids)
    commits = []

    for phid in phids:
        handle = handles.get(phid)
        if not handle:
            continue

        name = handle.get("name", "")
        summary = handle.get("fullName", "")
        if summary.startswith(f"{name}: "):
            summary = summary[len(name) + 2 :]
        elif summary == name:
            summary = ""

        commits.append(
            {
                "Link": handle.get("uri", ""),
                "Commit": {"Identifier": name, "Summary": summary},
            }
        )

    return commits


__all__ = [
    "COMMIT_GRAMMAR",
    "COMMIT_PHID_PREFIX",
    "MIN_HASH_LENGTH",
    "fetch_commit_handles",
    "is_too_short",
    "lookup_commits",
    "resolve_commit_phids",
]
