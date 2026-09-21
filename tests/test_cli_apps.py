# -*- coding: utf-8 -*-

"""phabfive.cli.apps is how every command builds the app it runs."""

from unittest import mock

import pytest
import requests
import typer

from phabfive.cli.apps import get_app, new_app, prompt_host
from phabfive.exceptions import PhabfiveConfigException


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
        cls = mock.MagicMock(side_effect=requests.exceptions.ConnectionError("down"))

        with pytest.raises(typer.Exit) as exit_info:
            get_app(cls)

        assert exit_info.value.exit_code == 1
        assert capsys.readouterr().err == (
            "Error: Failed to connect to Phabricator API: down\n"
        )
