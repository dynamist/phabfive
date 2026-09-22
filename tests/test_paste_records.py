# -*- coding: utf-8 -*-

"""Tests for the paste record (#450).

`paste search` emitted {"id", "title"} and `paste show` a flat
{"Link", "Name", ...}, so the same paste had two shapes and neither matched
a task's or a project's. Both now emit one record, built once by
build_paste_display_data: Link, a nested Paste section and a top-level
Space, with Content in the Paste section only when `show` fetched it.
"""

import json
from io import StringIO
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from phabfive.exceptions import PhabfiveAPIException
from phabfive.paste import Paste
from phabfive.paste.display import display_pastes

AUTHOR = "PHID-USER-alice"
SPACE = "PHID-SPCE-default"


def _item(id_=1, content="hello"):
    return {
        "id": id_,
        "phid": f"PHID-PSTE-{id_}",
        "fields": {
            "title": f"paste {id_}",
            "authorPHID": AUTHOR,
            "language": "",
            "status": "active",
            "spacePHID": SPACE,
            "dateCreated": 1234567890,
            "dateModified": 1234567900,
        },
        "attachments": {"content": {"content": content}},
    }


def _paste_app(items):
    paste = Paste.__new__(Paste)
    paste.url = "https://phorge.example.com"
    paste.phab = MagicMock()
    paste.phab.paste.search.return_value = {
        "data": items,
        "cursor": {"after": None},
    }
    paste.phab.phid.query.return_value = {
        AUTHOR: {"name": "alice", "fullName": "alice (Alice)"},
        SPACE: {"name": "S1", "fullName": "S1 Default"},
    }

    return paste


class TestRecordShape:
    def test_show_nests_the_fields_under_paste(self):
        paste = _paste_app([_item()])

        [record] = paste.paste_show([1])["pastes"]

        assert record["_url"] == "https://phorge.example.com/P1"
        assert record["Paste"] == {
            "Name": "paste 1",
            "Author": "alice",
            "Language": "text",
            "Status": "active",
            "Created": record["Paste"]["Created"],
            "Modified": record["Paste"]["Modified"],
            "Content": "hello",
        }
        assert record["Space"] == "S1 Default"

    def test_search_is_show_without_the_content(self):
        paste = _paste_app([_item()])

        [shown] = paste.paste_show([1])["pastes"]
        [found] = paste.paste_search()["pastes"]

        del shown["Paste"]["Content"]
        assert found == shown

    def test_no_content_leaves_the_key_out(self):
        paste = _paste_app([_item()])

        [record] = paste.paste_show([1], show_content=False)["pastes"]

        assert "Content" not in record["Paste"]

    def test_authors_and_spaces_are_named_in_one_lookup(self):
        paste = _paste_app([_item(1), _item(2), _item(3)])

        paste.paste_search()

        paste.phab.phid.query.assert_called_once_with(phids=sorted([AUTHOR, SPACE]))

    def test_a_failed_lookup_leaves_the_phids(self):
        paste = _paste_app([_item()])
        paste.phab.phid.query.side_effect = PhabfiveAPIException("ERR", "nope")

        [record] = paste.paste_search()["pastes"]

        assert record["Paste"]["Author"] == AUTHOR
        assert record["Space"] == SPACE

    def test_no_space_leaves_the_key_out(self):
        item = _item()
        del item["fields"]["spacePHID"]
        paste = _paste_app([item])

        [record] = paste.paste_search()["pastes"]

        assert "Space" not in record


class TestEveryFormatPublishesTheSameRecord:
    def _result(self):
        return _paste_app([_item(1, "line one\nline two")]).paste_show([1])

    def test_yaml_and_json_agree(self, capsys):
        display_pastes(self._result(), "json", MagicMock())
        as_json = json.loads(capsys.readouterr().out)

        display_pastes(self._result(), "yaml", MagicMock())
        as_yaml = YAML(typ="safe").load(StringIO(capsys.readouterr().out))

        assert as_yaml == as_json
        assert list(as_json[0]) == ["Link", "Paste", "Space"]
        assert as_json[0]["Paste"]["Content"] == "line one\nline two"

    def test_multiline_content_is_a_yaml_block(self, capsys):
        display_pastes(self._result(), "yaml", MagicMock())

        assert "Content: |-\n" in capsys.readouterr().out


class TestTreeShowsTheContent:
    """A block scalar's first line is only `|`, which is all tree showed."""

    def _tree(self, content, capsys):
        from rich.console import Console

        instance = MagicMock()
        instance.get_console.return_value = Console(
            force_terminal=False, no_color=True, width=400
        )
        result = _paste_app([_item(1, content)]).paste_show([1])

        display_pastes(result, "tree", instance)

        return capsys.readouterr().out

    def test_multiline_content_prints_its_lines(self, capsys):
        out = self._tree("line1\nline2 [bold]x[/bold]\n  indented\n", capsys)

        assert "Content: |" not in out
        assert "line1" in out
        # Markup in a paste is the paste's, not rich's to interpret.
        assert "line2 [bold]x[/bold]" in out
        assert "  indented" in out

    def test_long_content_is_cut_after_five_lines(self, capsys):
        out = self._tree("\n".join(f"line{n}" for n in range(1, 8)), capsys)

        assert "line5" in out
        assert "line6" not in out
        assert "..." in out

    def test_single_line_content_stays_on_its_node(self, capsys):
        assert "Content: hello" in self._tree("hello", capsys)
