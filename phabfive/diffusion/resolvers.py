# -*- coding: utf-8 -*-

"""PHID and identifier resolution functions for Diffusion module."""

from phabfive.exceptions import PhabfiveDataException


def resolve_shortname_to_id(phab, shortname):
    """
    Resolve repository shortname to its numeric ID.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    shortname : str
        Repository short name

    Returns
    -------
    int or None
        Repository ID if found, None otherwise
    """
    response = phab.diffusion.repository.search(
        queryKey="all",
        attachments={},
        constraints={},
    )
    repos = response.get("data", {})

    repo_ids = [
        repo["id"] for repo in repos if repo["fields"]["shortName"] == shortname
    ]

    return repo_ids[0] if repo_ids else None


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
        Repository short name
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
    response = phab.diffusion.repository.search(
        queryKey="all",
        attachments={"uris": True},
        constraints={},
    )

    for repo in response.get("data", {}):
        if repo["fields"]["shortName"] != repo_name:
            continue

        for uri in repo["attachments"]["uris"]["uris"]:
            if uri["fields"]["uri"]["display"] == uri_name:
                return uri

        raise PhabfiveDataException("Uri does not exist or other error")

    raise PhabfiveDataException(f"Repository '{repo_name}' does not exist")


def resolve_object_identifier(phab, repo_name=None, uri_name=None):
    """
    Identify repository or URI object identifier.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo_name : str, optional
        Repository name to look up
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
    object_identifier = ""
    response = phab.diffusion.repository.search(
        queryKey="all",
        attachments={"uris": True},
        constraints={},
    )
    repos = response.get("data", {})

    for repo in repos:
        name = repo["fields"]["shortName"]

        if repo_name != name:
            continue

        # object identifier for uri
        if repo_name and uri_name:
            uris = repo["attachments"]["uris"]["uris"]

            for i in range(len(uris)):
                uri = uris[i]["fields"]["uri"]["display"]

                if uri_name != uri:
                    continue

                if uris[i]["id"]:
                    object_identifier = uris[i]["id"]

            if object_identifier == "":
                raise PhabfiveDataException("Uri does not exist or other error")

            break
        # object identifier for repository
        elif repo_name and not uri_name:
            object_identifier = repo["id"]
            break

    return object_identifier
