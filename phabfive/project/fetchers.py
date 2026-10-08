# -*- coding: utf-8 -*-

"""API data fetching for the project app."""

import logging

from phabfive.capabilities import (
    known_constraint,
    rejects_constraint,
    remember_constraint,
)
from phabfive.constants import (
    PROJECT_STATUS_ACTIVE,
    PROJECT_STATUS_ALL,
    PROJECT_STATUS_ARCHIVED,
)
from phabfive.exceptions import PhabfiveAPIException, PhabfiveDataException
from phabfive.pagination import (
    MAX_PAGE_SIZE,
    iter_pages,
    page_records,
    search_all_pages,
)

log = logging.getLogger(__name__)


def fill_project_status(phab, projects):
    """Set ``fields.status`` on the projects that lack it, in place.

    Phabricator's project.search reports no status. Its project.query does
    take one, so the projects among these that it calls archived are
    archived, and the rest are active. Phorge reports the status on every
    record, and is asked nothing.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    projects : list
        project.search result items

    Returns
    -------
    list
        The same projects
    """
    missing = [
        project
        for project in projects
        if "status" not in project.setdefault("fields", {})
    ]

    if not missing:
        return projects

    archived = set()
    phids = [project["phid"] for project in missing]

    for start in range(0, len(phids), MAX_PAGE_SIZE):
        chunk = phids[start : start + MAX_PAGE_SIZE]
        found = phab.project.query(phids=chunk, status="status-archived")
        archived.update(
            record.get("phid") for record in page_records((found or {}).get("data"))
        )

    for project in missing:
        project["fields"]["status"] = (
            PROJECT_STATUS_ARCHIVED
            if project["phid"] in archived
            else PROJECT_STATUS_ACTIVE
        )

    return projects


def search_projects(phab, constraints=None, limit=None, **kwargs):
    """project.search across every page, on a server with or without `status`.

    Phorge before 2025.51, and Phabricator, have no ``status`` constraint
    and answer one with ``Constraint "status" is not a valid constraint``.
    The search is sent as asked all the same, so a server that has it is
    asked exactly what it always was; only when it is refused is that
    remembered for the client, and the status filtered here instead: "all"
    by not sending it, any other by `fill_project_status` page by page.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    constraints : dict, optional
        project.search constraints
    limit : int, optional
        How many projects to return in total; None for all of them
    **kwargs
        Passed to project.search, e.g. attachments, order

    Returns
    -------
    list
        project.search result items, in the order the API returned them

    Raises
    ------
    PhabfiveRemoteException
        If the search fails for any other reason
    """
    constraints = dict(constraints or {})
    status = constraints.get("status")

    if (
        status is None
        or known_constraint(phab, "project.search", "status") is not False
    ):
        try:
            found = search_all_pages(
                phab.project.search, limit=limit, constraints=constraints, **kwargs
            )
        except PhabfiveAPIException as e:
            if status is None or not rejects_constraint(e, "status"):
                raise
            remember_constraint(phab, "project.search", "status", False)
        else:
            if status is not None:
                remember_constraint(phab, "project.search", "status", True)
            return found

    del constraints["status"]

    if status == PROJECT_STATUS_ALL:
        return search_all_pages(
            phab.project.search, limit=limit, constraints=constraints, **kwargs
        )

    found = []

    # Every page, not `limit` of them: only the filter here can count matches
    for page in iter_pages(phab.project.search, constraints=constraints, **kwargs):
        fill_project_status(phab, page)
        found.extend(
            project for project in page if project["fields"]["status"] == status
        )

        if limit is not None and len(found) >= limit:
            return found[:limit]

    return found


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
    kwargs = {}

    if attachments:
        kwargs["attachments"] = attachments

    if order:
        kwargs["order"] = order

    try:
        return search_projects(phab, constraints, limit=limit, **kwargs)
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
