# -*- coding: utf-8 -*-
"""Display functions for Diffusion repositories.

Mirrors ``phabfive.display``, with one difference: a repository record is a
plain nested dict of scalars, lists and dicts, so every format here walks
that same dict generically instead of naming the sections one by one. That
is what makes the formats agree by construction - rich cannot grow a field
that yaml lacks, because neither knows what the fields are.

Rich contributes an OSC-8 hyperlink and colour, not a different layout: the
output is YAML-shaped, exactly as ``_display_task_rich`` is.
"""

from io import StringIO

from rich.text import Text
from rich.tree import Tree
from ruamel.yaml import YAML

from phabfive.display import _escape_for_rich, render_records
from phabfive.json_output import emit_records

# How far a tree node's value is allowed to run before it is cut short.
_TREE_VALUE_WIDTH = 60


def _public(record):
    """The record as it is published, Link first and no internal keys.

    Parameters
    ----------
    record : dict
        A repository display record, carrying _url and _link

    Returns
    -------
    dict
        The same record with a plain "Link" in front of it
    """
    output = {"Link": record.get("_url", "")}

    for key, value in record.items():
        if not key.startswith("_"):
            output[key] = value

    return output


def _yaml_scalar(value):
    """Spell a scalar the way YAML spells it, for the rich renderer."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return value


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


def _display_repository_rich(console, record, phabfive_instance):
    """Display a single repository in YAML-like format using Rich.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    record : dict
        A repository display record
    phabfive_instance : Phabfive
        Instance to access format_link() and check_line_width()
    """
    console.print(Text.assemble("- Link: ", record.get("_link", "")))

    sections = {k: v for k, v in record.items() if not k.startswith("_")}
    _print_mapping(console, sections, 2, phabfive_instance)


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


def _display_repository_tree(console, record, phabfive_instance):
    """Display a single repository as a Rich Tree.

    The same dict every other format renders, which is the point: a global
    --format must not quietly fall back on one app.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    record : dict
        A repository display record
    phabfive_instance : Phabfive
        Instance to access format_link()
    """
    tree = Tree(record.get("_link", record.get("_url", "")))

    _add_mapping(tree, {k: v for k, v in record.items() if not k.startswith("_")})

    console.print(tree)


def display_repositories_rich(console, records, phabfive_instance):
    """Display repositories in YAML-like format using Rich."""
    for record in records:
        _display_repository_rich(console, record, phabfive_instance)


def display_repositories_tree(console, records, phabfive_instance):
    """Display repositories as Rich Trees."""
    for record in records:
        _display_repository_tree(console, record, phabfive_instance)


def display_repositories_yaml(records):
    """Display repositories as strict YAML."""
    yaml = YAML()
    yaml.default_flow_style = False

    for record in records:
        stream = StringIO()
        yaml.dump([_public(record)], stream)
        print(stream.getvalue(), end="")


def display_repositories_json(records, output_format="json"):
    """Display repositories as a JSON array, or one object per line for jsonl."""
    emit_records([_public(record) for record in records], output_format)


def display_repositories(result, output_format, phabfive_instance):
    """Display repo show results in the specified format.

    Parameters
    ----------
    result : dict
        Result from Diffusion.repo_show(), containing 'repositories'
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json' or 'jsonl'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    """
    if not result or not result.get("repositories"):
        return

    console = phabfive_instance.get_console()
    records = result["repositories"]

    render_records(
        output_format,
        {
            "json": lambda: display_repositories_json(records, "json"),
            "jsonl": lambda: display_repositories_json(records, "jsonl"),
            "tree": lambda: display_repositories_tree(
                console, records, phabfive_instance
            ),
            "yaml": lambda: display_repositories_yaml(records),
            "rich": lambda: display_repositories_rich(
                console, records, phabfive_instance
            ),
        },
    )
