# -*- coding: utf-8 -*-
"""Display functions for Passphrase credentials."""

import json
import sys
from io import StringIO

from rich.text import Text
from rich.tree import Tree
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import PreservedScalarString


def display_passphrase_rich(console, passphrase_dict, phabfive_instance):
    """Display a passphrase in YAML-like format using Rich.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    passphrase_dict : dict
        Passphrase data dictionary with _link, url, type, name, username, secret
    phabfive_instance : Phabfive
        Instance to access format_link() and url
    """
    link = passphrase_dict.get("_link")
    passphrase_type = passphrase_dict.get("type", "Unknown")
    name = passphrase_dict.get("name", "")
    username = passphrase_dict.get("username")
    secret = passphrase_dict.get("secret", "")

    # Print link
    console.print(Text.assemble("- Link: ", link))

    # Print Type
    console.print(f"  Type: {passphrase_type}")

    # Print Name
    console.print(f"  Name: {name}")

    # Print Username (only when present)
    if username:
        console.print(f"  Username: {username}")

    # Print Secret (handle multi-line for SSH keys)
    if "\n" in secret:
        console.print("  Secret: |-")
        for line in secret.splitlines():
            console.print(f"    {line}")
    else:
        console.print(f"  Secret: {secret}")


def display_passphrase_tree(console, passphrase_dict, phabfive_instance):
    """Display a passphrase in tree format using Rich Tree.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    passphrase_dict : dict
        Passphrase data dictionary with _link, url, type, name, username, secret
    phabfive_instance : Phabfive
        Instance to access format_link() and url
    """
    link = passphrase_dict.get("_link")
    passphrase_type = passphrase_dict.get("type", "Unknown")
    name = passphrase_dict.get("name", "")
    username = passphrase_dict.get("username")
    secret = passphrase_dict.get("secret", "")

    # Create tree with link as root
    tree = Tree(link)

    # Add fields
    tree.add(f"Type: {passphrase_type}")
    tree.add(f"Name: {name}")

    if username:
        tree.add(f"Username: {username}")

    # For multi-line secrets, show first line only
    if "\n" in secret:
        first_line = secret.split("\n")[0]
        if len(first_line) > 50:
            first_line = first_line[:47] + "..."
        tree.add(f"Secret: {first_line}")
    else:
        tree.add(f"Secret: {secret}")

    console.print(tree)


def display_passphrase_yaml(passphrase_dict):
    """Display passphrase as strict YAML via ruamel.yaml.

    Guaranteed conformant YAML output for piping to yq/jq.
    No hyperlinks, no Rich formatting.

    Parameters
    ----------
    passphrase_dict : dict
        Passphrase data dictionary with url, type, name, username, secret
    """
    yaml = YAML()
    yaml.default_flow_style = False

    # Build clean dict - use url for the Link (plain URL string)
    output = {
        "Link": passphrase_dict.get("url", ""),
        "Type": passphrase_dict.get("type", "Unknown"),
        "Name": passphrase_dict.get("name", ""),
    }

    # Include Username only if present
    username = passphrase_dict.get("username")
    if username:
        output["Username"] = username

    # Handle multi-line secrets with block scalar
    secret = passphrase_dict.get("secret", "")
    if "\n" in secret:
        output["Secret"] = PreservedScalarString(secret)
    else:
        output["Secret"] = secret

    stream = StringIO()
    yaml.dump([output], stream)
    print(stream.getvalue(), end="")


def display_passphrase_json(passphrase_dict):
    """Display passphrase as JSON.

    Machine-readable JSON output for piping to jq or other tools.
    No hyperlinks, no Rich formatting.

    Parameters
    ----------
    passphrase_dict : dict
        Passphrase data dictionary with url, type, name, username, secret
    """
    output = {
        "Link": passphrase_dict.get("url", ""),
        "Type": passphrase_dict.get("type", "Unknown"),
        "Name": passphrase_dict.get("name", ""),
    }

    # Include Username only if present
    username = passphrase_dict.get("username")
    if username:
        output["Username"] = username

    output["Secret"] = passphrase_dict.get("secret", "")

    print(json.dumps(output, indent=2))


def display_passphrase_simple(passphrase_dict):
    """Display only the secret value (for piping).

    Parameters
    ----------
    passphrase_dict : dict
        Passphrase data dictionary with secret
    """
    print(passphrase_dict.get("secret", ""))


def display_passphrase(passphrase_dict, output_format, phabfive_instance):
    """Display passphrase in the specified format.

    Parameters
    ----------
    passphrase_dict : dict
        Passphrase data from get_passphrase()
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json', or 'simple'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    """
    console = phabfive_instance.get_console()

    try:
        if output_format == "simple":
            display_passphrase_simple(passphrase_dict)
        elif output_format == "tree":
            display_passphrase_tree(console, passphrase_dict, phabfive_instance)
        elif output_format in ("yaml", "strict"):
            display_passphrase_yaml(passphrase_dict)
        elif output_format == "json":
            display_passphrase_json(passphrase_dict)
        else:  # "rich" (default)
            display_passphrase_rich(console, passphrase_dict, phabfive_instance)
    except BrokenPipeError:
        # Handle pipe closed by consumer (e.g., head, less)
        sys.stderr.close()
        sys.exit(0)
