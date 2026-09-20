# -*- coding: utf-8 -*-

"""API data fetching functions for Diffusion module."""

from phabricator import APIError

from phabfive.diffusion.validators import validate_repo_identifier
from phabfive.exceptions import PhabfiveDataException


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
    query_key = query_key or "all"
    attachments = attachments or {}
    constraints = constraints or {}

    repos = []
    after = None

    while True:
        kwargs = {
            "queryKey": query_key,
            "attachments": attachments,
            "constraints": constraints,
        }

        if after is not None:
            kwargs["after"] = after

        response = phab.diffusion.repository.search(**kwargs)
        repos.extend(response.get("data") or [])

        after = (response.get("cursor") or {}).get("after")

        if not after:
            return repos


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
        except APIError as e:
            raise PhabfiveDataException(e)
    elif repo_callsign:
        # TODO: probably catch APIError here as well
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
    except APIError as e:
        raise PhabfiveDataException(e)
