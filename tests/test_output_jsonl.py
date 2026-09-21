# -*- coding: utf-8 -*-
"""Tests for --format=jsonl, the newline-delimited JSON output format.

The rule every command is held to: parsing jsonl line by line must give
exactly what --format=json gives as an array. That pins the two formats
together, and it catches a dispatcher that was never taught about jsonl,
since such a dispatcher falls through to rich and emits lines that do not
parse as JSON at all.
"""

# python std lib
import json
import re
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app, preprocess_format_alias
from phabfive.constants import FORMAT_ALIASES, OutputFormat, VALIDATORS
from phabfive.core import Phabfive
from phabfive.json_output import emit_record, emit_records, iter_records
from phabfive.maniphest import Maniphest
from tests.conftest import CONF

runner = CliRunner()


def parse_jsonl(output):
    """Parse jsonl output into records, failing loudly on a bad line."""
    records = []
    for number, line in enumerate(output.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            pytest.fail(f"line {number} is not JSON: {error}\n{line!r}")
    return records


@pytest.fixture
def restore_output_format():
    """Put the global output format back, even when a test fails.

    Phabfive._output_format is class state, so a test that leaves it set
    changes what every later test renders.
    """
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


class TestFormatRegistration:
    """jsonl is a real format, ndjson is a spelling of it."""

    def test_jsonl_is_an_output_format(self):
        assert OutputFormat.jsonl.value == "jsonl"

    @pytest.mark.parametrize("alias", sorted(FORMAT_ALIASES))
    def test_an_alias_is_not_an_output_format(self, alias):
        # An alias is rewritten in argv instead, so no display code tests for
        # it. Alias xor enum member, never both - which is what makes a rename
        # like simple -> value safe to do by moving the spelling between them.
        assert alias not in [member.value for member in OutputFormat]

    def test_format_is_offered_by_the_cli(self):
        result = runner.invoke(app, ["--format=nope", "cache", "info"])
        assert result.exit_code != 0
        assert "jsonl" in result.output

    @pytest.mark.parametrize("value", ["yaml", "json", "jsonl", "ndjson"])
    def test_fallback_accepts_machine_readable_formats(self, value):
        assert re.match(VALIDATORS["PHAB_FALLBACK"], value)

    def test_table_is_an_output_format(self):
        assert OutputFormat.table.value == "table"

    def test_value_is_an_output_format(self):
        assert OutputFormat.value.value == "value"

    @pytest.mark.parametrize(
        "value", ["rich", "tree", "value", "simple", "table", "jsonlines", ""]
    )
    def test_fallback_rejects_everything_else(self, value):
        """A fallback is for a pipe. table cuts cells and drops columns, so
        it is a human format and deliberately not one of them - and neither
        is value, which prints one field with no way to tell which."""
        assert not re.match(VALIDATORS["PHAB_FALLBACK"], value)


class TestFormatAliases:
    """preprocess_format_alias() rewrites argv before Typer parses it.

    It runs in cli_entrypoint(), not in app(), so CliRunner never sees the
    rewrite and these tests call the function directly.
    """

    @pytest.mark.parametrize(("alias", "resolved"), sorted(FORMAT_ALIASES.items()))
    def test_joined_form_is_rewritten(self, alias, resolved):
        argv = ["phabfive", f"--format={alias}", "T123"]
        assert preprocess_format_alias(argv) == [
            "phabfive",
            f"--format={resolved}",
            "T123",
        ]

    @pytest.mark.parametrize(("alias", "resolved"), sorted(FORMAT_ALIASES.items()))
    def test_separate_form_is_rewritten(self, alias, resolved):
        argv = ["phabfive", "--format", alias, "T123"]
        assert preprocess_format_alias(argv) == [
            "phabfive",
            "--format",
            resolved,
            "T123",
        ]

    @pytest.mark.parametrize("value", ["json", "jsonl", "yaml", "rich"])
    def test_real_formats_are_left_alone(self, value):
        argv = ["phabfive", f"--format={value}", "T123"]
        assert preprocess_format_alias(argv) == argv

    def test_a_trailing_format_flag_does_not_crash(self):
        argv = ["phabfive", "--format"]
        assert preprocess_format_alias(argv) == argv

    def test_an_alias_as_a_positional_is_left_alone(self):
        # "ndjson" only means a format directly after --format
        argv = ["phabfive", "paste", "search", "ndjson"]
        assert preprocess_format_alias(argv) == argv

    def test_ndjson_reaches_the_cli_as_jsonl(self):
        argv = preprocess_format_alias(["phabfive", "--format=ndjson", "cache", "info"])
        result = runner.invoke(app, argv[1:])
        assert result.exit_code == 0
        assert len(parse_jsonl(result.stdout)) == 1


class TestFallbackFormat:
    """PHAB_FALLBACK picks the format when stdout is not a TTY."""

    def test_jsonl_is_used_when_stdout_is_not_a_tty(self, restore_output_format):
        with patch.object(Phabfive, "_fallback_format", "jsonl"):
            with patch("sys.stdout.isatty", return_value=False):
                assert Phabfive._get_auto_format() == "jsonl"

    def test_a_tty_still_gets_rich(self, restore_output_format):
        with patch.object(Phabfive, "_fallback_format", "jsonl"):
            with patch("sys.stdout.isatty", return_value=True):
                assert Phabfive._get_auto_format() == "rich"

    def test_ndjson_in_config_is_resolved_to_jsonl(self):
        """The command applies PHAB_FALLBACK when it builds its app."""
        from phabfive.cli.apps import new_app

        # CONF's token is not 32 characters, which the validator demands
        conf = dict(CONF, PHAB_TOKEN="api-" + "a" * 28, PHAB_FALLBACK="ndjson")
        with patch.object(Phabfive, "read_config", return_value=(conf, True)):
            with patch("phabfive.core.Phabricator"):
                new_app(Phabfive)
        assert Phabfive._fallback_format == "jsonl"
        Phabfive._fallback_format = "yaml"

    def test_constructing_leaves_the_fallback_alone(self):
        """A library constructing an instance does not set process-wide state."""
        conf = dict(CONF, PHAB_TOKEN="api-" + "a" * 28, PHAB_FALLBACK="jsonl")
        with patch.object(Phabfive, "read_config", return_value=(conf, True)):
            with patch("phabfive.core.Phabricator"):
                Phabfive()
        assert Phabfive._fallback_format == "yaml"


def _maniphest_with_two_tasks():
    """A Maniphest whose Conduit returns two tasks, one with a newline."""
    maniphest = Maniphest()
    maniphest.url = "https://phabricator.example.com"
    maniphest.conf = {"PHAB_SPACE": "S1"}
    maniphest.phab = MagicMock()
    maniphest.phab.phid.lookup.return_value = {
        "S1": {
            "phid": "PHID-SPCE-1",
            "name": "Global",
            "fullName": "Global",
            "uri": "/S1",
        }
    }
    mock_project_result = MagicMock()
    mock_project_result.get.return_value = {
        "PHID-PROJ-123": {
            "name": "Test Project",
            "slugs": ["test-project", "test_project"],
        }
    }
    maniphest.phab.project.query.return_value = mock_project_result
    mock_response = MagicMock()
    mock_response.response = {
        "data": [
            {
                "id": 1,
                "phid": "PHID-TASK-1",
                "fields": {
                    "name": "First task",
                    "status": {"name": "Open"},
                    "priority": {"name": "High", "value": 80},
                    # A multi-line description must be escaped into one line,
                    # not wrapped across several, or jsonl stops being jsonl
                    "description": {"raw": "line one\nline two\nline three"},
                    "dateCreated": 1234567890,
                    "dateModified": 1234567900,
                    "dateClosed": None,
                },
                "attachments": {"columns": {"boards": {}}},
            },
            {
                "id": 2,
                "phid": "PHID-TASK-2",
                "fields": {
                    "name": "Second task",
                    "status": {"name": "Resolved"},
                    "priority": {"name": "Normal", "value": 50},
                    "description": {"raw": ""},
                    "dateCreated": 1234567800,
                    "dateModified": 1234567850,
                    "dateClosed": 1234567900,
                },
                "attachments": {"columns": {"boards": {}}},
            },
        ]
    }
    mock_response.get.return_value = {"after": None}
    maniphest.phab.maniphest.search.return_value = mock_response
    return maniphest


class TestManiphestTasks:
    """maniphest show and search, the case issue #179 is written against."""

    @patch("phabfive.maniphest.core.Phabfive.__init__")
    def test_jsonl_matches_json(self, mock_init, capsys):
        from phabfive.display import display_tasks

        mock_init.return_value = None
        maniphest = _maniphest_with_two_tasks()
        result = maniphest.task_search(tag="Test Project")

        display_tasks(result, "json", maniphest)
        as_array = json.loads(capsys.readouterr().out)

        display_tasks(result, "jsonl", maniphest)
        as_lines = parse_jsonl(capsys.readouterr().out)

        assert as_lines == as_array
        assert len(as_lines) == 2

    @patch("phabfive.maniphest.core.Phabfive.__init__")
    def test_one_line_per_task(self, mock_init, capsys):
        from phabfive.display import display_tasks

        mock_init.return_value = None
        maniphest = _maniphest_with_two_tasks()
        result = maniphest.task_search(tag="Test Project")

        display_tasks(result, "jsonl", maniphest)
        output = capsys.readouterr().out

        # Two tasks, one of them with a three-line description
        assert len(output.splitlines()) == 2

    @patch("phabfive.maniphest.core.Phabfive.__init__")
    def test_multiline_description_stays_on_one_line(self, mock_init, capsys):
        from phabfive.display import display_tasks

        mock_init.return_value = None
        maniphest = _maniphest_with_two_tasks()
        result = maniphest.task_search(tag="Test Project")

        display_tasks(result, "jsonl", maniphest)
        records = parse_jsonl(capsys.readouterr().out)

        assert records[0]["Task"]["Description"] == "line one\nline two\nline three"

    @patch("phabfive.maniphest.core.Phabfive.__init__")
    def test_cli_dispatcher_also_routes_jsonl(self, mock_init, capsys):
        # phabfive/cli/maniphest.py keeps its own copy of the dispatcher
        from phabfive.cli.maniphest import _display_tasks

        mock_init.return_value = None
        maniphest = _maniphest_with_two_tasks()
        result = maniphest.task_search(tag="Test Project")

        _display_tasks(result, "jsonl", maniphest)
        records = parse_jsonl(capsys.readouterr().out)

        assert [record["Task"]["Name"] for record in records] == [
            "First task",
            "Second task",
        ]

    @patch("phabfive.maniphest.core.Phabfive.__init__")
    def test_no_description_is_honoured(self, mock_init, capsys):
        from phabfive.cli.maniphest import _display_tasks

        mock_init.return_value = None
        maniphest = _maniphest_with_two_tasks()
        result = maniphest.task_search(tag="Test Project")

        _display_tasks(result, "jsonl", maniphest, show_description=False)
        records = parse_jsonl(capsys.readouterr().out)

        assert "Description" not in records[0]["Task"]


class TestUsers:
    """user whoami."""

    def _users(self):
        return [
            {
                "Host": "phorge.example.com",
                "URL": "https://phorge.example.com/api/",
                "_base_url": "https://phorge.example.com",
                "User": {
                    "UserName": "alice",
                    "RealName": "Alice",
                    "PrimaryEmail": "alice@example.com",
                },
            },
            {
                "Host": "other.example.com",
                "URL": "https://other.example.com/api/",
                "Error": "not reachable",
            },
        ]

    def test_jsonl_matches_json(self, capsys):
        from phabfive.display import display_users

        instance = MagicMock()

        display_users(self._users(), "json", instance)
        as_array = json.loads(capsys.readouterr().out)

        display_users(self._users(), "jsonl", instance)
        as_lines = parse_jsonl(capsys.readouterr().out)

        assert as_lines == as_array
        assert len(as_lines) == 2


class TestPastes:
    """paste show and paste search."""

    def _pastes(self):
        return {
            "pastes": [
                {
                    "url": "https://phorge.example.com/P1",
                    "title": "nginx config",
                    "author": "alice",
                    "language": "nginx",
                    "status": "active",
                    "dateCreated": 1234567890,
                    "dateModified": 1234567900,
                    "content": "server {\n  listen 80;\n}",
                },
                {
                    "url": "https://phorge.example.com/P2",
                    "title": "notes",
                    "author": "bob",
                    "language": "text",
                    "status": "active",
                    "dateCreated": 1234567800,
                    "dateModified": 1234567850,
                },
            ]
        }

    def test_show_jsonl_matches_json(self, capsys):
        from phabfive.cli.paste import _display_pastes

        instance = MagicMock()

        _display_pastes(self._pastes(), "json", instance)
        as_array = json.loads(capsys.readouterr().out)

        _display_pastes(self._pastes(), "jsonl", instance)
        as_lines = parse_jsonl(capsys.readouterr().out)

        assert as_lines == as_array
        assert len(as_lines) == 2

    def test_show_multiline_content_stays_on_one_line(self, capsys):
        from phabfive.cli.paste import _display_pastes

        _display_pastes(self._pastes(), "jsonl", MagicMock())
        output = capsys.readouterr().out

        assert len(output.splitlines()) == 2
        assert parse_jsonl(output)[0]["Content"] == "server {\n  listen 80;\n}"

    def test_search_jsonl_matches_json(self):
        pastes = [
            {"id": 1, "fields": {"title": "nginx config"}},
            {"id": 2, "fields": {"title": "notes"}},
        ]
        instance = MagicMock()
        instance.get_pastes.return_value = pastes

        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            as_array = json.loads(
                runner.invoke(app, ["--format=json", "paste", "search", "x"]).stdout
            )
            as_lines = parse_jsonl(
                runner.invoke(app, ["--format=jsonl", "paste", "search", "x"]).stdout
            )

        assert as_lines == as_array
        assert len(as_lines) == 2


class TestPassphrases:
    """passphrase show and passphrase search."""

    def _credentials(self):
        return [
            {
                "url": "https://phorge.example.com/K1",
                "type": "Password",
                "name": "deploy",
                "username": "deployer",
                "secret": "hunter2",
                "dateCreated": 1234567890,
                "dateModified": 1234567900,
            },
            {
                "url": "https://phorge.example.com/K2",
                "type": "SSH Private Key",
                "name": "build key",
                "secret": "-----BEGIN-----\nkey\n-----END-----",
                "public_key": "ssh-rsa AAAA",
                "dateCreated": 1234567800,
                "dateModified": 1234567850,
            },
        ]

    def test_jsonl_matches_json(self, capsys):
        from phabfive.passphrase.display import display_passphrases

        instance = MagicMock()

        display_passphrases(self._credentials(), "json", instance)
        as_array = json.loads(capsys.readouterr().out)

        display_passphrases(self._credentials(), "jsonl", instance)
        as_lines = parse_jsonl(capsys.readouterr().out)

        assert as_lines == as_array
        assert len(as_lines) == 2

    def test_search_jsonl_matches_json(self, capsys):
        from phabfive.passphrase.display import display_passphrases_list

        instance = MagicMock()

        display_passphrases_list(self._credentials(), "json", instance)
        as_array = json.loads(capsys.readouterr().out)

        display_passphrases_list(self._credentials(), "jsonl", instance)
        as_lines = parse_jsonl(capsys.readouterr().out)

        assert as_lines == as_array
        # search hides secrets by default, and that must hold for jsonl too
        assert all("Secret" not in record for record in as_lines)

    def test_multiline_secret_stays_on_one_line(self, capsys):
        from phabfive.passphrase.display import display_passphrases

        display_passphrases(self._credentials(), "jsonl", MagicMock())
        output = capsys.readouterr().out

        assert len(output.splitlines()) == 2
        assert parse_jsonl(output)[1]["Secret"] == "-----BEGIN-----\nkey\n-----END-----"

    def test_a_closed_pipe_is_still_quiet(self):
        """Passphrase prints its own lines, so it needs its own handler.

        The print lives in passphrase/display.py rather than in the shared
        emitter, so that the clear-text-logging suppression covers only the
        code meant to write secrets. That means the shared emitter's own
        BrokenPipeError handling does not apply here.
        """
        from phabfive.passphrase.display import display_passphrases

        stdout = MagicMock()
        stdout.write.side_effect = BrokenPipeError(32, "Broken pipe")
        with patch("sys.stdout", stdout):
            with patch("sys.stderr"):
                with pytest.raises(SystemExit) as exit_info:
                    display_passphrases(self._credentials(), "jsonl", MagicMock())
        assert exit_info.value.code == 0

    def test_a_single_credential_has_the_same_shape_as_many(self, capsys):
        from phabfive.passphrase.display import (
            display_passphrase,
            display_passphrases,
        )

        instance = MagicMock()
        credential = self._credentials()[0]

        display_passphrase(credential, "jsonl", instance)
        [alone] = parse_jsonl(capsys.readouterr().out)

        display_passphrases([credential], "jsonl", instance)
        [among_others] = parse_jsonl(capsys.readouterr().out)

        assert alone == among_others


class TestCacheInfo:
    """cache info is a single top-level object, not a list."""

    def test_jsonl_is_one_line(self, enabled_cache):
        from phabfive import cache

        cache.set("users", "key", {"records": [], "truncated": False})
        result = runner.invoke(app, ["--format=jsonl", "cache", "info"])

        assert result.exit_code == 0
        assert len(result.stdout.splitlines()) == 1

    def test_jsonl_matches_json(self, enabled_cache):
        from phabfive import cache

        cache.set("users", "key", {"records": [], "truncated": False})

        as_object = json.loads(
            runner.invoke(app, ["--format=json", "cache", "info"]).stdout
        )
        [as_line] = parse_jsonl(
            runner.invoke(app, ["--format=jsonl", "cache", "info"]).stdout
        )

        assert as_line == as_object


class TestClosedPipe:
    """head closing the pipe is normal, not a traceback.

    jsonl flushes after every record, so it meets a closed pipe far more
    often than a single buffered write did. Neither emitter may let the
    BrokenPipeError escape.
    """

    def _closed_stdout(self):
        stdout = MagicMock()
        stdout.write.side_effect = BrokenPipeError(32, "Broken pipe")
        return stdout

    def test_records_exit_quietly(self):
        with patch("sys.stdout", self._closed_stdout()):
            with patch("sys.stderr"):
                with pytest.raises(SystemExit) as exit_info:
                    emit_records([{"a": 1}, {"a": 2}], "jsonl")
        assert exit_info.value.code == 0

    def test_a_single_record_exits_quietly(self):
        with patch("sys.stdout", self._closed_stdout()):
            with patch("sys.stderr"):
                with pytest.raises(SystemExit) as exit_info:
                    emit_record({"a": 1}, "jsonl")
        assert exit_info.value.code == 0

    def test_a_failing_flush_is_caught_too(self):
        stdout = MagicMock()
        stdout.flush.side_effect = BrokenPipeError(32, "Broken pipe")
        with patch("sys.stdout", stdout):
            with patch("sys.stderr"):
                with pytest.raises(SystemExit) as exit_info:
                    emit_records([{"a": 1}, {"a": 2}], "jsonl")
        assert exit_info.value.code == 0

    def test_json_is_covered_as_well(self):
        with patch("sys.stdout", self._closed_stdout()):
            with patch("sys.stderr"):
                with pytest.raises(SystemExit) as exit_info:
                    emit_records([{"a": 1}], "json")
        assert exit_info.value.code == 0


class TestSerialisationIsShared:
    """json and jsonl are decided in one place, even where printing is not."""

    def test_iter_records_yields_one_chunk_per_record_for_jsonl(self):
        chunks = list(iter_records([{"a": 1}, {"a": 2}], "jsonl"))
        assert [json.loads(chunk) for chunk in chunks] == [{"a": 1}, {"a": 2}]

    def test_iter_records_yields_one_array_for_json(self):
        [chunk] = list(iter_records([{"a": 1}, {"a": 2}], "json"))
        assert json.loads(chunk) == [{"a": 1}, {"a": 2}]

    def test_no_chunk_contains_a_newline_for_jsonl(self):
        records = [{"text": "one\ntwo\nthree"}]
        [chunk] = list(iter_records(records, "jsonl"))
        assert "\n" not in chunk
        assert json.loads(chunk)["text"] == "one\ntwo\nthree"
