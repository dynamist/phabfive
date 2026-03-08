# -*- coding: utf-8 -*-

"""Tests for the setup wizard module."""

import os
from unittest import mock

import pytest

from phabfive.setup import (
    SetupWizard,
    offer_setup_on_error,
    _find_git_root,
    _setup_arcconfig,
)


# Skip marker for tests that require Unix-style permissions
skip_on_windows = pytest.mark.skipif(
    os.name == "nt", reason="Windows uses ACLs, not Unix permissions"
)


class TestSetupWizardUrlNormalization:
    """Tests for URL normalization in setup wizard."""

    def setup_method(self):
        self.wizard = SetupWizard()

    def test_normalize_url_bare(self):
        """Test normalization of bare URL."""
        result = self.wizard._normalize_url("https://phorge.example.com")
        assert result == "https://phorge.example.com/api/"

    def test_normalize_url_trailing_slash(self):
        """Test normalization of URL with trailing slash."""
        result = self.wizard._normalize_url("https://phorge.example.com/")
        assert result == "https://phorge.example.com/api/"

    def test_normalize_url_with_api(self):
        """Test normalization of URL with /api."""
        result = self.wizard._normalize_url("https://phorge.example.com/api")
        assert result == "https://phorge.example.com/api/"

    def test_normalize_url_with_api_slash(self):
        """Test normalization of URL already ending with /api/."""
        result = self.wizard._normalize_url("https://phorge.example.com/api/")
        assert result == "https://phorge.example.com/api/"

    def test_normalize_url_multiple_trailing_slashes(self):
        """Test normalization of URL with multiple trailing slashes."""
        result = self.wizard._normalize_url("https://phorge.example.com///")
        assert result == "https://phorge.example.com/api/"

    def test_normalize_url_with_port(self):
        """Test normalization of URL with port."""
        result = self.wizard._normalize_url("https://phorge.example.com:8080")
        assert result == "https://phorge.example.com:8080/api/"

    def test_normalize_url_http(self):
        """Test normalization of HTTP URL."""
        result = self.wizard._normalize_url("http://localhost")
        assert result == "http://localhost/api/"


class TestOfferSetupOnError:
    """Tests for offer_setup_on_error function."""

    def test_non_interactive_shows_hint(self, capsys):
        """Test that non-interactive mode shows hint about phabfive user setup."""
        with mock.patch("sys.stdin.isatty", return_value=False):
            result = offer_setup_on_error("PHAB_TOKEN is not configured")

        assert result is False
        captured = capsys.readouterr()
        assert "phabfive user setup" in captured.err
        assert "PHAB_TOKEN is not configured" in captured.err

    def test_non_interactive_returns_false(self):
        """Test that non-interactive mode returns False."""
        with mock.patch("sys.stdin.isatty", return_value=False):
            result = offer_setup_on_error("Test error")
        assert result is False

    def test_interactive_declines_returns_false(self):
        """Test that declining interactive setup returns False."""
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("phabfive.setup.Confirm.ask", return_value=False),
            mock.patch("phabfive.setup.Console"),
        ):
            result = offer_setup_on_error("Test error")
        assert result is False

    def test_phab_url_error_offers_arcconfig(self):
        """Test that PHAB_URL error offers .arcconfig setup, not full wizard."""
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch(
                "phabfive.setup.Confirm.ask", return_value=False
            ) as mock_confirm,
            mock.patch("phabfive.setup.Console"),
        ):
            offer_setup_on_error("PHAB_URL is not configured")

        # Should ask about .arcconfig, not generic setup
        call_args = mock_confirm.call_args[0][0]
        assert ".arcconfig" in call_args

    def test_phab_token_error_offers_full_setup(self):
        """Test that PHAB_TOKEN error offers full setup wizard."""
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch(
                "phabfive.setup.Confirm.ask", return_value=False
            ) as mock_confirm,
            mock.patch("phabfive.setup.Console"),
        ):
            offer_setup_on_error("PHAB_TOKEN is not configured")

        call_args = mock_confirm.call_args[0][0]
        assert "interactive setup" in call_args


