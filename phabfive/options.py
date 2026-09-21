# -*- coding: utf-8 -*-
"""How an option that takes several values is read.

An option that takes a list of values is both repeatable and comma-separated:
``--member @a,@b --member @c`` names three members. That is the rule the
positional IDs of every ``show`` command already follow (``T1,T2`` or ``T1
T2``), and the one ``maniphest edit --subscribe`` followed on its own. It
lives here so that each new list option reads a value the same way, rather
than each command splitting it by hand.

A comma is safe to split on in every value it is used for: Phorge allows none
in a username, and normalises one out of a hashtag. ``+`` is not a separator
here - in a search filter it means AND, which a list of values to add has no
use for.
"""


def split_list_option(values):
    """Flatten a repeatable, comma-separated option into its values.

    Parameters
    ----------
    values : str, list or None
        What the option collected: None when it was not given, a string for
        a single occurrence, or a list with one string per occurrence

    Returns
    -------
    list
        Each value once, stripped, in the order first given. Empty values -
        a trailing comma, or ``--member ''`` - are dropped.

    Examples
    --------
    >>> split_list_option(["@a,@b", "@c"])
    ['@a', '@b', '@c']
    >>> split_list_option("x, y,,x")
    ['x', 'y']
    """
    if not values:
        return []

    if isinstance(values, str):
        values = [values]

    parts = (part.strip() for value in values for part in str(value).split(","))

    return list(dict.fromkeys(part for part in parts if part))


__all__ = ["split_list_option"]
