# -*- coding: utf-8 -*-

"""Build the display record for a paste."""

from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.maniphest.utils import format_timestamp


def build_paste_display_data(
    url,
    format_link_func,
    pastes,
    author_names=None,
    space_map=None,
    show_content=True,
):
    """
    Build the display record for each paste.

    One record per paste, nested and capitalized the way a task or a
    project is, with Link first so the output can be piped back in. Every
    format renders this same dict - `paste show` and `paste search` alike -
    so the two answer with the same record and a jq filter written for one
    works on the other.

    Every paste carries the same keys in the Paste section, so a table has
    the same columns on every row and a script can read a key without
    testing for it first. Content is the one exception: it is there only
    when the content was fetched, and an absent Content is "not asked for"
    where an empty one is an empty paste.

    Parameters
    ----------
    url : str
        Instance base URL
    format_link_func : callable
        Phabfive.format_link, for the clickable link
    pastes : list
        paste.search result items; with the content attachment when
        show_content
    author_names : dict, optional
        User PHID to username. A PHID missing from it stands in for the
        username, the way a member the viewer may not see does in a project.
    space_map : dict, optional
        Space PHID to its name
    show_content : bool, optional
        Include the content in the Paste section

    Returns
    -------
    list
        One display dict per paste
    """
    author_names = author_names or {}
    space_map = space_map or {}

    records = []

    for paste in pastes:
        fields = paste.get("fields", {})
        monogram = f"P{paste['id']}"
        link_url = f"{url}/{monogram}"
        author_phid = fields.get("authorPHID")

        record = {
            "_url": link_url,
            "_link": format_link_func(link_url, monogram),
            "Paste": {
                "Name": fields.get("title", ""),
                "Author": author_names.get(author_phid, author_phid),
                "Language": fields.get("language") or "text",
                "Status": fields.get("status", ""),
                "Created": format_timestamp(fields["dateCreated"])
                if fields.get("dateCreated")
                else None,
                "Modified": format_timestamp(fields["dateModified"])
                if fields.get("dateModified")
                else None,
            },
        }

        if show_content:
            content = (paste.get("attachments") or {}).get("content", {}).get(
                "content"
            ) or ""

            record["Paste"]["Content"] = (
                PreservedScalarString(content) if "\n" in content else content
            )

        space_phid = fields.get("spacePHID")
        if space_phid:
            record["Space"] = space_map.get(space_phid, space_phid)

        records.append(record)

    return records
