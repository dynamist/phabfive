# -*- coding: utf-8 -*-

"""Data formatting functions for Diffusion module."""

from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.constants import (
    POLICY_NOT_HOSTED,
    REPO_POLICY_FIELDS,
    URI_ROLE_DISABLED,
    URI_ROLES,
)
from phabfive.maniphest.utils import format_timestamp
from phabfive.policy import policy_label


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


def format_policy(policy, policy_names=None, hosted=True):
    """
    Label a repository's policies the way the Phorge web UI labels them.

    Parameters
    ----------
    policy : dict
        The "policy" field of a repository record
    policy_names : dict, optional
        PHID to name, from phabfive.policy.resolve_policy_names. A policy can
        carry a PHID rather than a keyword - a project, a user or a custom
        rule - and this is what names it. Left out, or missing an entry, the
        PHID is shown as it stands rather than guessed at.
    hosted : bool, optional
        Whether Phabricator serves the repository itself, from
        repository_is_hosted. A push policy on a repository that follows a
        remote is stored but inert, and is reported as POLICY_NOT_HOSTED
        rather than as a value that reads as if it were in force.

    Returns
    -------
    dict
        {"Visible To": ..., "Editable By": ..., "Can Push": ...}, each a
        web-UI label for a keyword constant, the name of a PHID that was
        resolved, or the raw value.

        The keys are Phorge's own labels rather than the API's field names.
        AphrontFormPolicyControl special-cases exactly three capabilities
        into the "-able By" family - CAN_VIEW "Visible To", CAN_EDIT
        "Editable By" and CAN_JOIN "Joinable By" - and every other one falls
        through to its capability name. Push is not one of the three, so it
        is DiffusionPushCapability::getCapabilityName() that names it, and
        that returns "Can Push". "Pushable By" is a label
        DiffusionRepositoryPoliciesManagementPanel applies to its own row
        and nothing else in Phorge uses. This is the same rule that names a
        task's "Can Interact".
    """
    policy = policy or {}

    return {
        "Visible To": policy_label(
            policy.get(REPO_POLICY_FIELDS["view"]), policy_names
        ),
        "Editable By": policy_label(
            policy.get(REPO_POLICY_FIELDS["edit"]), policy_names
        ),
        "Can Push": policy_label(policy.get(REPO_POLICY_FIELDS["push"]), policy_names)
        if hosted
        else POLICY_NOT_HOSTED,
    }


def uri_origin(uri):
    """Whether Phorge generated this URI or someone added it.

    Phorge gives a repository a built-in URI per protocol it serves, and
    says so by filling in ``builtin.protocol``. A URI someone added has a
    null protocol. Nothing else in the record distinguishes the two, and
    the distinction matters: it is half of what decides an inherited I/O
    value, and a built-in URI cannot be removed.

    Parameters
    ----------
    uri : dict
        A URI record, as the "uris" attachment returns it

    Returns
    -------
    str
        "built-in" or "external"
    """
    builtin = uri.get("fields", {}).get("builtin") or {}

    return "built-in" if builtin.get("protocol") else "external"


def builtin_clone_name(fields):
    """The name Phorge builds a repository's built-in URIs out of.

    Phorge asks the repository for a clone name and gets its short name if
    it has one, and its name if it does not. That is why renaming a
    repository that never got a short name moves its clone URIs while
    renaming one that has a short name does not.

    Parameters
    ----------
    fields : dict
        The "fields" of a repository record

    Returns
    -------
    str or None
        The clone name, or None for a record carrying neither
    """
    return fields.get("shortName") or fields.get("name")


def builtin_uri_moves(repo, old_name, new_name):
    """Every built-in URI a change of clone name moves, before and after.

    Read off the URIs the repository actually carries rather than built
    out of the field being edited, because a repository has as many
    built-in URIs as it has shapes to be addressed by - ``/source/<name>``,
    ``/diffusion/<id>/<name>`` and, with a callsign,
    ``/diffusion/<callsign>/<name>`` - and a rename moves all of them at
    once. Guessing one shape names the wrong number of them, and names
    none at all correctly on a repository with no short name, which has no
    ``/source/`` URI to guess.

    A built-in URI ends in the clone name, so only the last path segment
    moves. One that does not end in the old clone name is not derived from
    it and is left out.

    Parameters
    ----------
    repo : dict
        A repository record, fetched with attachments={"uris": True}
    old_name : str
        The clone name in force
    new_name : str
        The clone name it is moving to

    Returns
    -------
    list
        (old, new) display URIs, in the order the attachment returned them
    """
    uris = repo.get("attachments", {}).get("uris", {}).get("uris", [])
    moves = []

    for uri in uris:
        if uri_origin(uri) != "built-in":
            continue

        display = (uri.get("fields", {}).get("uri") or {}).get("display")

        if not display:
            continue

        head, slash, tail = display.rpartition("/")

        if not slash or not (tail == old_name or tail.startswith(f"{old_name}.")):
            continue

        # Whatever follows the clone name is the VCS suffix Phorge appended
        # (".git"), and it stays put.
        moves.append((display, f"{head}/{new_name}{tail[len(old_name) :]}"))

    return moves


