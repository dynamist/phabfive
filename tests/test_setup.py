# -*- coding: utf-8 -*-

"""Tests for the setup wizard module."""

import os
from unittest import mock

import pytest

from phabfive.setup import SetupWizard, offer_setup_on_error


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


class TestSetupWizardSaveConfig:
    """Tests for configuration saving."""

    def setup_method(self):
        self.wizard = SetupWizard()

    def test_save_creates_file(self, tmp_path):
        """Test that save creates config file."""
        config_path = tmp_path / "phabfive.yaml"

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

        result = self.wizard._save_config()

        assert result is True
        assert config_path.exists()

    def test_save_writes_correct_content(self, tmp_path):
        """Test that save writes correct YAML content."""
        config_path = tmp_path / "phabfive.yaml"

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

        self.wizard._save_config()

        content = config_path.read_text()
        assert "PHAB_URL: https://phorge.example.com/api/" in content
        assert "PHAB_TOKEN: cli-abcdefghijklmnopqrstuvwxyz12" in content

    @skip_on_windows
    def test_save_creates_secure_permissions(self, tmp_path):
        """Test that saved config file has secure permissions."""
        config_path = tmp_path / "phabfive.yaml"

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

        self.wizard._save_config()

        # Check permissions on Unix
        mode = os.stat(config_path).st_mode & 0o777
        assert mode == 0o600

    def test_save_preserves_existing_config(self, tmp_path):
        """Test that save preserves existing configuration values."""
        config_path = tmp_path / "phabfive.yaml"
        config_path.write_text("PHAB_SPACE: S42\n")

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

        self.wizard._save_config()

        content = config_path.read_text()
        assert "PHAB_SPACE: S42" in content
        assert "PHAB_URL: https://phorge.example.com/api/" in content
        assert "PHAB_TOKEN: cli-abcdefghijklmnopqrstuvwxyz12" in content

    def test_save_creates_parent_directory(self, tmp_path):
        """Test that save creates parent directory if needed."""
        config_path = tmp_path / "subdir" / "phabfive.yaml"

        self.wizard.CONFIG_PATH = str(config_path)
        self.wizard.phab_url = "https://phorge.example.com/api/"
        self.wizard.phab_token = "cli-abcdefghijklmnopqrstuvwxyz12"

        result = self.wizard._save_config()

        assert result is True
        assert config_path.exists()


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
