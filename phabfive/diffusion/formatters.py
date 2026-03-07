# -*- coding: utf-8 -*-

"""Data formatting functions for Diffusion module."""

from phabfive.constants import REPO_STATUS_CHOICES
from phabfive.diffusion.fetchers import fetch_branches, fetch_repositories, fetch_uris
from phabfive.diffusion.validators import validate_repo_identifier
from phabfive.exceptions import PhabfiveDataException


def format_uris(phab, repo, clone_uri=False):
    """
    Return list of URI strings for a repository.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo : str
        Repository ID (e.g., "R123") or short name
    clone_uri : bool, optional
        If True, only return clone URIs

    Returns
    -------
    list
        List of URI strings
    """
    if validate_repo_identifier(repo):
        repo = repo.replace("R", "")
    return fetch_uris(phab, repo_id=repo, clone_uri=clone_uri)


def format_repositories(phab, status=None, include_url=False):
    """
    Return list of repository dicts with 'name' and optionally 'urls' keys.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    status : list, optional
        Status filter, defaults to REPO_STATUS_CHOICES
    include_url : bool, optional
        If True, include URLs in output

    Returns
    -------
    list
        List of dicts with 'name' and optionally 'urls' keys

    Raises
    ------
    PhabfiveDataException
        If no data returned
    """
    status = status or REPO_STATUS_CHOICES

    repos = fetch_repositories(phab, attachments={"uris": include_url})

    if not repos:
        raise PhabfiveDataException("No data or other error")

    # filter based on active or inactive status
    repos = [repo for repo in repos if repo["fields"]["status"] in status]

    # sort based on name
    repos = sorted(
        repos,
        key=lambda key: key["fields"]["name"],
    )

    result = []
    for repo in repos:
        entry = {"name": repo["fields"].get("name", "")}
        if include_url:
            uris = repo["attachments"]["uris"]["uris"]
            entry["urls"] = [
                uri["fields"]["uri"]["effective"]
                for uri in uris
                if uri["fields"]["display"]["effective"] == "always"
            ]
        result.append(entry)

    return result


def format_branches(phab, repo):
    """
    Return sorted list of branch names for a repository.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo : str
        Repository ID (e.g., "R123") or short name

    Returns
    -------
    list
        Sorted list of branch name strings
    """
    if validate_repo_identifier(repo):
        repo = repo.replace("R", "")
        branches = fetch_branches(phab, repo_id=repo)
    else:
        branches = fetch_branches(phab, repo_shortname=repo)

    return sorted(
        branch["shortName"] for branch in branches if branch["refType"] == "branch"
    )
