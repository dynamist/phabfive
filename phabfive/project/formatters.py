# -*- coding: utf-8 -*-

"""Build the display record for a project."""

from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.constants import PROJECT_POLICY_FIELDS
from phabfive.maniphest.utils import format_timestamp
from phabfive.policy import policy_label


def project_link_url(url, project):
    """The page a project lives at.

    By ID rather than by hashtag: a milestone has no hashtag, and a hashtag
    can be renamed, while ``/project/view/<id>/`` names the same project for
    as long as it exists.
    """
    return f"{url}/project/view/{project['id']}/"


def project_hashtag(project):
    """The project's hashtag, "#slug", or None for a milestone, which has none."""
    slug = project.get("fields", {}).get("slug")

    return f"#{slug}" if slug else None


def describe_project(project):
    """How a project reads in a message: its hashtag, else its name and ID.

    A milestone has no hashtag and usually shares its name with milestones
    of other projects, so it is told apart by its ID.
    """
    fields = project.get("fields", {})

    return project_hashtag(project) or f"{fields.get('name', '')} (ID {project['id']})"


def format_project_policy(policy, policy_names=None):
    """
    Label a project's policies the way the Phorge web UI labels them.

    Parameters
    ----------
    policy : dict
        The "policy" field of a project record
    policy_names : dict, optional
        PHID to name, from phabfive.policy.resolve_policy_names

    Returns
    -------
    dict
        {"Visible To": ..., "Editable By": ..., "Joinable By": ...}. All three
        are among the capabilities AphrontFormPolicyControl special-cases into
        the "-able By" family, which makes a project the one object whose
        third policy is labelled that way.
    """
    policy = policy or {}

    return {
        "Visible To": policy_label(
            policy.get(PROJECT_POLICY_FIELDS["view"]), policy_names
        ),
        "Editable By": policy_label(
            policy.get(PROJECT_POLICY_FIELDS["edit"]), policy_names
        ),
        "Joinable By": policy_label(
            policy.get(PROJECT_POLICY_FIELDS["join"]), policy_names
        ),
    }


def format_member(phid, user=None):
    """
    One member of a project, as a record.

    Parameters
    ----------
    phid : str
        The member's PHID
    user : dict, optional
        The user.search result item for it. Missing when the viewer may not
        see that user, and then the PHID stands in for the username.

    Returns
    -------
    dict
        {"Username", "Name", "Roles"}. Roles are user.search's own, passed
        through unchanged - "bot", "admin", "disabled", "verified" and so on
        - because telling a bot from a person is exactly what a membership
        check is for, and a renamed role would be one more thing to map back.
    """
    fields = (user or {}).get("fields", {})

    return {
        "Username": fields.get("username") or phid,
        "Name": fields.get("realName") or "",
        "Roles": list(fields.get("roles") or []),
    }


def project_member_phids(project):
    """The PHIDs of a project's members, from its members attachment."""
    members = project.get("attachments", {}).get("members", {}).get("members", [])

    return [member["phid"] for member in members if member.get("phid")]


def build_project_display_data(
    url,
    format_link_func,
    projects,
    space_map=None,
    policy_names=None,
    users=None,
    show_policy=False,
    show_members=False,
    show_metadata=False,
    show_description=True,
):
    """
    Build the display record for each project.

    One record per project, nested and capitalized the way a repository's
    is, with Link first so the output can be piped back in. Every format
    renders this same dict, and anything the caller did not ask for is left
    out here rather than filtered per format.

    Every project carries the same keys in the Project section, a milestone
    included - Hashtag is None on a milestone and Parent is None on a root
    project - so a table has the same columns on every row and a script can
    read a key without testing for it first.

    Parameters
    ----------
    url : str
        Instance base URL
    format_link_func : callable
        Phabfive.format_link, for the clickable link
    projects : list
        project.search result items; with the members attachment when
        show_members
    space_map : dict, optional
        Space PHID to its name
    policy_names : dict, optional
        Policy PHID to its name
    users : dict, optional
        User PHID to user.search result item, for the members
    show_policy, show_members, show_metadata : bool, optional
        Include that section
    show_description : bool, optional
        Include the description in the Project section

    Returns
    -------
    list
        One display dict per project
    """
    space_map = space_map or {}
    policy_names = policy_names or {}
    users = users or {}

    records = []

    for project in projects:
        fields = project.get("fields", {})
        link_url = project_link_url(url, project)
        hashtag = project_hashtag(project)
        parent = fields.get("parent") or {}

        record = {
            "_url": link_url,
            "_link": format_link_func(link_url, hashtag or fields.get("name", "")),
            "Project": {
                "Name": fields.get("name", ""),
                "Hashtag": hashtag,
                "Status": fields.get("status", ""),
                "Icon": (fields.get("icon") or {}).get("key"),
                "Color": (fields.get("color") or {}).get("key"),
                "Parent": parent.get("name"),
                "Milestone": fields.get("milestone"),
            },
        }

        if show_description:
            description = fields.get("description") or ""

            if isinstance(description, dict):
                description = description.get("raw") or ""

            record["Project"]["Description"] = (
                PreservedScalarString(description)
                if "\n" in description
                else description
            )

        space_phid = fields.get("spacePHID")
        if space_phid:
            record["Space"] = space_map.get(space_phid, space_phid)

        if show_policy:
            record["Policy"] = format_project_policy(fields.get("policy"), policy_names)

        if show_members:
            members = [
                format_member(phid, users.get(phid))
                for phid in project_member_phids(project)
            ]
            record["Members"] = sorted(
                members, key=lambda member: member["Username"].casefold()
            )

        if show_metadata:
            record["Metadata"] = {
                "PHID": project.get("phid", ""),
                "ID": project["id"],
                "Parent PHID": parent.get("phid") or "(none)",
                "Space PHID": space_phid or "(none)",
                "Created": format_timestamp(fields["dateCreated"])
                if fields.get("dateCreated")
                else "",
                "Modified": format_timestamp(fields["dateModified"])
                if fields.get("dateModified")
                else "",
            }

        records.append(record)

    return records
