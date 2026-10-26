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


def board_order(columns, proxies=None):
    """Workboard columns in the order the board shows them.

    project.column.search answers newest first. The board shows its own
    columns and its subprojects' by sequence, then its milestones' after
    them all, by milestone number (PhabricatorProjectColumn::getOrderingKey).
    A milestone's column is created the first time the board is drawn after
    the milestone is, so a column added to the board later has a higher
    sequence than the milestones and still comes before them.

    Phabricator and Phorge before 2023.32 report no sequence. Their own
    columns are then in the order they were created, which is the board's
    until someone reorders it, and the milestones still go last.

    Parameters
    ----------
    columns : list
        project.column.search result items
    proxies : dict, optional
        Proxy PHID to its project.search item. A proxy missing from it - one
        the viewer may not see - is ordered as a column of the board's own.

    Returns
    -------
    list
        The same items, in board order
    """
    proxies = proxies or {}

    def key(column):
        fields = column.get("fields", {})
        proxy = proxies.get(fields.get("proxyPHID")) or {}
        milestone = (proxy.get("fields") or {}).get("milestone")

        if milestone is not None:
            return (1, milestone, column.get("id") or 0)

        # Without a sequence every column ties, and the id decides
        return (0, fields.get("sequence") or 0, column.get("id") or 0)

    return sorted(columns, key=key)


def fetch_boards_columns(phab, board_phids):
    """The workboard columns of several projects, in board order, with their proxies.

    Every column, the hidden ones included: what to leave out is the
    caller's to decide. One project.column.search for all the boards,
    however many there are.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    board_phids : list
        The projects' PHIDs

    Returns
    -------
    tuple
        ``(columns, proxies)``: board PHID to its project.column.search
        result items in board order, without the projects that have no
        workboard, and proxy PHID to the project.search item of the
        milestone or subproject each column stands for. One more request,
        and only when some board has such a column.

    Raises
    ------
    PhabfiveDataException
        If either lookup fails. An empty board would read as a project with
        no workboard, which is a different answer.
    """
    board_phids = list(dict.fromkeys(board_phids))
    if not board_phids:
        return {}, {}

    try:
        found_columns = search_all_pages(
            phab.project.column.search, constraints={"projects": board_phids}
        )
    except Exception as e:
        raise PhabfiveDataException(f"Failed to fetch workboard columns: {e}")

    proxy_phids = sorted(
        {
            phid
            for column in found_columns
            if (phid := column.get("fields", {}).get("proxyPHID"))
        }
    )
    proxies = {}

    if proxy_phids:
        # Every status: an archived milestone's column is still on the
        # board, hidden
        found = fetch_projects(
            phab, constraints={"phids": proxy_phids, "status": PROJECT_STATUS_ALL}
        )
        proxies = {project["phid"]: project for project in found}

    # Asked for one board, every column is that board's
    only = board_phids[0] if len(board_phids) == 1 else None
    columns = {}
    for column in found_columns:
        board = (column.get("fields", {}).get("project") or {}).get("phid") or only
        columns.setdefault(board, []).append(column)

    return {
        board: board_order(listed, proxies) for board, listed in columns.items()
    }, proxies


def fetch_board_columns(phab, board_phid):
    """One project's workboard columns, in board order, with their proxies.

    See fetch_boards_columns.

    Returns
    -------
    tuple
        ``(columns, proxies)``: project.column.search result items in board
        order, empty when the project has no workboard, and proxy PHID to
        the project.search item each column stands for

    Raises
    ------
    PhabfiveDataException
        If either lookup fails
    """
    columns, proxies = fetch_boards_columns(phab, [board_phid])

    return columns.get(board_phid, []), proxies
