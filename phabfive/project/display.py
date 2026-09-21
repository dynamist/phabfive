# -*- coding: utf-8 -*-
"""Display functions for projects.

A project record is a plain nested dict, so it is rendered by the generic
renderers in ``phabfive.record_display``, the same ones that render a
repository. What is left here is picking the records out of what ``Project``
answers with.
"""

from phabfive.record_display import display_records


def display_projects(result, output_format, phabfive_instance, tabular=False):
    """Display `project show` and `project search` results.

    Parameters
    ----------
    result : dict
        Result from Project.show() or Project.search(), containing
        'projects'
    output_format : str
        One of 'rich', 'tree', 'yaml', 'json', 'jsonl' or 'table'
    phabfive_instance : Phabfive
        Instance to access formatting helpers
    tabular : bool, optional
        True from `project search`, which is list-shaped. `project show`
        leaves it False and gets rich for `--format=table`.
    """
    if not result:
        return

    display_records(
        result.get("projects"), output_format, phabfive_instance, tabular=tabular
    )
