# -*- coding: utf-8 -*-

"""The time grammar a spec and a search flag both speak.

``created-after: 7d`` in a spec and ``--created-after 7d`` on the command
line are the same value, so they have to be read by the same parser. This is
that parser, moved out of ``phabfive/maniphest/utils.py`` for one reason:
``phabfive.spec.validate`` has to read a time offline, and importing
``phabfive.maniphest`` would pull in ``phabfive.core`` and the ``phabricator``
client onto the one code path whose whole claim is that it needs neither.

Standard library only, exactly like ``phabfive.spec.registry``. It moved
out of ``phabfive.maniphest.utils`` rather than being re-exported from it,
the same way the Jinja2 engine did in #471: two import paths for one
function is how a grammar ends up with two readers that disagree.

The grammar, said once::

    time := <number> [ <unit> ]
    unit := h | d | w | m | y        (case-insensitive; d when omitted)

``phabfive.spec.schema.TIME_PATTERN`` publishes that grammar as a regular
expression for editors and other tools. It is deliberately narrower than
this parser, which also accepts whatever :func:`float` happens to read -
``1e3``, ``nan``, ``1_0``. Those are accidents of the backward-compatible
"a bare number is days" branch, not part of the format, so the published
pattern does not bless them; what matters is that nothing phabfive would
accept is ever *reported* as a bad time, which is why the offline pass calls
this function rather than matching that pattern.
"""

import re

from phabfive.exceptions import PhabfiveInputException

__all__ = [
    "TIME_UNITS",
    "parse_time_with_unit",
]

#: Each unit suffix and what one of it is worth in days. Months and years are
#: the approximations the search has always used.
TIME_UNITS = {
    "h": 1 / 24,  # hours to days
    "d": 1,  # days
    "w": 7,  # weeks to days
    "m": 30,  # months to days (approximate)
    "y": 365,  # years to days (approximate)
}

_WITH_UNIT = re.compile(r"^(-?[0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)$")


def parse_time_with_unit(time_value):
    """
    Parse time value with optional unit suffix.

    Supports the following time units:
    - h: hours
    - d: days (default when no unit specified)
    - w: weeks (7 days)
    - m: months (30 days)
    - y: years (365 days)

    Parameters
    ----------
    time_value : str, int, float, or None
        Time value with optional unit suffix.
        Examples: "1w", "2m", "7d", "7", 7

    Returns
    -------
    float or None
        Number of days as a float, or None if input is None.

    Raises
    ------
    PhabfiveInputException
        If the format is invalid, the unit is not recognized, or the value is
        negative.

    Examples
    --------
    >>> parse_time_with_unit("1w")
    7.0
    >>> parse_time_with_unit("2m")
    60.0
    >>> parse_time_with_unit("7")
    7.0
    >>> parse_time_with_unit(7)
    7.0
    >>> parse_time_with_unit("1h")
    0.041666666666666664
    """
    if time_value is None:
        return None

    # Convert to string for parsing
    time_str = str(time_value).strip()

    if not time_str:
        raise PhabfiveInputException("Time value cannot be empty")

    # Try to parse as a plain number first (backward compatibility)
    try:
        # If it's just a number, treat as days
        days = float(time_str)
        if days < 0:
            raise PhabfiveInputException(
                f"Time value cannot be negative: '{time_value}'"
            )
        return days
    except ValueError as e:
        # If it's a negative value error, re-raise it
        if "cannot be negative" in str(e):
            raise
        # Not a plain number, try to parse with unit suffix
        pass

    # Extract numeric part and unit suffix
    match = _WITH_UNIT.match(time_str)
    if not match:
        raise PhabfiveInputException(
            f"Invalid time format: '{time_value}'. "
            f"Expected format: NUMBER[UNIT] where UNIT is one of: h, d, w, m, y. "
            f"Examples: '7d', '1w', '2m', '1y', '12h', or just '7' (defaults to days)"
        )

    numeric_part = match.group(1)
    unit = match.group(2).lower()

    if unit not in TIME_UNITS:
        valid_units = ", ".join(sorted(TIME_UNITS.keys()))
        raise PhabfiveInputException(
            f"Invalid time unit: '{unit}'. Valid units are: {valid_units}"
        )

    try:
        numeric_value = float(numeric_part)
    except ValueError:
        raise PhabfiveInputException(f"Invalid numeric value: '{numeric_part}'")

    if numeric_value < 0:
        raise PhabfiveInputException(f"Time value cannot be negative: '{time_value}'")

    # Convert to days
    days = numeric_value * TIME_UNITS[unit]
    return days
