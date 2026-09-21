# -*- coding: utf-8 -*-

"""Tests for `user whoami` honouring the configured host.

`whoami` used to always enumerate ~/.arcrc, so an explicitly configured
PHAB_URL/PHAB_TOKEN was ignored and the command reported an unrelated
host, or failed outright when no ~/.arcrc existed.
"""

import os
from unittest import mock
from unittest.mock import MagicMock, patch

from phabfive.exceptions import PhabfiveAPIException
from typer.testing import CliRunner

from phabfive.cli.user import user_app
from phabfive.core import Phabfive
from phabfive.user import User

runner = CliRunner()

TOKEN = "api-" + "b" * 28


def _load_config(tmp_path, environ, arcrc=None, user_yaml=None, arcconfig=None):
    """Run load_config() against an isolated set of config sources."""
    phabfive = object.__new__(Phabfive)

    arcrc_path = tmp_path / ".arcrc"
    if arcrc is not None:
        arcrc_path.write_text(arcrc)
        os.chmod(arcrc_path, 0o600)

    user_conf = tmp_path / "phabfive.yaml"
    if user_yaml is not None:
        user_conf.write_text(user_yaml)
        os.chmod(user_conf, 0o600)

    with (
        mock.patch(
            "phabfive.core.Phabfive._site_config_base",
            return_value=str(tmp_path / "site"),
        ),
        mock.patch(
            "appdirs.user_config_dir", return_value=str(user_conf).replace(".yaml", "")
        ),
        mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)),
        mock.patch.dict(os.environ, environ, clear=True),
        mock.patch.object(
            Phabfive, "_load_arcconfig", return_value=arcconfig or {}, create=True
        ),
    ):
        phabfive.conf = phabfive.load_config()

    return phabfive


class TestExplicitPhabUrlDetection:
    """PHAB_URL counts as explicit unless it only came from ~/.arcrc."""

    def test_env_var_is_explicit(self, tmp_path):
        phabfive = _load_config(
            tmp_path, {"PHAB_URL": "https://phorge.example.com/api/"}
        )
        assert phabfive.has_explicit_phab_url() is True

    def test_user_yaml_is_explicit(self, tmp_path):
        phabfive = _load_config(
            tmp_path, {}, user_yaml="PHAB_URL: https://phorge.example.com/api/\n"
        )
        assert phabfive.has_explicit_phab_url() is True

    def test_arcconfig_is_explicit(self, tmp_path):
        phabfive = _load_config(
            tmp_path, {}, arcconfig={"PHAB_URL": "https://phorge.example.com/api/"}
        )
        assert phabfive.has_explicit_phab_url() is True

    def test_arcrc_only_is_not_explicit(self, tmp_path):
        """A host discovered by reading ~/.arcrc is not an explicit choice."""
        arcrc = (
            '{"hosts": {"https://phorge.example.com/api/": {"token": "%s"}}}' % TOKEN
        )
        phabfive = _load_config(tmp_path, {}, arcrc=arcrc)

        assert phabfive.conf["PHAB_URL"] == "https://phorge.example.com/api/"
        assert phabfive.has_explicit_phab_url() is False

    def test_no_url_anywhere_is_not_explicit(self, tmp_path):
        phabfive = _load_config(tmp_path, {})
        assert phabfive.has_explicit_phab_url() is False


class TestWhoamiForHost:
    """The shared per-host helper used by both whoami paths."""

    def _user(self):
        user = object.__new__(User)
        user.format_link = lambda url, text, show_url=False: text
        return user

    def test_successful_lookup(self):
        phab = MagicMock()
        phab.user.whoami.return_value = {
            "userName": "admin",
            "realName": "Administrator",
            "primaryEmail": "admin@example.com",
        }

        result = self._user()._whoami_for_host(
            "https://phorge.example.com/api/", TOKEN, phab=phab
        )

        assert result["Host"] == "phorge.example.com"
        assert result["URL"] == "https://phorge.example.com/api/"
        assert result["User"]["UserName"] == "admin"
        assert result["User"]["RealName"] == "Administrator"
        assert "Error" not in result

    def test_missing_token_reports_error(self):
        result = self._user()._whoami_for_host("https://phorge.example.com/api/", None)

        assert result["Error"] == "No token configured for this host"
        assert "User" not in result

    def test_api_error_is_captured(self):
        phab = MagicMock()
        phab.user.whoami.side_effect = PhabfiveAPIException(
            "ERR-INVALID-AUTH", "bad token"
        )

        result = self._user()._whoami_for_host(
            "https://phorge.example.com/api/", TOKEN, phab=phab
        )

        assert "Error" in result
        assert "User" not in result

    def test_configured_host_uses_existing_client(self):
        """whoami_configured_host reports the host from self.conf."""
        user = self._user()
        user.conf = {
            "PHAB_URL": "https://phorge.example.com/api/",
            "PHAB_TOKEN": TOKEN,
        }
        user._normalize_url = Phabfive._normalize_url.__get__(user)
        user.phab = MagicMock()
        user.phab.user.whoami.return_value = {"userName": "admin"}

        result = user.whoami_configured_host()

        assert result["Host"] == "phorge.example.com"
        assert result["User"]["UserName"] == "admin"


class TestWhoamiHostSelection:
    """The CLI picks the configured host or enumerates ~/.arcrc."""

    def _mock_user(self, explicit):
        mock_user = MagicMock()
        mock_user.has_explicit_phab_url.return_value = explicit
        mock_user.whoami_configured_host.return_value = {
            "Host": "phorge.example.com",
            "URL": "https://phorge.example.com/api/",
            "User": {"UserName": "admin"},
        }
        mock_user.whoami_all_hosts.return_value = [
            {
                "Host": "other.example.com",
                "URL": "https://other.example.com/api/",
                "User": {"UserName": "someone"},
            }
        ]
        return mock_user

    @patch("phabfive.user.User")
    def test_explicit_url_queries_only_that_host(self, mock_user_cls):
        mock_user = self._mock_user(explicit=True)
        mock_user_cls.return_value = mock_user

        result = runner.invoke(user_app, ["whoami"])

        assert result.exit_code == 0
        mock_user.whoami_configured_host.assert_called_once()
        mock_user.whoami_all_hosts.assert_not_called()

    @patch("phabfive.user.User")
    def test_without_explicit_url_enumerates_arcrc(self, mock_user_cls):
        mock_user = self._mock_user(explicit=False)
        mock_user_cls.return_value = mock_user

        result = runner.invoke(user_app, ["whoami"])

        assert result.exit_code == 0
        mock_user.whoami_all_hosts.assert_called_once()
        mock_user.whoami_configured_host.assert_not_called()

    @patch("phabfive.user.User")
    def test_all_flag_enumerates_despite_explicit_url(self, mock_user_cls):
        mock_user = self._mock_user(explicit=True)
        mock_user_cls.return_value = mock_user

        result = runner.invoke(user_app, ["whoami", "--all"])

        assert result.exit_code == 0
        mock_user.whoami_all_hosts.assert_called_once()
        mock_user.whoami_configured_host.assert_not_called()

    @patch("phabfive.user.User")
    def test_exit_code_when_every_host_failed(self, mock_user_cls):
        mock_user = self._mock_user(explicit=True)
        mock_user.whoami_configured_host.return_value = {
            "Host": "phorge.example.com",
            "URL": "https://phorge.example.com/api/",
            "Error": "Failed to resolve host",
        }
        mock_user_cls.return_value = mock_user

        result = runner.invoke(user_app, ["whoami"])

        assert result.exit_code == 1
