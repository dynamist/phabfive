# -*- coding: utf-8 -*-
"""Display functions for Diffusion.

A repository record and a URI record are plain nested dicts, so they are
rendered by the generic renderers in ``phabfive.record_display``. What is left
here is picking the records out of what ``Diffusion`` answers with.
"""

from phabfive.record_display import (  # noqa: F401 - re-exported for callers
    _yaml_scalar,
    display_records,
    display_records_rich,
)


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
