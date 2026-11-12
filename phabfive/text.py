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

__all__ = ["CommentText", "RemarkupText", "remarkup", "render_text"]


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


class CommentText(PreservedScalarString):
    """A comment as phabfive prints it: a header, then its Remarkup body.

    The string is ``"<header> <body>"``, the one line or block every
    format prints. The parts stay apart, so :func:`render_text` can print
    the header as it is and render the body as Remarkup.

    Parameters
    ----------
    header : str
        Who wrote it and when, with its reference, e.g.
        ``2026-01-09T05:15:20 [admin T5@93]``
    body : str
        The comment's text, as Phorge stores it
    """

    __slots__ = ("header", "body")

    def __new__(cls, header, body):
        comment = super().__new__(cls, f"{header} {body}")
        comment.header = header
        comment.body = remarkup(body)
        return comment

    def __getnewargs__(self):
        # copy and pickle rebuild a str subclass from its arguments
        return (self.header, str(self.body))


def _represent_remarkup(representer, data):
    """yaml for a RemarkupText: a block when it has several lines."""
    if "\n" in data:
        return representer.represent_preserved_scalarstring(data)

    return representer.represent_str(str(data))


# ruamel looks representers up by exact type, so a subclass needs its own.
# Registered on the class, every YAML() phabfive creates has it.
RoundTripRepresenter.add_representer(RemarkupText, _represent_remarkup)
# A comment prints as one string in yaml, the way it always has
RoundTripRepresenter.add_representer(
    CommentText, lambda representer, data: representer.represent_str(str(data))
)


def render_text(value, width=None):
    """The lines of a value, ready for Rich to print.

    The printers lay the lines out: as a ``Key: |-`` block, a list item
    or tree nodes. This only decides what each line says. It takes the
    value itself rather than ``str(value)``, which would drop the
    :class:`RemarkupText` and :class:`CommentText` markers.

    Parameters
    ----------
    value : str
        Text with one or more lines: a :class:`RemarkupText`, a
        :class:`CommentText` or plain text
    width : int, optional
        For a preview: cut a longer line to this many characters, the last
        three of them "...". The cut is made before escaping, so it counts
        what is shown, and a preview prints the text as written.

    Returns
    -------
    list of str
        One Rich markup string per line, escaped so the text prints as
        written
    """
    # Rich is for the printers; the formatters that import this module
    # are library code, which stays off it
    from rich.markup import escape

    if width is not None:
        return [
            escape(line if len(line) <= width else line[: width - 3] + "...")
            for line in _lines(value)
        ]

    if isinstance(value, CommentText):
        # The header as it is, the body on the same first line after it
        first, *rest = render_text(value.body)
        return [f"{escape(value.header)} {first}", *rest]

    return [escape(line) for line in _lines(value)]


def _lines(value):
    """The lines of `value`; one line when it has no newline in it."""
    text = str(value)
    return text.splitlines() if "\n" in text else [text]
