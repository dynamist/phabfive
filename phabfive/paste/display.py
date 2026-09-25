# -*- coding: utf-8 -*-
"""Display functions for pastes.

A paste record is a plain nested dict, so it is rendered by the generic
renderers in ``phabfive.record_display``, the same ones that render a
project or a repository. What is left here is ``value``, the one format a
paste has something of its own to say for.
"""

from phabfive.display import display_empty, render_records
from phabfive.record_display import display_records


def _paste_value(record):
    """A paste's bare value: its content, or its monogram without one.

    The content is what a paste is for, so ``paste show`` pipes it
    straight into a file or a shell. A record without Content - every
    ``paste search`` result, and ``paste show --no-content`` - has none to
    give, and answers with its monogram instead, as ``passphrase search``
    does, so ``paste search | xargs paste show`` works. An empty paste has
    Content, just none of it, and prints nothing rather than a monogram
    that reads as content.
    """
    paste = record.get("Paste", {})

    if "Content" in paste:
        return paste["Content"] or None

    return record.get("_monogram")


def display_pastes(result, output_format, phabfive_instance, tabular=False):
    """Display `paste show` and `paste search` results.

    Parameters
    ----------
    result : dict
        Result from Paste.paste_show() or Paste.paste_search(), containing
        'pastes'
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json', 'jsonl', 'table' or 'value'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    tabular : bool, optional
        True from `paste search`, which is list-shaped. `paste show` leaves
        it False and gets rich for `--format=table`.
    """
    if not result:
        return

    if not result.get("pastes"):
        display_empty(output_format)
        return

    records = result["pastes"]

    def _values():
        for record in records:
            value = _paste_value(record)
            if value:
                print(value)

    # Every format but value is display_records' to switch on, so it is
    # handed the format as given and this switch only peels value off.
    render_records(
        output_format,
        {
            "value": _values,
            "rich": lambda: display_records(
                records, output_format, phabfive_instance, tabular=tabular
            ),
        },
    )
