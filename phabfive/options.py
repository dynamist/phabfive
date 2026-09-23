# -*- coding: utf-8 -*-
"""How an option that takes several values is read.

An option that takes a list of values is both repeatable and comma-separated:
``--member @a,@b --member @c`` names three members. That is the rule the
positional IDs of every ``show`` command already follow (``T1,T2`` or ``T1
T2``). It lives here so that every list option that adds values - the
``project`` options, ``--tag`` and ``--subscribe`` on ``maniphest
create/edit`` and ``paste create/edit`` - reads a value the same way, rather
than each command splitting it by hand.

A comma is safe to split on in every value it is used for: Phorge allows none
in a username, and normalises one out of a hashtag. ``+`` is not a separator
here - in a search filter it means AND, which a list of values to add has no
use for. ``maniphest create`` still accepts it, with a warning, because it
once split on nothing else.
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


def value_list(value):
    """One filter's values, however a caller wrote them.

    ``"1,2"``, ``["1", "2"]`` and ``1`` are the three spellings a list
    filter accepts - a flag can only write the first, a spec may write any
    of them - so every ``*_id_list`` and ``*_name_list`` in the apps reads
    its argument through this one function rather than a copy of it.

    Unlike :func:`split_list_option` nothing is de-duplicated: a search
    constraint is a set on the server's side already, and keeping the order
    and the repeats is what makes a round trip through a spec comparable.

    Parameters
    ----------
    value : str, int, list, tuple or None
        What was written.

    Returns
    -------
    list
        The non-empty entries, stripped, in the order given. Empty for
        None, "" and a list holding nothing usable.

    Examples
    --------
    >>> value_list("P12, P13")
    ['P12', 'P13']
    >>> value_list([",", ""])
    []
    """
    if value is None or value == "":
        return []

    entries = value if isinstance(value, (list, tuple)) else [value]

    return [
        part.strip()
        for entry in entries
        for part in str(entry).split(",")
        if part.strip()
    ]


def any_list_value(*values):
    """Whether any list option carried something a filter can be built from.

    ``--ids=,`` is a list holding one empty entry: truthy as typer collected
    it, and empty once :func:`split_list_option` has read it. A "did you name
    any filter at all" guard that tests the raw option therefore lets a
    search through that then sends no constraint and reads the whole
    instance - the case the guard exists to prevent. So the guard asks this
    instead, which parses the value exactly as the constraint builder will.

    Parameters
    ----------
    *values
        What each option collected: None, a string, or a list of strings.

    Returns
    -------
    bool
        True when at least one of them holds a non-empty entry.

    Examples
    --------
    >>> any_list_value(None, [","])
    False
    >>> any_list_value(None, ["P12"])
    True
    """
    return any(split_list_option(value) for value in values)


__all__ = ["any_list_value", "split_list_option", "value_list"]
