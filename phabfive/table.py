# -*- coding: utf-8 -*-
"""The ``table`` format: one row per record, one column per field.

A table is list-shaped. It is the only format that has to *compress* - yaml
and json publish a record's nesting as it is, while a grid has one cell per
field and nowhere to put a subtree. So this module states one flattening
rule and applies it to whatever record it is handed, rather than carrying a
column list per command. ``repo list``, ``uri list``, ``maniphest search``
and ``paste search`` all reach it with nothing app-specific in between, and
an app added later needs no code here.

The rule, in five parts:

1. **Flatten.** A nested mapping contributes one column per scalar leaf, not
   a column of its own. ``Repository: {Name: ...}`` is a ``Name`` column.
2. **Collapse.** A list is one column, its items joined with ", ". A list of
   mappings contributes each item's *first* field, which is the identifying
   one every record builder here puts first - so ``URIs`` reads as the URIs
   and not as a wall of braces.
3. **Resolve.** A mapping that is one value spelled three ways - ``Raw``,
   ``Default`` and ``Effective``, which is how Phorge answers anything a
   record can inherit - is one column rather than three, spelling the value
   in force and saying where it came from: ``observe (set)`` against
   ``readwrite (default)``. Rule 1 would answer with six columns for a
   URI's I/O and display, which is the whole record's width spent on two
   fields.
4. **Name by the leaf.** A column is named by the last key on its path, and
   only grows leftwards - ``Infrastructure Column``, ``Security Column`` -
   when that would name two columns the same.
5. **Drop what says nothing.** Internal ``_`` keys never appear, a ``Link``
   becomes the row's hyperlink rather than a column of URLs, and a column
   that is empty in every row is left out entirely.

Cells are single-line and cut to :data:`CELL_WIDTH`, so one long description
cannot push every other column off the screen and a row is always one line -
which is what makes the output greppable. That is lossy on purpose, and why
``table`` is a human format: it is deliberately not accepted for
``PHAB_FALLBACK``, and anything being parsed should ask for json or yaml.

Narrowing a wide table is ``--columns``, which is #50 and not implemented.
"""

import shutil
import sys
from collections import Counter

from rich.table import Table
from rich.text import Text

#: How wide a cell is allowed to run before it is cut short. A repository
#: URI is comfortably over 40 characters and has to survive whole.
CELL_WIDTH = 60

#: Written when a value was cut. Plain ASCII, so ``--ascii`` needs no say.
ELLIPSIS = "..."

#: Blank columns between one column and the next.
PADDING = 2

#: How far a column may be shrunk to make the row fit. Below this there is
#: no value left to read, only ellipsis.
MIN_COLUMN = 6

#: The keys of a value that is set, inherited or in force - rule 3. Named
#: here rather than per command, so any record publishing this shape gets
#: one column for it without the table learning what the field means.
RESOLVED_KEYS = frozenset({"Raw", "Default", "Effective"})


def _is_empty(value):
    """Whether a value has nothing to say, for dropping empty columns.

    ``False`` and ``0`` say something; ``None``, ``""`` and ``[]`` do not.
    """
    if value is None:
        return True

    if isinstance(value, (str, list, tuple, dict)):
        return len(value) == 0

    return False


def _cut(text, width):
    """Cut text to width, saying so with :data:`ELLIPSIS`.

    The table does its own cutting rather than leaving it to Rich, which
    would spell it with a Unicode ellipsis that ``--ascii`` has no say
    over, and would spell it differently in a pipe than on a terminal.
    """
    if len(text) <= width:
        return text

    if width <= len(ELLIPSIS):
        return text[:width]

    return text[: width - len(ELLIPSIS)] + ELLIPSIS


def _cell(value):
    """Render one value as a single line of at most :data:`CELL_WIDTH`.

    Parameters
    ----------
    value : any
        Scalar or list, as :func:`_walk` leaves it

    Returns
    -------
    str
        One line, ending in :data:`ELLIPSIS` when anything was cut
    """
    if value is None:
        return ""

    # The YAML spelling, so a boolean reads the same in every format.
    if value is True:
        return "true"
    if value is False:
        return "false"

    if isinstance(value, (list, tuple)):
        text = ", ".join(_item(item) for item in value)
    else:
        text = str(value)

    lines = text.split("\n")

    # A row is one line, always. A description that is not is marked as cut
    # and left at its first line rather than breaking the grid.
    text = lines[0] + ELLIPSIS if len(lines) > 1 else lines[0]

    return _cut(text, CELL_WIDTH)


def _item(item):
    """One item of a list, as it appears inside that list's single cell."""
    if isinstance(item, dict):
        # The first field is the identifying one - the URI of a URI record -
        # once the Link is out of the way, the same as for a whole row.
        fields = {k: v for k, v in item.items() if k != "Link"}
        value = next(iter(fields.values()), "")

        return _item(value) if isinstance(value, dict) else str(value)

    return str(item)


