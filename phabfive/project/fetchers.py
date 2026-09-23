# -*- coding: utf-8 -*-

"""API data fetching for the project app."""

import logging

from phabfive.exceptions import PhabfiveDataException
from phabfive.pagination import MAX_PAGE_SIZE, search_all_pages

log = logging.getLogger(__name__)


def fetch_projects(phab, constraints=None, attachments=None, limit=None, order=None):
    """Every project a search matches, across as many pages as that takes.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    constraints : dict, optional
        project.search constraints
    attachments : dict, optional
        project.search attachments, e.g. {"members": True}
    limit : int, optional
        How many projects to return in total. None means all of them.
    order : str or list, optional
        A builtin project.search order name, or a column vector. Cursor
        paging respects it, so the pages stay in order as they are
        concatenated - which is what lets a limit be forwarded at all.

    Returns
    -------
    list
        project.search result items, in the order the API returned them

    Raises
    ------
    PhabfiveDataException
        If the search fails. An empty answer is a real answer and is
        returned; a failed one must not look like it.
    """
    kwargs = {"constraints": constraints or {}}

    if attachments:
        kwargs["attachments"] = attachments

    if order:
        kwargs["order"] = order

    try:
        return search_all_pages(phab.project.search, limit=limit, **kwargs)
    except Exception as e:
        raise PhabfiveDataException(f"Failed to search projects: {e}")


def fetch_users_by_phid(phab, phids):
    """The users a set of PHIDs name, keyed by PHID.

    Asked a page at a time, so a project with hundreds of members costs a
    few round trips rather than one oversized request.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    phids : iterable
        User PHIDs

    Returns
    -------
    dict
        PHID to user.search result item. A PHID the viewer may not see is
        missing, and the caller shows it as the PHID.

    Raises
    ------
    PhabfiveDataException
        If the lookup fails. Members are what `--show-members` was asked
        for, so a failed lookup is an error rather than a list of PHIDs that
        reads as though nobody could be named.
    """
    phids = sorted(set(phids))
    users = {}

    for start in range(0, len(phids), MAX_PAGE_SIZE):
        chunk = phids[start : start + MAX_PAGE_SIZE]

        try:
            found = search_all_pages(phab.user.search, constraints={"phids": chunk})
        except Exception as e:
            raise PhabfiveDataException(f"Failed to look up project members: {e}")

        users.update({user["phid"]: user for user in found})

    return users


def name_spaces(phab, phids):
    """Name the Spaces a set of projects live in.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    phids : iterable
        Space PHIDs; None entries are ignored

    Returns
    -------
    dict
        Space PHID to its name, e.g. "S1 Default". Empty when nothing is in a
        Space, which is every instance that never turned Spaces on, and when
        the lookup fails: naming a Space is a convenience, and a read must
        not fail over it.
    """
    phids = sorted({phid for phid in phids if phid})

    if not phids:
        return {}

    try:
        found = phab.phid.query(phids=phids)

        return {
            phid: data.get("fullName") or data.get("name") or phid
            for phid, data in found.items()
            if hasattr(data, "get")
        }
    except Exception as e:
        log.warning(f"Failed to resolve space PHIDs: {e}")
        return {}
