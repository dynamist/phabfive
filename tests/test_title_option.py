# -*- coding: utf-8 -*-

"""`--title` on `maniphest create/edit` and `paste create/edit` (#580).

The flag was hidden, so `--help` never said a task could be renamed, and the
positional title silently won when both were given. It is visible now, with
`-t`, and a positional title next to a different `--title` is refused.
"""

import re
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app as main_app
from phabfive.cli.editor import merge_title
from phabfive.cli.maniphest import maniphest_app
from phabfive.cli.paste import paste_app

runner = CliRunner()


def _output(result):
    """Combined stdout/stderr regardless of click version."""
    output = result.output
    try:
        output += result.stderr
    except (ValueError, AttributeError):
        pass
    return output


class TestMergeTitle:
    @pytest.mark.parametrize(
        "positional, option, expected",
        [
            (None, None, None),
            ("A", None, "A"),
            (None, "A", "A"),
            ("A", "A", "A"),
            ("", "A", "A"),
        ],
    )
    def test_one_title_or_two_that_agree(self, positional, option, expected):
        assert merge_title(positional, option) == expected

    def test_two_different_titles_are_refused(self):
        with pytest.raises(ValueError, match="TITLE 'A' and --title 'B' differ"):
            merge_title("A", "B")


class TestManiphestEdit:
    def _invoke(self, args):
        with (
            patch("phabfive.cli.maniphest._get_edit_app", return_value=MagicMock()),
            patch("phabfive.cli.edit_flow.run_edit", return_value=0) as run_edit,
        ):
            result = runner.invoke(maniphest_app, ["edit", *args])
        return result, run_edit

    def test_a_monogram_title_renames_the_one_task(self):
        result, run_edit = self._invoke(["T5", "--title=T6"])

        assert result.exit_code == 0, _output(result)
        kwargs = run_edit.call_args.kwargs
        assert kwargs["object_id"] == "T5"
        assert kwargs["title"] == "T6"

    def test_short_flag(self):
        result, run_edit = self._invoke(["T5", "-t", "New"])

        assert result.exit_code == 0, _output(result)
        assert run_edit.call_args.kwargs["title"] == "New"

    def test_the_same_title_both_ways_is_accepted(self):
        result, run_edit = self._invoke(["T5", "New", "--title=New"])

        assert result.exit_code == 0, _output(result)
        assert run_edit.call_args.kwargs["title"] == "New"

    def test_a_differing_title_is_refused(self):
        result, run_edit = self._invoke(["T5", "Old", "--title=New"])

        assert result.exit_code == 1
        assert "TITLE 'Old' and --title 'New' differ" in _output(result)
        run_edit.assert_not_called()


def test_phabfive_edit_takes_the_title_option_before_the_monogram():
    """`edit --title=X T5` is not routed to `maniphest edit`, so it is its own."""
    with (
        patch("phabfive.cli.edit._get_edit_app", return_value=MagicMock()),
        patch("phabfive.cli.edit_flow.run_edit", return_value=0) as run_edit,
    ):
        result = runner.invoke(main_app, ["edit", "-t", "T6", "T5"])

    assert result.exit_code == 0, _output(result)
    assert run_edit.call_args.kwargs["object_id"] == "T5"
    assert run_edit.call_args.kwargs["title"] == "T6"


class TestManiphestCreate:
    def _invoke(self, args):
        instance = MagicMock()
        instance.create_task.return_value = {"dry_run": True, "title": "x"}
        with patch(
            "phabfive.cli.maniphest._get_maniphest_app", return_value=instance
        ) as get_app:
            result = runner.invoke(
                maniphest_app, ["create", "--description=x", "--dry-run", *args]
            )
        return result, instance, get_app

    def test_title_option_alone(self):
        result, instance, _ = self._invoke(["-t", "New"])

        assert result.exit_code == 0, _output(result)
        assert instance.create_task.call_args.kwargs["title"] == "New"

    def test_a_differing_title_is_refused_before_connecting(self):
        result, instance, get_app = self._invoke(["Old", "--title=New"])

        assert result.exit_code == 1
        assert "TITLE 'Old' and --title 'New' differ" in _output(result)
        get_app.assert_not_called()


def _a_paste():
    instance = MagicMock()
    instance.create_paste_from_content.return_value = {"id": 42}
    instance.get_paste_url.return_value = "https://example.com/P42"
    instance.get_paste_for_edit.return_value = {
        "id": 42,
        "phid": "PHID-PSTE-42",
        "title": "Notes",
        "content": "hello",
    }
    return instance


class TestPasteCreate:
    def _invoke(self, args):
        instance = _a_paste()
        with patch(
            "phabfive.cli.paste._get_paste_app", return_value=instance
        ) as get_app:
            result = runner.invoke(paste_app, ["create", "--yes", *args])
        return result, instance, get_app

    def test_title_option_with_content(self):
        result, instance, _ = self._invoke(["-t", "Notes", "--content=hello"])

        assert result.exit_code == 0, _output(result)
        kwargs = instance.create_paste_from_content.call_args.kwargs
        assert kwargs["title"] == "Notes"

    def test_with_title_option_the_lone_positional_is_the_file(self, tmp_path):
        script = tmp_path / "script.py"
        script.write_text("print(1)\n")

        result, instance, _ = self._invoke(["--title=My Script", str(script)])

        assert result.exit_code == 0, _output(result)
        kwargs = instance.create_paste_from_content.call_args.kwargs
        assert kwargs["title"] == "My Script"
        assert kwargs["content"] == "print(1)\n"

    def test_a_differing_title_is_refused_before_connecting(self, tmp_path):
        script = tmp_path / "script.py"
        script.write_text("print(1)\n")

        result, _, get_app = self._invoke(["Old", str(script), "--title=New"])

        assert result.exit_code == 1
        assert "TITLE 'Old' and --title 'New' differ" in _output(result)
        get_app.assert_not_called()


class TestPasteEdit:
    def _invoke(self, args):
        instance = _a_paste()
        with patch(
            "phabfive.cli.paste._get_paste_app", return_value=instance
        ) as get_app:
            result = runner.invoke(paste_app, ["edit", "P42", *args])
        return result, instance, get_app

    def test_title_option_renames(self):
        result, instance, _ = self._invoke(["-t", "Renamed"])

        assert result.exit_code == 0, _output(result)
        assert instance.edit_paste.call_args.kwargs["title"] == "Renamed"

    def test_a_differing_title_is_refused_before_connecting(self):
        result, instance, get_app = self._invoke(["Old", "--title=New"])

        assert result.exit_code == 1
        assert "TITLE 'Old' and --title 'New' differ" in _output(result)
        get_app.assert_not_called()


@pytest.mark.parametrize(
    "app, command",
    [
        (main_app, ["edit"]),
        (maniphest_app, ["create"]),
        (maniphest_app, ["edit"]),
        (paste_app, ["create"]),
        (paste_app, ["edit"]),
    ],
)
def test_title_shows_in_help(app, command):
    result = runner.invoke(app, [*command, "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    assert "--title" in result.output
    # The short flag on its own, not the "-t" inside "--title"
    assert re.search(r"(?<!-)-t\b", result.output)
