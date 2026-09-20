# -*- coding: utf-8 -*-
"""Display functions for Passphrase credentials."""

import json
import sys
from datetime import datetime
from io import StringIO

from rich.text import Text
from rich.tree import Tree
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import PreservedScalarString

from phabfive.json_output import iter_records


def _format_timestamp(ts):
    """Convert Unix timestamp to ISO format string."""
    if ts:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    return None


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

    # Print Secret (only when present and non-empty)
    if "secret" in passphrase_dict and secret:
        if "\n" in secret:
            console.print("  Secret: |-")
            for line in secret.splitlines():
                console.print(f"    {line}")
        else:
            console.print(f"  Secret: {secret}")

    # Print Public Key (only for SSH credentials when requested)
    public_key = passphrase_dict.get("public_key", "")
    if public_key:
        if "\n" in public_key:
            console.print("  PublicKey: |-")
            for line in public_key.splitlines():
                console.print(f"    {line}")
        else:
            console.print(f"  PublicKey: {public_key}")

    # Print dates
    created = _format_timestamp(passphrase_dict.get("dateCreated"))
    if created:
        console.print(f"  Created: {created}")
    modified = _format_timestamp(passphrase_dict.get("dateModified"))
    if modified:
        console.print(f"  Modified: {modified}")


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

    # Show secret only if present and non-empty
    if "secret" in passphrase_dict and secret:
        secret_stripped = secret.strip()
        # For multi-line secrets, use subtree
        if "\n" in secret_stripped:
            secret_branch = tree.add("Secret:")
            for line in secret_stripped.splitlines():
                secret_branch.add(line)
        else:
            tree.add(f"Secret: {secret_stripped}")

    # Show public key if present
    public_key = passphrase_dict.get("public_key", "")
    if public_key:
        public_key_stripped = public_key.strip()
        # For multi-line, use subtree; otherwise show full
        if "\n" in public_key_stripped:
            pk_branch = tree.add("PublicKey:")
            for line in public_key_stripped.splitlines():
                pk_branch.add(line)
        else:
            tree.add(f"PublicKey: {public_key_stripped}")

    # Show dates
    created = _format_timestamp(passphrase_dict.get("dateCreated"))
    if created:
        tree.add(f"Created: {created}")
    modified = _format_timestamp(passphrase_dict.get("dateModified"))
    if modified:
        tree.add(f"Modified: {modified}")

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

    # Include PublicKey for SSH credentials
    public_key = passphrase_dict.get("public_key", "")
    if public_key:
        if "\n" in public_key:
            output["PublicKey"] = PreservedScalarString(public_key)
        else:
            output["PublicKey"] = public_key

    # Include dates
    created = _format_timestamp(passphrase_dict.get("dateCreated"))
    if created:
        output["Created"] = created
    modified = _format_timestamp(passphrase_dict.get("dateModified"))
    if modified:
        output["Modified"] = modified

    stream = StringIO()
    yaml.dump([output], stream)
    print(stream.getvalue(), end="")


def _build_passphrase_json_output(passphrase_dict):
    """Build a clean JSON-serializable dict for a single passphrase.

    Parameters
    ----------
    passphrase_dict : dict
        Passphrase data dictionary with url, type, name, username, secret

    Returns
    -------
    dict
        Clean dictionary ready for JSON serialization.
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

    # Include PublicKey for SSH credentials
    public_key = passphrase_dict.get("public_key")
    if public_key:
        output["PublicKey"] = public_key

    # Include dates
    created = _format_timestamp(passphrase_dict.get("dateCreated"))
    if created:
        output["Created"] = created
    modified = _format_timestamp(passphrase_dict.get("dateModified"))
    if modified:
        output["Modified"] = modified

    return output


def display_passphrase_json(passphrase_dict):
    """Display passphrase as JSON.

    Machine-readable JSON output for piping to jq or other tools.
    No hyperlinks, no Rich formatting.

    Parameters
    ----------
    passphrase_dict : dict
        Passphrase data dictionary with url, type, name, username, secret
    """
    output = _build_passphrase_json_output(passphrase_dict)
    # Intentional: a top-level object, not an array, for a single credential.
    # The print stays in this module rather than in phabfive/json_output.py so
    # the clear-text-logging suppression covers only the code that is meant to
    # write secrets, instead of blinding the shared emitter for every caller.
    print(json.dumps(output, indent=2))  # noqa: T201  # lgtm[py/clear-text-logging-sensitive-data]


def display_passphrase_value(passphrase_dict):
    """Display only the secret value (for piping).

    Parameters
    ----------
    passphrase_dict : dict
        Passphrase data dictionary with secret
    """
    # Intentional: This function's purpose is to output secrets for piping
    print(passphrase_dict.get("secret", ""))  # noqa: T201  # lgtm[py/clear-text-logging-sensitive-data]


def display_passphrase(passphrase_dict, output_format, phabfive_instance):
    """Display passphrase in the specified format.

    Parameters
    ----------
    passphrase_dict : dict
        Passphrase data from get_passphrase()
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json', 'jsonl', or 'value'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    """
    console = phabfive_instance.get_console()

    try:
        if output_format == "value":
            display_passphrase_value(passphrase_dict)
        elif output_format == "tree":
            display_passphrase_tree(console, passphrase_dict, phabfive_instance)
        elif output_format in ("yaml", "strict"):
            display_passphrase_yaml(passphrase_dict)
        elif output_format == "json":
            display_passphrase_json(passphrase_dict)
        elif output_format == "jsonl":
            # Deliberately the list builder: jsonl emits the same object shape
            # whether one credential was asked for or several
            display_passphrases_json([passphrase_dict], output_format="jsonl")
        else:  # "rich" (default)
            display_passphrase_rich(console, passphrase_dict, phabfive_instance)
    except BrokenPipeError:
        # Handle pipe closed by consumer (e.g., head, less)
        sys.stderr.close()
        sys.exit(0)


