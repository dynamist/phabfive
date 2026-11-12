"""Text that Phorge stores as Remarkup, and how phabfive prints text.

A formatter marks a value as Remarkup with :func:`remarkup`, so the
printers know what kind of text they hold rather than only that it has
several lines. The task and record printers turn multi-line text into lines
through :func:`render_text`, the one place that would render Remarkup
for a human. Today it prints the source as it is.

Only the human formats (rich and tree) go through :func:`render_text`.
yaml and json publish the source, which is what an agent reads and what
``apply`` reads back.
"""

from ruamel.yaml.representer import RoundTripRepresenter
from ruamel.yaml.scalarstring import PreservedScalarString

__all__ = ["RemarkupText", "remarkup", "render_text"]


class RemarkupText(PreservedScalarString):
    """A string that Phorge stores as Remarkup.

    A task's, project's or repository's description, for example. It is
    a :class:`str`, so json and every string operation see the plain
    text. In yaml it is a block scalar when it has several lines, and a
    plain scalar when it has one.
    """

    __slots__ = ()


def remarkup(text):
    """Mark `text` as Remarkup.

    Parameters
    ----------
    text : str or None
        Text as Phorge stores it

    Returns
    -------
    RemarkupText or str or None
        The text as :class:`RemarkupText`, or `text` unchanged when it is
        empty or None, which have nothing to render
    """
    return RemarkupText(text) if text else text


def _represent_remarkup(representer, data):
    """yaml for a RemarkupText: a block when it has several lines."""
    if "\n" in data:
        return representer.represent_preserved_scalarstring(data)

    return representer.represent_str(str(data))


# ruamel looks representers up by exact type, so a subclass needs its own.
# Registered on the class, every YAML() phabfive creates has it.
RoundTripRepresenter.add_representer(RemarkupText, _represent_remarkup)


def render_text(value, width=None):
    """The lines of a multi-line value, ready for Rich to print.

    The printers lay the lines out: as a ``Key: |-`` block, a list item
    or tree nodes. This only decides what each line says. It takes the
    value itself rather than ``str(value)``, which would drop the
    :class:`RemarkupText` marker.

    Parameters
    ----------
    value : str
        Text with one or more lines; a :class:`RemarkupText` or not
    width : int, optional
        Cut a longer line to this many characters, the last three of them
        "...". The cut is made before escaping, so it counts what is shown.

    Returns
    -------
    list of str
        One Rich markup string per line, escaped so the text prints as
        written
    """
    # Rich is for the printers; the formatters that import this module
    # are library code, which stays off it
    from rich.markup import escape

    lines = str(value).splitlines()

    if width is not None:
        lines = [
            line if len(line) <= width else line[: width - 3] + "..." for line in lines
        ]

    return [escape(line) for line in lines]
