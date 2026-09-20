# -*- coding: utf-8 -*-
"""Display functions for Diffusion.

Mirrors ``phabfive.display``, with one difference: a repository record is a
plain nested dict of scalars, lists and dicts, so every format here walks
that same dict generically instead of naming the sections one by one. That
is what makes the formats agree by construction - rich cannot grow a field
that yaml lacks, because neither knows what the fields are.

Rich contributes an OSC-8 hyperlink and colour, not a different layout: the
output is YAML-shaped, exactly as ``_display_task_rich`` is.

Repositories and URIs both come through here. Nothing below knows which it
is holding - a record is a dict, a record with a ``_url`` leads with a Link
and one without leads with its first field - so ``repo show``, ``repo list``
and ``uri list`` share one set of renderers and one ``--format`` switch.

``table`` is the one format they do not all share, and not because it knows
anything about repositories: a grid needs a list, so only the list commands
register it and ``repo show`` falls back to rich. The renderer itself is
``phabfive.table``, app-agnostic, reading the published record.
"""

from io import StringIO

from rich.text import Text
from rich.tree import Tree
from ruamel.yaml import YAML

from phabfive.display import _escape_for_rich, _yaml_scalar, render_records
from phabfive.json_output import emit_records
from phabfive.table import display_records_table

# How far a tree node's value is allowed to run before it is cut short.
_TREE_VALUE_WIDTH = 60


def _public(record):
    """The record as it is published, Link first and no internal keys.

    Parameters
    ----------
    record : dict
        A display record, carrying _url and _link when it has a page of
        its own. A URI has none, and then the record leads with its own
        first field instead of an empty Link.

    Returns
    -------
    dict
        The same record with a plain "Link" in front of it, when there is
        one to put there
    """
    output = {"Link": record["_url"]} if record.get("_url") else {}

    for key, value in record.items():
        if not key.startswith("_"):
            output[key] = value

    return output


def _print_pair(console, key, value, lead, indent, phabfive_instance):
    """Print one ``key: value``, recursing into whatever the value is.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    key : str
        The field name
    value : any
        Scalar, list or dict
    lead : str
        Everything printed before the key - the indent, plus a "- " when
        this is the first field of a list item
    indent : int
        The column this pair's children are indented from
    phabfive_instance : Phabfive
        Instance to access check_line_width()
    """
    pad = " " * (indent + 2)

    if isinstance(value, dict):
        console.print(f"{lead}{key}:")
        _print_mapping(console, value, indent + 2, phabfive_instance)
    elif isinstance(value, list):
        if not value:
            console.print(f"{lead}{key}: []")
            return

        console.print(f"{lead}{key}:")
        for item in value:
            if isinstance(item, dict):
                _print_mapping(
                    console, item, indent + 2, phabfive_instance, first_prefix="- "
                )
            else:
                console.print(f"{pad}- {_escape_for_rich(_yaml_scalar(item))}")
    elif value is None:
        # ruamel writes a bare "Key:" for None; rich says the same thing.
        console.print(f"{lead}{key}:")
    elif value == "":
        # And it writes '' for an empty string, which a bare "Key:" would
        # read back as None - a different value. A repository with no
        # description is the common case for this.
        console.print(f"{lead}{key}: ''")
    elif isinstance(value, str) and "\n" in value:
        console.print(f"{lead}{key}: |-")
        for line in value.splitlines():
            console.print(f"{pad}{_escape_for_rich(line)}")
    else:
        phabfive_instance.check_line_width(value, key)
        console.print(f"{lead}{key}: {_escape_for_rich(_yaml_scalar(value))}")


def _print_mapping(console, mapping, indent, phabfive_instance, first_prefix=""):
    """Print a mapping YAML-shaped.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    mapping : dict
        The mapping to print
    indent : int
        Column to indent from
    phabfive_instance : Phabfive
        Instance to access check_line_width()
    first_prefix : str, optional
        Written in front of the first key, so that "- " turns the mapping
        into a list item without the rest of it knowing
    """
    for position, (key, value) in enumerate(mapping.items()):
        prefix = first_prefix if position == 0 else " " * len(first_prefix)
        _print_pair(
            console,
            key,
            value,
            " " * indent + prefix,
            indent + len(first_prefix),
            phabfive_instance,
        )


def _display_record_rich(console, record, phabfive_instance):
    """Display a single record in YAML-like format using Rich.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    record : dict
        A display record
    phabfive_instance : Phabfive
        Instance to access format_link() and check_line_width()
    """
    sections = {k: v for k, v in record.items() if not k.startswith("_")}

    if record.get("_url"):
        console.print(Text.assemble("- Link: ", record.get("_link") or record["_url"]))
        _print_mapping(console, sections, 2, phabfive_instance)
        return

    # No page to link to - a URI has none - so the first field is what
    # opens the list item, which is what yaml does with the same record.
    _print_mapping(console, sections, 0, phabfive_instance, first_prefix="- ")