def display_passphrases(
    credentials, output_format, phabfive_instance, show_secrets=True
):
    """Display multiple passphrases in the specified format.

    Parameters
    ----------
    credentials : list
        List of passphrase data dictionaries
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json', 'jsonl', or 'value'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    show_secrets : bool
        Whether to show secret values
    """
    console = phabfive_instance.get_console()

    try:
        if output_format == "value":
            for cred in credentials:
                if show_secrets and "secret" in cred:
                    # Intentional: Output secrets for piping
                    print(cred.get("secret", ""))  # noqa: T201  # lgtm[py/clear-text-logging-sensitive-data]
        elif output_format == "tree":
            for cred in credentials:
                display_passphrase_tree(console, cred, phabfive_instance)
        elif output_format in ("yaml", "strict"):
            display_passphrases_yaml(credentials, show_secrets)
        elif output_format in ("json", "jsonl"):
            display_passphrases_json(
                credentials, show_secrets, output_format=output_format
            )
        else:  # "rich" (default)
            for cred in credentials:
                display_passphrase_rich(console, cred, phabfive_instance)
    except BrokenPipeError:
        sys.stderr.close()
        sys.exit(0)


def display_passphrases_yaml(credentials, show_secrets=True):
    """Display multiple passphrases as YAML.

    Parameters
    ----------
    credentials : list
        List of passphrase data dictionaries
    show_secrets : bool
        Whether to include secret values
    """
    yaml = YAML()
    yaml.default_flow_style = False

    output = []
    for cred in credentials:
        item = {
            "Link": cred.get("url", ""),
            "Type": cred.get("type", "Unknown"),
            "Name": cred.get("name", ""),
        }

        username = cred.get("username")
        if username:
            item["Username"] = username

        if show_secrets and "secret" in cred:
            secret = cred.get("secret", "")
            if "\n" in secret:
                item["Secret"] = PreservedScalarString(secret)
            else:
                item["Secret"] = secret

        if "public_key" in cred:
            public_key = cred.get("public_key", "")
            if "\n" in public_key:
                item["PublicKey"] = PreservedScalarString(public_key)
            else:
                item["PublicKey"] = public_key

        # Include dates
        created = _format_timestamp(cred.get("dateCreated"))
        if created:
            item["Created"] = created
        modified = _format_timestamp(cred.get("dateModified"))
        if modified:
            item["Modified"] = modified

        output.append(item)

    stream = StringIO()
    yaml.dump(output, stream)
    print(stream.getvalue(), end="")


def _build_passphrases_json_output(credentials, show_secrets=True):
    """Build clean JSON-serializable dicts for a list of passphrases.

    Kept separate from the single-credential builder above: that one always
    carries a Secret key, this one only when secrets were asked for.

    Parameters
    ----------
    credentials : list
        List of passphrase data dictionaries
    show_secrets : bool
        Whether to include secret values

    Returns
    -------
    list[dict]
        Clean dictionaries ready for JSON serialization.
    """
    output = []
    for cred in credentials:
        item = {
            "Link": cred.get("url", ""),
            "Type": cred.get("type", "Unknown"),
            "Name": cred.get("name", ""),
        }

        username = cred.get("username")
        if username:
            item["Username"] = username

        if show_secrets and "secret" in cred:
            item["Secret"] = cred.get("secret", "")

        if "public_key" in cred:
            item["PublicKey"] = cred.get("public_key", "")

        # Include dates
        created = _format_timestamp(cred.get("dateCreated"))
        if created:
            item["Created"] = created
        modified = _format_timestamp(cred.get("dateModified"))
        if modified:
            item["Modified"] = modified

        output.append(item)

    return output


def display_passphrases_json(credentials, show_secrets=True, output_format="json"):
    """Display multiple passphrases as JSON, or one object per line for jsonl.

    Parameters
    ----------
    credentials : list
        List of passphrase data dictionaries
    show_secrets : bool
        Whether to include secret values
    output_format : str
        Either 'json' or 'jsonl'.
    """
    records = _build_passphrases_json_output(credentials, show_secrets)
    # Intentional: output secrets for piping. Serialisation is shared with
    # every other app, the printing is not - see display_passphrase_json above
    for chunk in iter_records(records, output_format):
        print(chunk)  # noqa: T201  # lgtm[py/clear-text-logging-sensitive-data]
        sys.stdout.flush()


def display_passphrases_list(
    credentials, output_format, phabfive_instance, show_secrets=False
):
    """Display credentials list in the specified format (for search).

    Parameters
    ----------
    credentials : list
        List of credential dictionaries
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json', 'jsonl', 'value'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    show_secrets : bool
        Whether to show secret values (default: False for search)
    """
    console = phabfive_instance.get_console()

    try:
        if output_format in ("json", "jsonl"):
            display_passphrases_json(
                credentials, show_secrets, output_format=output_format
            )
        elif output_format in ("yaml", "strict"):
            display_passphrases_yaml(credentials, show_secrets)
        elif output_format == "tree":
            for cred in credentials:
                display_passphrase_tree(console, cred, phabfive_instance)
        elif output_format == "value":
            # Just output monograms for the value format
            for cred in credentials:
                print(cred.get("id", ""))
        else:
            # rich format (default) - YAML-like with Rich console
            for cred in credentials:
                display_passphrase_rich(console, cred, phabfive_instance)
    except BrokenPipeError:
        sys.stderr.close()
        sys.exit(0)
