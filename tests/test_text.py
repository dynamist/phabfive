"""Tests for phabfive.text: Remarkup marking and the shared line renderer."""

import json
from io import StringIO

from ruamel.yaml import YAML

from phabfive.text import CommentText, RemarkupText, remarkup, render_text


def _printed(markup):
    """What Rich prints for one line of markup."""
    from rich.console import Console

    console = Console(file=StringIO(), width=200, no_color=True)
    console.print(markup)
    return console.file.getvalue().rstrip("\n")


def _yaml(data):
    stream = StringIO()
    YAML().dump(data, stream)
    return stream.getvalue()


class TestRemarkup:
    def test_marks_text(self):
        assert isinstance(remarkup("**bold**"), RemarkupText)
        assert remarkup("**bold**") == "**bold**"

    def test_leaves_nothing_unmarked(self):
        assert remarkup("") == ""
        assert not isinstance(remarkup(""), RemarkupText)
        assert remarkup(None) is None

    def test_multi_line_is_a_block_in_yaml(self):
        assert _yaml({"Description": remarkup("one\n\n= two =")}) == (
            "Description: |-\n  one\n\n  = two =\n"
        )

    def test_one_line_is_a_plain_scalar_in_yaml(self):
        # As a plain string would be, so marking one changes nothing
        assert _yaml({"Description": remarkup("one line")}) == _yaml(
            {"Description": "one line"}
        )
        assert _yaml({"Description": remarkup("#tag: x")}) == _yaml(
            {"Description": "#tag: x"}
        )

    def test_is_a_plain_string_in_json(self):
        assert json.dumps({"d": remarkup("a\nb")}) == '{"d": "a\\nb"}'


class TestRenderText:
    def test_one_line_per_line(self):
        assert render_text("a\nb\n\nc") == ["a", "b", "", "c"]

    def test_prints_as_written(self):
        # Remarkup links, checkboxes and tags look like Rich markup
        text = "[[/T1 | x]]\n[ ] todo\n[bold]not bold[/bold]\n[/path]"

        assert [_printed(line) for line in render_text(text)] == text.split("\n")

    def test_takes_marked_and_plain_text_alike(self):
        assert render_text(remarkup("a\nb")) == render_text("a\nb")

    def test_width_cuts_a_long_line(self):
        assert render_text("x" * 61, width=60) == ["x" * 57 + "..."]
        assert render_text("x" * 60, width=60) == ["x" * 60]

    def test_width_counts_what_is_printed(self):
        # The cut comes before escaping, so an escape does not use it up
        (line,) = render_text("[bold]" + "x" * 54, width=60)

        assert _printed(line) == "[bold]" + "x" * 54


class TestCommentText:
    HEADER = "2026-01-01T00:00:00 [alice T1@2]"

    def test_is_the_string_every_format_prints(self):
        comment = CommentText(self.HEADER, "first\n[[/T2]]")

        assert comment == f"{self.HEADER} first\n[[/T2]]"
        assert comment.header == self.HEADER
        assert isinstance(comment.body, RemarkupText)

    def test_is_one_string_in_yaml_and_json(self):
        for text in ("one line", "first\nsecond"):
            comment = CommentText(self.HEADER, text)

            assert _yaml({"c": [comment]}) == _yaml({"c": [str(comment)]})
            assert json.dumps(comment) == json.dumps(str(comment))

    def test_survives_a_copy(self):
        import copy

        comment = copy.deepcopy(CommentText(self.HEADER, "a\nb"))

        assert (comment.header, comment.body) == (self.HEADER, "a\nb")
        assert isinstance(comment.body, RemarkupText)

    def test_an_empty_body(self):
        comment = CommentText(self.HEADER, "")

        assert render_text(comment) == render_text(str(comment))

    def test_renders_as_the_whole_string_would(self):
        # The header's [alice T1@2] and the body's Remarkup are each escaped
        for text in ("one line", "first\n[ ] todo\n[[/T2 | x]]", "[bold]x[/bold]"):
            comment = CommentText(self.HEADER, text)

            assert render_text(comment) == render_text(str(comment))
            assert render_text(comment, width=30) == render_text(str(comment), width=30)
            printed = [_printed(line) for line in render_text(comment)]
            assert printed == str(comment).split("\n")

    def test_renders_its_body_as_its_own_text(self, monkeypatch):
        # The hook: what the body renders to is what the comment shows
        import phabfive.text as text

        real = text.render_text

        def fake(value, width=None):
            if isinstance(value, RemarkupText):
                return ["RENDERED"]
            return real(value, width)

        monkeypatch.setattr(text, "render_text", fake)

        # The header as written, escaped for Rich; the body as rendered
        assert text.render_text(CommentText(self.HEADER, "**x**")) == [
            "2026-01-01T00:00:00 \\[alice T1@2] RENDERED"
        ]


class TestFormattersMarkRemarkup:
    def test_project_description(self):
        from phabfive.project.formatters import build_project_display_data

        project = {
            "id": 1,
            "phid": "PHID-PROJ-1",
            "fields": {"name": "Dev", "description": "one\ntwo"},
        }
        (record,) = build_project_display_data(
            "http://phorge.localhost", lambda url, text: url, [project]
        )

        assert isinstance(record["Project"]["Description"], RemarkupText)


class TestBuildComments:
    def test_each_comment_keeps_its_header_and_body_apart(self):
        from unittest.mock import MagicMock

        from phabfive.maniphest.formatters import build_comments

        phab = MagicMock()
        phab.user.search.return_value = {
            "data": [{"phid": "PHID-USER-a", "fields": {"username": "alice"}}]
        }
        transactions = [
            {"id": 3, "authorPHID": "PHID-USER-a", "text": "b\nc", "dateCreated": 2},
            {"id": 2, "authorPHID": "PHID-USER-a", "text": "a", "dateCreated": 1},
        ]

        first, second = build_comments(phab, transactions, task_id=1)

        assert isinstance(first, CommentText)
        assert first.header.endswith(" [alice T1@2]")
        assert (first.body, second.body) == ("a", "b\nc")
        assert second == f"{second.header} b\nc"


class TestTaskPrinters:
    """A multi-line comment, which no task in the real-output comparison had."""

    TASK = {
        "_link": "https://p.test/T1",
        "_url": "https://p.test/T1",
        "Task": {"Name": "x", "Description": remarkup("[ ] first\nsecond")},
        "Comments": [
            CommentText("2026-01-01 [alice T1@2]", "[[/T2]]\nmore"),
            CommentText("2026-01-02 [bob T1@3]", "x" * 80),
        ],
    }

    def _print(self, printer):
        from unittest.mock import MagicMock

        from rich.console import Console

        console = Console(file=StringIO(), width=200, no_color=True)
        printer(console, self.TASK, MagicMock())
        return console.file.getvalue()

    def test_rich(self):
        from phabfive.display import _display_task_rich

        out = self._print(_display_task_rich)

        assert "    Description: |-\n      [ ] first\n      second\n" in out
        assert "    - 2026-01-01 [alice T1@2] [[/T2]]\n      more\n" in out
        assert f"    - 2026-01-02 [bob T1@3] {'x' * 80}\n" in out

    def test_tree(self):
        from phabfive.display import _display_task_tree

        out = self._print(_display_task_tree)

        assert "Description: [ ] first" in out
        assert "2026-01-01 [alice T1@2] [[/T2]]" in out
        # A one-line comment is shown whole; only a multi-line one is cut
        assert f"2026-01-02 [bob T1@3] {'x' * 80}" in out