class TestSetupArcconfig:
    """Tests for .arcconfig creation."""

    def test_find_git_root(self, tmp_path):
        """Test finding git root from subdirectory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        subdir = tmp_path / "src" / "deep"
        subdir.mkdir(parents=True)

        with mock.patch("os.getcwd", return_value=str(subdir)):
            result = _find_git_root()

        assert result == str(tmp_path)

    def test_find_git_root_not_in_repo(self, tmp_path):
        """Test returns None when not in a git repo."""
        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = _find_git_root()

        assert result is None

    def test_setup_arcconfig_creates_file(self, tmp_path):
        """Test that .arcconfig is created with correct content."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        console = mock.MagicMock()

        with (
            mock.patch("os.getcwd", return_value=str(tmp_path)),
            mock.patch(
                "phabfive.setup.Prompt.ask",
                return_value="https://phorge.example.com",
            ),
        ):
            result = _setup_arcconfig(console)

        assert result is True

        import json

        arcconfig_path = tmp_path / ".arcconfig"
        assert arcconfig_path.exists()

        with open(arcconfig_path) as f:
            data = json.load(f)

        assert data["phabricator.uri"] == "https://phorge.example.com/"

    def test_setup_arcconfig_strips_api_suffix(self, tmp_path):
        """Test that /api/ suffix is stripped from URL for .arcconfig."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        console = mock.MagicMock()

        with (
            mock.patch("os.getcwd", return_value=str(tmp_path)),
            mock.patch(
                "phabfive.setup.Prompt.ask",
                return_value="https://phorge.example.com/api/",
            ),
        ):
            _setup_arcconfig(console)

        import json

        with open(tmp_path / ".arcconfig") as f:
            data = json.load(f)

        assert data["phabricator.uri"] == "https://phorge.example.com/"

    def test_setup_arcconfig_not_in_git_repo(self, tmp_path):
        """Test that setup fails gracefully outside a git repo."""
        console = mock.MagicMock()

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = _setup_arcconfig(console)

        assert result is False


class TestSetupWizardSaveConfig:
    """Tests for configuration saving to ~/.arcrc."""

    def setup_method(self):
        self.wizard = SetupWizard()

    def test_save_creates_file(self, tmp_path):
        """Test that save creates .arcrc file."""
        config_path = tmp_path / ".arcrc"

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

        result = self.wizard._save_config()

        assert result is True
        assert config_path.exists()

    def test_save_writes_arcrc_json_format(self, tmp_path):
        """Test that save writes correct .arcrc JSON format."""
        config_path = tmp_path / ".arcrc"

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

        self.wizard._save_config()

        import json

        with open(config_path) as f:
            data = json.load(f)

        assert "hosts" in data
        assert "https://phorge.example.com/api/" in data["hosts"]
        assert (
            data["hosts"]["https://phorge.example.com/api/"]["token"]
            == "cli-abcdefghijklmnopqrstuvwxyz12"
        )

    @skip_on_windows
    def test_save_creates_secure_permissions(self, tmp_path):
        """Test that saved .arcrc file has secure permissions."""
        config_path = tmp_path / ".arcrc"

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

        self.wizard._save_config()

        # Check permissions on Unix
        mode = os.stat(config_path).st_mode & 0o777
        assert mode == 0o600

    def test_save_merges_into_existing_arcrc(self, tmp_path):
        """Test that save merges into existing .arcrc with other hosts."""
        import json

        config_path = tmp_path / ".arcrc"
        existing = {
            "hosts": {
                "https://other.example.com/api/": {
                    "token": "cli-existingtokenexistingtoken12"
                }
            }
        }
        config_path.write_text(json.dumps(existing))

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

        self.wizard._save_config()

        with open(config_path) as f:
            data = json.load(f)

        # Both hosts should be present
        assert "https://other.example.com/api/" in data["hosts"]
        assert (
            data["hosts"]["https://other.example.com/api/"]["token"]
            == "cli-existingtokenexistingtoken12"
        )
        assert "https://phorge.example.com/api/" in data["hosts"]
        assert (
            data["hosts"]["https://phorge.example.com/api/"]["token"]
            == "cli-abcdefghijklmnopqrstuvwxyz12"
        )

    def test_save_updates_existing_host(self, tmp_path):
        """Test that save updates token for existing host."""
        import json

        config_path = tmp_path / ".arcrc"
        existing = {
            "hosts": {
                "https://phorge.example.com/api/": {
                    "token": "cli-oldtokenoldtokenoldtokenold1"
                }
            }
        }
        config_path.write_text(json.dumps(existing))

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-newtokennewtokennewtokennew1"

        self.wizard._save_config()

        with open(config_path) as f:
            data = json.load(f)

        assert (
            data["hosts"]["https://phorge.example.com/api/"]["token"]
            == "cli-newtokennewtokennewtokennew1"
        )


class TestSetupWizardVerifyConnection:
    """Tests for connection verification."""

    def setup_method(self):
        self.wizard = SetupWizard()
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

    def test_verify_connection_success(self):
        """Test successful connection verification."""
        mock_whoami = {"userName": "testuser", "realName": "Test User"}

        with (
            mock.patch("phabfive.setup.Phabricator") as mock_phab,
            mock.patch("phabfive.setup.Console"),
        ):
            mock_instance = mock_phab.return_value
            mock_instance.user.whoami.return_value = mock_whoami

            result = self.wizard._verify_connection()

        assert result is True
        mock_instance.update_interfaces.assert_called_once()
        mock_instance.user.whoami.assert_called_once()

    def test_verify_connection_api_error(self):
        """Test connection verification with API error."""
        from phabricator import APIError

        with (
            mock.patch("phabfive.setup.Phabricator") as mock_phab,
            mock.patch("phabfive.setup.Console"),
            mock.patch("phabfive.setup.Confirm.ask", return_value=False),
        ):
            mock_instance = mock_phab.return_value
            mock_instance.update_interfaces.return_value = None
            mock_instance.user.whoami.side_effect = APIError(
                "ERR-CONDUIT-CORE", "Invalid token"
            )

            result = self.wizard._verify_connection()

        assert result is False
