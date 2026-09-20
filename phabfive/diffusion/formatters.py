# -*- coding: utf-8 -*-

"""Data formatting functions for Diffusion module."""

from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.constants import POLICY_LABELS
from phabfive.maniphest.utils import format_timestamp


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


def format_uri(uri, credential_names=None):
    """
    Describe one repository URI the way the web UI's URI table does.

    The URI reported is ``uri.display`` - what the web UI shows and what a
    caller would clone. A URI record spells itself three ways (``raw``, what
    was typed; ``display``, what is shown; ``effective``, what Phabricator
    resolves it to), and the two list commands used to read two different
    ones, so `repo list --url` and `uri list` could disagree about the same
    URI. Every caller now reads ``display``.

    Parameters
    ----------
    uri : dict
        A URI record, as the "uris" attachment returns it
    credential_names : dict, optional
        Credential PHID to its monogram. Given, the record names the
        credential; left out, the record has no Credential field at all.
        A secret is never read either way - naming a credential is
        ``phid.query``, which answers with the monogram alone.

    Returns
    -------
    dict
        "URI", "I/O", "Display", optionally "Credential", and "Disabled"
    """
    fields = uri.get("fields", {})

    record = {
        "URI": fields["uri"]["display"],
        "I/O": fields["io"]["effective"],
        "Display": fields["display"]["effective"],
    }

    if credential_names is not None:
        phid = fields.get("credentialPHID")
        record["Credential"] = credential_names.get(phid, phid) if phid else "(none)"

    record["Disabled"] = bool(fields.get("disabled"))

    return record


def format_repository_uris(repo, credential_names=None):
    """
    Describe a repository's URIs the way the web UI's URI table does.

    Parameters
    ----------
    repo : dict
        A repository record, fetched with attachments={"uris": True}
    credential_names : dict, optional
        Credential PHID to its monogram, passed through to :func:`format_uri`

    Returns
    -------
    list
        One dict per URI
    """
    uris = repo.get("attachments", {}).get("uris", {}).get("uris", [])

    return [format_uri(uri, credential_names) for uri in uris]


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
