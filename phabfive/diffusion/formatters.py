# -*- coding: utf-8 -*-

"""Data formatting functions for Diffusion module."""

from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.constants import POLICY_LABELS, REPO_STATUS_CHOICES
from phabfive.diffusion.fetchers import (
    fetch_refs,
    fetch_repositories,
    fetch_uris,
    find_repository,
)
from phabfive.diffusion.validators import validate_repo_identifier
from phabfive.exceptions import PhabfiveDataException
from phabfive.maniphest.utils import format_timestamp


def format_uris(phab, repo, clone_uri=False):
    """
    Return list of URI strings for a repository.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo : str
        Repository monogram (e.g., "R123"), callsign or short name
    clone_uri : bool, optional
        If True, only return clone URIs

    Returns
    -------
    list
        List of URI strings
    """
    # Passed whole: fetch_uris matches the monogram itself, and stripping the
    # "R" here would leave it comparing a bare number against short names.
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
    """
    status = status or REPO_STATUS_CHOICES

    repos = fetch_repositories(phab, attachments={"uris": include_url})

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


def ref_names(refs, ref_type="branch"):
    """
    Return the sorted names of the refs of one kind.

    The two endpoints do not agree on how a ref is spelled: ``branchquery``
    answers with a "shortName" and says what the ref is in "refType", while
    ``tagsquery`` answers with a "name" and says nothing, having been asked
    for tags alone. A record with no "refType" is therefore taken at the
    endpoint's word rather than filtered away.

    Parameters
    ----------
    refs : list
        Raw ref records, as returned by fetch_refs
    ref_type : str, optional
        Either "branch" (the default) or "tag"

    Returns
    -------
    list
        Sorted ref name strings
    """
    names = []

    for ref in refs:
        if ref.get("refType", ref_type) != ref_type:
            continue

        name = ref.get("shortName") or ref.get("name")

        if name:
            names.append(name)

    return sorted(names)


def format_refs(phab, repo, ref_type="branch"):
    """
    Return the sorted names of a repository's branches, or of its tags.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    repo : str
        Repository monogram (e.g., "R123"), callsign or short name
    ref_type : str, optional
        Either "branch" (the default) or "tag"

    Returns
    -------
    list
        Sorted ref name strings

    Raises
    ------
    PhabfiveDataException
        If no such repository exists, or the API refuses the query
    """
    if validate_repo_identifier(repo):
        repo_id = repo[1:]
    else:
        resolved = find_repository(phab, repo)

        if resolved is None:
            raise PhabfiveDataException(
                f"Repository '{repo}' is not a valid repository"
            )

        repo_id = resolved["id"]

    return ref_names(fetch_refs(phab, repo_id, ref_type), ref_type)


def repository_is_hosted(repo):
    """
    Whether Phabricator serves this repository itself.

    Phorge reports this as ``isHosted`` and that is what is read. The
    fallback is for an instance that does not: hosting is what a read-write
    URI means, a repository that follows a remote carrying an observed URI
    and read-only built-ins instead. It is a fallback rather than the rule
    because the URIs attachment can come back empty on a repository that
    is hosted - a freshly seeded one does - and an empty list must not be
    read as "not hosted" when the instance has already said otherwise.

    Parameters
    ----------
    repo : dict
        A repository record, fetched with attachments={"uris": True}

    Returns
    -------
    bool
        True if Phabricator hosts the repository
    """
    fields = repo.get("fields", {})

    if "isHosted" in fields:
        return bool(fields["isHosted"])

    uris = repo.get("attachments", {}).get("uris", {}).get("uris", [])

    return any(
        uri.get("fields", {}).get("io", {}).get("effective") == "readwrite"
        for uri in uris
    )


def format_policy(policy):
    """
    Label a repository's policies the way the Phorge web UI labels them.

    Parameters
    ----------
    policy : dict
        The "policy" field of a repository record

    Returns
    -------
    dict
        {"View": ..., "Edit": ..., "Push": ...}, each a web-UI label for a
        keyword constant, or the raw value - a PHID, typically - for
        anything else. Resolving a PHID to the project or rule behind it is
        its own piece of work.
    """
    policy = policy or {}

    def label(value):
        if value is None:
            return "(none)"
        return POLICY_LABELS.get(value, value)

    return {
        "View": label(policy.get("view")),
        "Edit": label(policy.get("edit")),
        "Push": label(policy.get("diffusion.push")),
    }


def format_repository_uris(repo):
    """
    Describe a repository's URIs the way the web UI's URI table does.

    Parameters
    ----------
    repo : dict
        A repository record, fetched with attachments={"uris": True}

    Returns
    -------
    list
        One dict per URI, with "URI", "I/O", "Display" and "Disabled" keys
    """
    uris = repo.get("attachments", {}).get("uris", {}).get("uris", [])

    return [
        {
            "URI": uri["fields"]["uri"]["display"],
            "I/O": uri["fields"]["io"]["effective"],
            "Display": uri["fields"]["display"]["effective"],
            "Disabled": bool(uri["fields"].get("disabled")),
        }
        for uri in uris
    ]


def build_repository_display_data(
    url,
    format_link_func,
    repos,
    branches_map=None,
    tags_map=None,
    space_map=None,
    show_uris=False,
    show_branches=False,
    show_tags=False,
    show_metadata=False,
    show_description=True,
):
    """
    Build the display record for each repository.

    One record per repository, nested and capitalized the way maniphest's
    is, with Link first so that the output can be piped back in. Every
    format renders this same dict, which is what keeps rich, yaml, json and
    jsonl agreeing on what a repository is. Anything the caller did not ask
    for is left out here rather than filtered per format, so no format can
    be the one that forgets.

    Parameters
    ----------
    url : str
        Instance base URL
    format_link_func : callable
        Phabfive.format_link, for the clickable monogram
    repos : list
        Repository records, fetched with attachments={"uris": True}
    branches_map : dict, optional
        Repository id to sorted branch names
    tags_map : dict, optional
        Repository id to sorted tag names
    space_map : dict, optional
        Space PHID to its name
    show_uris, show_branches, show_tags, show_metadata : bool, optional
        Include that section
    show_description : bool, optional
        Include the description in the Repository section

    Returns
    -------
    list
        One display dict per repository
    """
    branches_map = branches_map or {}
    tags_map = tags_map or {}
    space_map = space_map or {}

    records = []

    for repo in repos:
        fields = repo.get("fields", {})
        monogram = f"R{repo['id']}"

        # browseUri is what the instance itself would link to; not every
        # version reports it, so the monogram URL stands in, which redirects
        # to the same page.
        link_url = fields.get("browseUri") or f"{url}/{monogram}"

        record = {
            "_url": link_url,
            "_link": format_link_func(link_url, monogram),
            "Repository": {
                "Name": fields.get("name", ""),
                "Short Name": fields.get("shortName"),
                "Callsign": fields.get("callsign"),
                "Monogram": monogram,
                "Status": fields.get("status", ""),
                "VCS": fields.get("vcs", ""),
                "Default Branch": fields.get("defaultBranch"),
            },
        }

        if show_description:
            description = fields.get("description") or {}

            if isinstance(description, dict):
                description = description.get("raw") or ""

            record["Repository"]["Description"] = (
                PreservedScalarString(description)
                if "\n" in description
                else description
            )

        record["Repository"]["Hosted"] = repository_is_hosted(repo)
        record["Repository"]["Importing"] = bool(fields.get("isImporting"))

        space_phid = fields.get("spacePHID")
        if space_phid:
            record["Space"] = space_map.get(space_phid, space_phid)

        record["Policy"] = format_policy(fields.get("policy"))

        if show_uris:
            record["URIs"] = format_repository_uris(repo)

        if show_branches:
            record["Branches"] = branches_map.get(repo["id"], [])

        if show_tags:
            record["Tags"] = tags_map.get(repo["id"], [])

        if show_metadata:
            record["Metadata"] = {
                "PHID": repo.get("phid", ""),
                "ID": repo["id"],
                "Space PHID": space_phid or "(none)",
                "Almanac Service PHID": fields.get("almanacServicePHID") or "(none)",
                "Created": format_timestamp(fields["dateCreated"])
                if fields.get("dateCreated")
                else "",
                "Modified": format_timestamp(fields["dateModified"])
                if fields.get("dateModified")
                else "",
            }

        records.append(record)

    return records
