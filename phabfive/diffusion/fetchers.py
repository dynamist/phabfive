# -*- coding: utf-8 -*-

"""API data fetching functions for Diffusion module."""

import logging

from phabfive.diffusion.validators import validate_repo_identifier
from phabfive.exceptions import PhabfiveAPIException, PhabfiveDataException
from phabfive.pagination import search_all_pages

log = logging.getLogger(__name__)


def fetch_repositories(phab, query_key=None, attachments=None, constraints=None):
    """
    Fetch repository data from Phabricator API.

    Follows the result cursor to the end, so callers see every repository
    rather than the first page. Conduit returns 100 rows per page, and an
    instance past that limit would otherwise hide repositories from every
    lookup that goes through here.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    query_key : str, optional
        Query key, defaults to "all"
    attachments : dict, optional
        Attachments to include
    constraints : dict, optional
        Search constraints

    Returns
    -------
    list
        List of repository data dicts
    """
    return search_all_pages(
        phab.diffusion.repository.search,
        queryKey=query_key or "all",
        attachments=attachments or {},
        constraints=constraints or {},
    )


def match_repository(repos, repo_id):
    """
    Pick the repository that an identifier names.

    Accepts an "R123" monogram, a callsign, or a short name, tried in that
    order. A repository with no short name set is only reachable by the first
    two, which is why the short name is not the only thing compared.

    Parameters
    ----------
    repos : list
        Repository data dicts, as returned by fetch_repositories
    repo_id : str
        Repository monogram, callsign or short name

    Returns
    -------
    dict or None
        The matching repository, or None if nothing matches
    """
    if validate_repo_identifier(repo_id):
        wanted = int(repo_id[1:])

        for repo in repos:
            if repo["id"] == wanted:
                return repo

    for repo in repos:
        if repo["fields"].get("callsign") == repo_id:
            return repo

    for repo in repos:
        if repo["fields"].get("shortName") == repo_id:
            return repo

    return None


def find_repository(phab, repo_id, attachments=None):
    """
    Fetch repositories and return the one an identifier names.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo_id : str
        Repository monogram, callsign or short name
    attachments : dict, optional
        Attachments to include

    Returns
    -------
    dict or None
        The matching repository, or None if nothing matches
    """
    return match_repository(fetch_repositories(phab, attachments=attachments), repo_id)


#: How many repositories one repository.query asks about. It answers 100
#: rows unless told otherwise, so a page of PHIDs is never cut short.
_HOSTING_BATCH = 100


def fill_hosting(phab, repos):
    """
    Add ``isHosted`` to repository records whose instance left it out.

    Phabricator, and Phorge before 2025.51, have no ``isHosted`` in
    ``diffusion.repository.search``. The frozen ``repository.query``
    reports the same stored flag on those versions, so that is asked,
    by PHID, and only for the records that lack it - an instance that
    reports the field pays nothing.

    Nothing else is a reliable stand-in. Phabricator derives the flag from
    whether the repository observes a remote, but a repository from before
    its URI migration can be neither hosted nor observing, and a hosted
    one can answer with no URIs at all. So when ``repository.query`` is not
    there to ask, the field stays missing, which
    :func:`phabfive.diffusion.formatters.repository_is_hosted` reads as
    unknown rather than as not hosted.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repos : list
        Repository records, from fetch_repositories. Updated in place.

    Returns
    -------
    list
        The same records
    """
    lacking = [repo for repo in repos if "isHosted" not in repo.get("fields", {})]
    hosted = {}

    for start in range(0, len(lacking), _HOSTING_BATCH):
        phids = [repo["phid"] for repo in lacking[start : start + _HOSTING_BATCH]]

        try:
            answered = phab.repository.query(phids=phids)
        except PhabfiveAPIException as e:
            log.debug(f"repository.query did not answer, hosting is unknown: {e}")
            return repos

        hosted.update({row["phid"]: row["isHosted"] for row in answered or []})

    for repo in lacking:
        if repo["phid"] in hosted:
            repo["fields"]["isHosted"] = bool(hosted[repo["phid"]])

    return repos


def demotion_io(uri_fields):
    """The io value that takes a URI out of service, for this kind of URI.

    Phabricator validates io per URI: a hosted (built-in) URI accepts
    "read", while an observed or mirrored one accepts only "default",
    "none", "observe" and "mirror". Sending "read" to the latter is
    rejected outright, which is what made `uri create` fail on any
    repository that observes a remote.

    Parameters
    ----------
    uri_fields : dict
        The "fields" of a URI record

    Returns
    -------
    str
        "read" for a built-in URI, "none" otherwise
    """
    builtin = uri_fields.get("builtin") or {}

    return "read" if builtin.get("protocol") else "none"


def fetch_branches(phab, repo_id=None, repo_callsign=None, repo_shortname=None):
    """
    Fetch branches for a repository from Phabricator API.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo_id : str, optional
        Repository ID
    repo_callsign : str, optional
        Repository callsign
    repo_shortname : str, optional
        Repository short name

    Returns
    -------
    list
        List of branch data

    Raises
    ------
    PhabfiveDataException
        If repository is invalid or API error occurs
    """
    if repo_id:
        try:
            return phab.diffusion.branchquery(repository=repo_id)
        except PhabfiveAPIException as e:
            raise PhabfiveDataException(e)
    elif repo_callsign:
        # TODO: probably catch PhabfiveAPIException here as well
        return phab.diffusion.branchquery(callsign=repo_callsign)
    else:
        resolved = find_repository(phab, repo_shortname)

        if resolved:
            return phab.diffusion.branchquery(repository=resolved["id"])
        else:
            raise PhabfiveDataException(
                f"Repository '{repo_shortname}' is not a valid repository"
            )


def fetch_refs(phab, repo_id, ref_type="branch"):
    """
    Fetch the branches or the tags of a repository, by numeric id.

    Branches and tags live behind two endpoints that take the same
    ``repository`` argument and answer with two different record shapes;
    :func:`phabfive.diffusion.formatters.ref_names` is what reads either.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo_id : str or int
        Numeric repository ID, without the "R"
    ref_type : str, optional
        Either "branch" (the default) or "tag"

    Returns
    -------
    list
        The raw ref records the endpoint returned

    Raises
    ------
    PhabfiveDataException
        If the API refuses, which it does for a repository whose data it
        cannot reach even though the repository itself exists
    """
    query = (
        phab.diffusion.tagsquery if ref_type == "tag" else phab.diffusion.branchquery
    )

    try:
        return query(repository=repo_id)
    except PhabfiveAPIException as e:
        raise PhabfiveDataException(e)
