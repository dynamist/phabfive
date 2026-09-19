# -*- coding: utf-8 -*-

"""PHID and identifier resolution functions for Diffusion module."""

from phabfive.diffusion.fetchers import fetch_repositories, match_repository
from phabfive.exceptions import PhabfiveDataException


def resolve_shortname_to_id(phab, shortname):
    """
    Resolve repository shortname to its numeric ID.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    shortname : str
        Repository monogram, callsign or short name

    Returns
    -------
    int or None
        Repository ID if found, None otherwise
    """
    repo = match_repository(fetch_repositories(phab), shortname)

    return repo["id"] if repo else None


def resolve_uri_record(phab, repo_name, uri_name):
    """
    Fetch a repository URI in full, so an edit can be shown before it is made.

    resolve_object_identifier already reads this record but keeps only the id.
    Editing needs the rest of it to say what each value is changing from.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo_name : str
        Repository monogram, callsign or short name
    uri_name : str
        URI as displayed

    Returns
    -------
    dict
        The URI object, with 'id', 'phid' and 'fields' keys

    Raises
    ------
    PhabfiveDataException
        If the repository or URI does not exist
    """
    repos = fetch_repositories(phab, attachments={"uris": True})
    repo = match_repository(repos, repo_name)

    if repo is None:
        raise PhabfiveDataException(f"Repository '{repo_name}' does not exist")

    for uri in repo["attachments"]["uris"]["uris"]:
        if uri["fields"]["uri"]["display"] == uri_name:
            return uri

    raise PhabfiveDataException("Uri does not exist or other error")


def resolve_object_identifier(phab, repo_name=None, uri_name=None):
    """
    Identify repository or URI object identifier.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo_name : str, optional
        Repository monogram, callsign or short name to look up
    uri_name : str, optional
        URI name to look up (requires repo_name)

    Returns
    -------
    str
        Object identifier for the repository or URI

    Raises
    ------
    PhabfiveDataException
        If URI does not exist
    """
    if not repo_name:
        return ""

    repos = fetch_repositories(phab, attachments={"uris": True})
    repo = match_repository(repos, repo_name)

    if repo is None:
        return ""

    if not uri_name:
        return repo["id"]

    for uri in repo["attachments"]["uris"]["uris"]:
        if uri["fields"]["uri"]["display"] == uri_name and uri["id"]:
            return uri["id"]

    raise PhabfiveDataException("Uri does not exist or other error")