def uri_role(uri):
    """What this URI actually does, in one line.

    Derived once here and carried in every format, so that rich, yaml,
    json and table answer the question the same way rather than each
    reader of the output deriving it again from the I/O value.

    Being disabled overrides everything: a disabled URI neither serves
    clones nor is pulled from, whatever its I/O says.

    Parameters
    ----------
    uri : dict
        A URI record, as the "uris" attachment returns it

    Returns
    -------
    str
        One of :data:`phabfive.constants.URI_ROLES`' answers, or
        "disabled". An I/O value Phorge has and phabfive does not is
        answered with the value itself rather than a guess.
    """
    fields = uri.get("fields", {})

    if fields.get("disabled"):
        return URI_ROLE_DISABLED

    effective = (fields.get("io") or {}).get("effective")

    return URI_ROLES.get(effective, effective)


def _resolution(section):
    """Publish a value that is set, inherited or resolved, as all three.

    Phorge answers with ``raw`` (what is written on the URI, possibly the
    literal "default"), ``default`` (what it would inherit, which depends
    on whether the repository is hosted and whether the URI is built-in)
    and ``effective`` (what is actually in force). ``effective`` alone
    cannot say whether a value was chosen or inherited, and ``raw`` alone
    is "default" often enough to be useless, so all three are published
    and the reader decides.

    Parameters
    ----------
    section : dict
        An ``io`` or ``display`` section of a URI record

    Returns
    -------
    dict
        {"Raw": ..., "Default": ..., "Effective": ...}
    """
    section = section or {}

    return {
        "Raw": section.get("raw"),
        "Default": section.get("default"),
        "Effective": section.get("effective"),
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

    A URI has four independent dimensions - where it came from, what I/O it
    does, whether it is shown, and whether it is disabled - and the record
    carries all four. ``I/O`` and ``Display`` are published in full rather
    than flattened to their effective value, because an inherited value and
    a chosen one are different facts about the URI (#375).

    This is the one description of a URI in phabfive: `uri list`,
    `repo list --show-uris` and `repo show --show-uris` all render this
    record, which is what keeps them from disagreeing again.

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
        "URI", "Origin", "Role", "I/O", "Display", optionally
        "Credential", and "Disabled". "URI" stays first and stays a
        scalar: every renderer here takes a record's first field as the
        identifying one.
    """
    fields = uri.get("fields", {})

    record = {
        "URI": fields["uri"]["display"],
        "Origin": uri_origin(uri),
        "Role": uri_role(uri),
        "I/O": _resolution(fields.get("io")),
        "Display": _resolution(fields.get("display")),
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
    policy_names=None,
    show_uris=False,
    show_branches=False,
    show_tags=False,
    show_metadata=False,
    show_policy=False,
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
    policy_names : dict, optional
        Policy PHID to its name, for the policies that name a project, a
        user or a custom rule rather than a keyword
    show_uris, show_branches, show_tags, show_metadata, show_policy : bool, optional
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
    policy_names = policy_names or {}

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

        hosted = repository_is_hosted(repo)

        record["Repository"]["Hosted"] = hosted
        record["Repository"]["Importing"] = bool(fields.get("isImporting"))

        space_phid = fields.get("spacePHID")
        if space_phid:
            record["Space"] = space_map.get(space_phid, space_phid)

        # Gated here rather than per format, and gated over the resolution
        # too: the caller skips the phid.query that names the policies when
        # nobody asked for them. A repository that was not asked about says
        # nothing at all, which is a different answer from the one a
        # non-hosted repository gives about its push policy.
        if show_policy:
            record["Policy"] = format_policy(
                fields.get("policy"), policy_names, hosted=hosted
            )

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
