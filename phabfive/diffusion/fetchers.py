# -*- coding: utf-8 -*-

"""API data fetching functions for Diffusion module."""

from phabricator import APIError

from phabfive.diffusion.resolvers import resolve_shortname_to_id
from phabfive.exceptions import PhabfiveDataException


def fetch_repositories(phab, query_key=None, attachments=None, constraints=None):
    """
    Fetch repository data from Phabricator API.

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

    response = phab.diffusion.repository.search(
        queryKey=query_key,
        attachments=attachments,
        constraints=constraints,
    )

    return response.get("data", {})


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
        resolved = resolve_shortname_to_id(phab, repo_shortname)

        if resolved:
            return phab.diffusion.branchquery(repository=resolved)
        else:
            raise PhabfiveDataException(
                f"Repository '{repo_shortname}' is not a valid repository"
            )


def fetch_uris(phab, repo_id=None, clone_uri=False):
    """
    Fetch URIs for a repository from Phabricator API.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo_id : str
        Repository ID or short name
    clone_uri : bool, optional
        If True, only return clone URIs (display=always)

    Returns
    -------
    list
        List of URI strings

    Raises
    ------
    PhabfiveDataException
        If no data returned
    """
    uris = []
    repos = fetch_repositories(phab, attachments={"uris": True})

    if not repos:
        raise PhabfiveDataException("No data or other error.")

    for repo in repos:
        if repo_id == repo["fields"]["shortName"]:
            repo_uris = repo["attachments"]["uris"]["uris"]

            if clone_uri:
                for uri in repo_uris:
                    if "always" not in uri["fields"]["display"]["effective"]:
                        continue
                    uris.append(uri["fields"]["uri"]["display"])
            else:
                for uri in repo_uris:
                    uris.append(uri["fields"]["uri"]["display"])

    return uris