def _resolved(section):
    """One cell for a set-or-inherited value, or None if that is not one.

    ``Raw`` is what is written down, which may be the literal "default";
    ``Effective`` is what that resolves to. The cell answers with the
    value in force and says in one word which of the two it came from, so
    that a grid does not have to choose between being wrong and being six
    columns wide.

    Parameters
    ----------
    section : dict
        A nested mapping, which may or may not be a resolved value

    Returns
    -------
    str or None
        The cell, or None when the mapping is something else and rule 1
        should flatten it as usual
    """
    if set(section) != RESOLVED_KEYS:
        return None

    value = section["Effective"]
    inherited = section["Raw"] in (None, "default")

    if value is None:
        return ""

    return f"{value} ({'default' if inherited else 'set'})"


def _walk(mapping, path, fields):
    """Collect the scalar leaves of a mapping, keyed by their path.

    A list is a leaf: it collapses into one cell rather than one column per
    item, because the number of items is a property of the row and columns
    are a property of the table.

    Parameters
    ----------
    mapping : dict
        The record, or a section of it
    path : tuple
        Keys walked to get here
    fields : dict
        Accumulator, path tuple to value
    """
    for key, value in mapping.items():
        if key.startswith("_"):
            continue

        if isinstance(value, dict) and value:
            resolved = _resolved(value)

            if resolved is None:
                _walk(value, path + (key,), fields)
            else:
                fields[path + (key,)] = resolved
        else:
            fields[path + (key,)] = value


def _label_columns(paths):
    """Name each column by as little of its path as stays unique.

    Parameters
    ----------
    paths : list
        Path tuples, in the order the columns appear

    Returns
    -------
    dict
        Path tuple to column name
    """
    depth = {path: 1 for path in paths}

    while True:
        labels = {path: " ".join(path[-depth[path] :]) for path in paths}
        clashes = Counter(labels.values())
        grew = False

        for path in paths:
            if clashes[labels[path]] > 1 and depth[path] < len(path):
                depth[path] += 1
                grew = True

        # Two distinct records can still spell the same full path, and then
        # there is nothing left to grow into. Two columns of one name beats
        # not answering at all.
        if not grew:
            return labels


def _link_of(record):
    """The row's hyperlink, if the record carries one.

    Every record builder in phabfive leads with a ``Link`` holding the
    object's page. A column of 40-character URLs helps nobody, so the table
    hangs it on the row's first cell instead.
    """
    url = record.get("Link")

    return url if isinstance(url, str) and "://" in url else None


def _fit(widths, limit):
    """Shrink the widest column first until the row fits.

    Proportional shrinking is what Rich would do left to itself, and it
    costs the short columns first - a `Monogram` disappears so that a
    description can keep 50 characters it does not need. Taking from
    whichever column is currently widest spends the whole budget on the one
    field that has room to lose it.

    Parameters
    ----------
    widths : list
        Natural column widths, in column order
    limit : int
        Total width the columns may occupy between them

    Returns
    -------
    list
        The same widths, reduced. A table with more columns than the
        terminal has room for is left overflowing rather than crushed.
    """
    widths = list(widths)

    while sum(widths) > limit and max(widths, default=0) > MIN_COLUMN:
        widths[widths.index(max(widths))] -= 1

    return widths


def build_table(records, max_width=None):
    """Build the Rich table for a list of already-public records.

    Parameters
    ----------
    records : list
        Display records with their internal keys already stripped, as
        every other format receives them
    max_width : int, optional
        Columns are fitted into this many characters when given, padding
        included. Left out, every cell keeps its full width.

    Returns
    -------
    Table
        A Rich table, headed and unboxed
    """
    paths = []
    rows = []

    for record in records:
        fields = {}
        _walk({k: v for k, v in record.items() if k != "Link"}, (), fields)

        for path in fields:
            if path not in paths:
                paths.append(path)

        rows.append((_link_of(record), fields))

    paths = [
        path
        for path in paths
        if any(not _is_empty(fields.get(path)) for _, fields in rows)
    ]
    labels = _label_columns(paths)
    cells = [[_cell(fields.get(path)) for path in paths] for _, fields in rows]

    widths = [
        max([len(labels[path])] + [len(row[column]) for row in cells])
        for column, path in enumerate(paths)
    ]

    if max_width is not None and paths:
        widths = _fit(widths, max_width - PADDING * len(paths))

    table = Table(box=None, pad_edge=False, show_edge=False, padding=(0, PADDING, 0, 0))

    for path, width in zip(paths, widths):
        table.add_column(
            _cut(labels[path], width), width=width, no_wrap=True, overflow="crop"
        )

    for (link, _), row in zip(rows, cells):
        row = [_cut(cell, width) for cell, width in zip(row, widths)]

        if link and row:
            row[0] = Text(row[0], style=f"link {link}")

        table.add_row(*row)

    return table


def display_records_table(console, records):
    """Print records as a table.

    The console phabfive hands out is 4096 columns wide, so that rich never
    soft-wraps a YAML line in half. A table wants the opposite: printed to
    a terminal it is fitted to it, so that a row stays one line and the
    grid survives. Piped, there is no width to fit and every cell arrives
    whole, which is what `--format=table | grep` needs.

    Parameters
    ----------
    console : Console
        Rich Console instance for output
    records : list
        Display records with their internal keys already stripped
    """
    max_width = shutil.get_terminal_size().columns if sys.stdout.isatty() else None
    table = build_table(records, max_width=max_width)

    if not table.columns:
        return

    console.print(table)
