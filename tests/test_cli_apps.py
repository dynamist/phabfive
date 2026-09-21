# -*- coding: utf-8 -*-

"""phabfive.cli.apps is how every command builds the app it runs."""

from unittest import mock

import pytest
import typer

from phabfive.cli.apps import get_app, new_app, prompt_host
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveConfigException,
    PhabfiveConnectionException,
    PhabfiveDataException,
)


class TestNewApp:
    def test_the_command_verifies_the_connection(self):
        """A library default is no request; the command wants to fail fast."""
        cls = mock.MagicMock()

        new_app(cls)

        cls.assert_called_once_with(verify=True, select_host=prompt_host)


class TestPromptHost:
    def test_no_terminal_declines(self):
        """Declining leaves core to explain how to choose; nothing blocks."""
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            assert prompt_host(["https://a/api/", "https://b/api/"]) is None


class TestGetApp:
    def test_returns_the_instance(self):
        cls = mock.MagicMock()

        assert get_app(cls) is cls.return_value

    def test_a_configuration_problem_offers_setup_then_retries(self):
        app = mock.MagicMock()
        cls = mock.MagicMock(side_effect=[PhabfiveConfigException("nope"), app])

        with mock.patch("phabfive.setup.offer_setup_on_error", return_value=True):
            assert get_app(cls) is app

        assert cls.call_count == 2

    def test_declined_setup_exits_1(self):
        cls = mock.MagicMock(side_effect=PhabfiveConfigException("nope"))

        with mock.patch("phabfive.setup.offer_setup_on_error", return_value=False):
            with pytest.raises(typer.Exit) as exit_info:
                get_app(cls)

        assert exit_info.value.exit_code == 1

    def test_an_unreachable_host_is_one_line(self, capsys):
        cls = mock.MagicMock(side_effect=PhabfiveConnectionException("down"))

        with pytest.raises(typer.Exit) as exit_info:
            get_app(cls)

        assert exit_info.value.exit_code == 1
        assert capsys.readouterr().err == (
            "Error: Failed to connect to Phabricator API: down\n"
        )


class TestEntrypoint:
    """An error nothing else caught is one line, not a traceback."""

    def _run(self, error, monkeypatch, capsys):
        import phabfive.cli as cli

        monkeypatch.setattr(cli, "app", mock.MagicMock(side_effect=error))
        monkeypatch.setattr("sys.argv", ["phabfive", "maniphest", "show", "T1"])
        # SystemExit, not typer.Exit: outside click's main loop nothing
        # would turn a typer.Exit into an exit status
        with pytest.raises(SystemExit) as exit_info:
            cli.cli_entrypoint()
        return exit_info.value.code, capsys.readouterr().err

    def test_an_api_error(self, monkeypatch, capsys):
        error = PhabfiveAPIException("ERR-INVALID-AUTH", "API token is not valid.")

        code, err = self._run(error, monkeypatch, capsys)

        assert code == 1
        assert err == "Error: ERR-INVALID-AUTH: API token is not valid.\n"

    def test_a_connection_error(self, monkeypatch, capsys):
        code, err = self._run(
            PhabfiveConnectionException("refused"), monkeypatch, capsys
        )

        assert code == 1
        assert err == "Error: Failed to connect to Phabricator API: refused\n"

    def test_any_phabfive_error(self, monkeypatch, capsys):
        code, err = self._run(PhabfiveDataException("no such URI"), monkeypatch, capsys)

        assert code == 1
        assert err == "Error: no such URI\n"

    def test_an_interrupt_exits_130(self, monkeypatch, capsys):
        code, _err = self._run(KeyboardInterrupt(), monkeypatch, capsys)

        assert code == 130