def _shorten(value):
    """A one-line stand-in for a value, for the tree renderer."""
    text = "" if value is None else str(_yaml_scalar(value))
    first_line = text.split("\n")[0]

    if len(first_line) > _TREE_VALUE_WIDTH:
        return first_line[: _TREE_VALUE_WIDTH - 3] + "..."

    return first_line


def _add_mapping(branch, mapping):
    """Add a mapping's fields to a Rich Tree branch."""
    for key, value in mapping.items():
        if isinstance(value, dict):
            _add_mapping(branch.add(key), value)
        elif isinstance(value, list):
            if not value:
                branch.add(f"{key}: []")
                continue

            sub = branch.add(key)
            for item in value:
                if isinstance(item, dict):
                    # Label the node with the item's first field - the URI
                    # itself, for a URI - and hang the rest under it.
                    fields = list(item.items())
                    _add_mapping(
                        sub.add(_escape_for_rich(_shorten(fields[0][1]))),
                        dict(fields[1:]),
                    )
                else:
                    sub.add(_escape_for_rich(_shorten(item)))
        else:
            branch.add(f"{key}: {_escape_for_rich(_shorten(value))}")


def _display_record_tree(console, record, phabfive_instance):
    """Display a single record as a Rich Tree.

    The same dict every other format renders, which is the point: a global
    --format must not quietly fall back on one app.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    record : dict
        A display record
    phabfive_instance : Phabfive
        Instance to access format_link()
    """
    sections = {k: v for k, v in record.items() if not k.startswith("_")}
    root = record.get("_link") or record.get("_url")

    if not root:
        # A record with no page of its own is rooted at its first field -
        # the URI itself, for a URI - the way a list item already is.
        fields = list(sections.items())
        root = _escape_for_rich(_shorten(fields[0][1])) if fields else ""
        sections = dict(fields[1:])

    tree = Tree(root)

    _add_mapping(tree, sections)

    console.print(tree)


def display_records_rich(console, records, phabfive_instance):
    """Display records in YAML-like format using Rich."""
    for record in records:
        _display_record_rich(console, record, phabfive_instance)


def display_records_tree(console, records, phabfive_instance):
    """Display records as Rich Trees."""
    for record in records:
        _display_record_tree(console, record, phabfive_instance)


def display_records_yaml(records):
    """Display records as strict YAML."""
    yaml = YAML()
    yaml.default_flow_style = False

    for record in records:
        stream = StringIO()
        yaml.dump([_public(record)], stream)
        print(stream.getvalue(), end="")


def display_records_json(records, output_format="json"):
    """Display records as a JSON array, or one object per line for jsonl."""
    emit_records([_public(record) for record in records], output_format)


def display_records(records, output_format, phabfive_instance, tabular=False):
    """Display records in the specified format. The one --format switch.

    Every diffusion command that prints records comes through here, so a
    format added or fixed for one of them is added or fixed for all.

    Parameters
    ----------
    records : list
        Display records, as the formatters build them
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json', 'jsonl' or 'table'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    tabular : bool, optional
        Whether this call is list-shaped, and so has a table to offer.
        ``repo show`` sets it False and `--format=table` falls back to
        rich there, which is :func:`render_records` doing nothing special.
    """
    if not records:
        return

    console = phabfive_instance.get_console()

    renderers = {
        "json": lambda: display_records_json(records, "json"),
        "jsonl": lambda: display_records_json(records, "jsonl"),
        "tree": lambda: display_records_tree(console, records, phabfive_instance),
        "yaml": lambda: display_records_yaml(records),
        "rich": lambda: display_records_rich(console, records, phabfive_instance),
    }

    if tabular:
        # The table reads the published record, the same one yaml and json
        # publish, so it has no idea whether it is holding repositories or
        # URIs - and neither app needs a column list of its own.
        renderers["table"] = lambda: display_records_table(
            console, [_public(record) for record in records]
        )

    render_records(output_format, renderers)


def display_repositories(result, output_format, phabfive_instance, tabular=False):
    """Display `repo show` and `repo list` results in the specified format.

    Parameters
    ----------
    result : dict
        Result from Diffusion.repo_show() or Diffusion.repo_list(),
        containing 'repositories'
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json', 'jsonl' or 'table'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    tabular : bool, optional
        True from `repo list`, which is list-shaped. `repo show` leaves it
        False and gets rich for `--format=table`.
    """
    if not result:
        return

    display_records(
        result.get("repositories"), output_format, phabfive_instance, tabular=tabular
    )


def display_uris(result, output_format, phabfive_instance, tabular=False):
    """Display `uri list` results in the specified format.

    Parameters
    ----------
    result : dict
        Result from Diffusion.uri_list(), containing 'uris'
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json', 'jsonl' or 'table'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    tabular : bool, optional
        True from `uri list`, which is list-shaped
    """
    if not result:
        return

    display_records(
        result.get("uris"), output_format, phabfive_instance, tabular=tabular
    )
