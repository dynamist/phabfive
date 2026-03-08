# -*- coding: utf-8 -*-

"""Tests for .arcconfig file support (issue #171)."""

import json
from unittest import mock

from phabfive.core import Phabfive


class TestLoadArcconfig:
    """Tests for the _load_arcconfig method."""

    def setup_method(self):
        """Create a Phabfive instance without initialization for testing."""
        self.phabfive = object.__new__(Phabfive)

    def test_arcconfig_found_with_valid_uri(self, tmp_path):
        """Test .arcconfig found at git root with valid phabricator.uri."""
        # Create fake git repo structure
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        arcconfig = tmp_path / ".arcconfig"
        arcconfig.write_text(
            json.dumps({"phabricator.uri": "https://phorge.example.com/"})
        )

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = self.phabfive._load_arcconfig()

        assert result == {"PHAB_URL": "https://phorge.example.com/api/"}

    def test_arcconfig_missing(self, tmp_path):
        """Test .arcconfig missing returns empty dict."""
        # Create git repo without .arcconfig
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = self.phabfive._load_arcconfig()

        assert result == {}

    def test_no_phabricator_uri_key(self, tmp_path):
        """Test .arcconfig with no phabricator.uri key returns empty dict."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        arcconfig = tmp_path / ".arcconfig"
        arcconfig.write_text(json.dumps({"other.key": "value"}))

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = self.phabfive._load_arcconfig()

        assert result == {}

    def test_invalid_json(self, tmp_path):
        """Test .arcconfig with invalid JSON returns empty dict."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        arcconfig = tmp_path / ".arcconfig"
        arcconfig.write_text("{ invalid json }")

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = self.phabfive._load_arcconfig()

        assert result == {}

    def test_not_in_git_repo(self, tmp_path):
        """Test not in a git repo returns empty dict."""
        # No .git directory anywhere
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        with mock.patch("os.getcwd", return_value=str(subdir)):
            result = self.phabfive._load_arcconfig()

        assert result == {}

    def test_git_root_found_in_parent(self, tmp_path):
        """Test .arcconfig found when .git is in parent directory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        arcconfig = tmp_path / ".arcconfig"
        arcconfig.write_text(
            json.dumps({"phabricator.uri": "https://phorge.example.com/"})
        )

        subdir = tmp_path / "src" / "deep"
        subdir.mkdir(parents=True)

        with mock.patch("os.getcwd", return_value=str(subdir)):
            result = self.phabfive._load_arcconfig()

        assert result == {"PHAB_URL": "https://phorge.example.com/api/"}


class TestArconfigUrlNormalization:
    """Tests for URL normalization from .arcconfig."""

    def setup_method(self):
        self.phabfive = object.__new__(Phabfive)

    def test_url_without_api_suffix(self, tmp_path):
        """Test URL without /api/ gets normalized."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        arcconfig = tmp_path / ".arcconfig"
        arcconfig.write_text(
            json.dumps({"phabricator.uri": "https://phorge.example.com"})
        )

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = self.phabfive._load_arcconfig()

        assert result["PHAB_URL"] == "https://phorge.example.com/api/"

    def test_url_with_trailing_slash(self, tmp_path):
        """Test URL with trailing slash gets normalized."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        arcconfig = tmp_path / ".arcconfig"
        arcconfig.write_text(
            json.dumps({"phabricator.uri": "https://phorge.example.com/"})
        )

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = self.phabfive._load_arcconfig()

        assert result["PHAB_URL"] == "https://phorge.example.com/api/"

    def test_url_with_api_already(self, tmp_path):
        """Test URL already ending with /api/ is preserved."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        arcconfig = tmp_path / ".arcconfig"
        arcconfig.write_text(
            json.dumps({"phabricator.uri": "https://phorge.example.com/api/"})
        )

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = self.phabfive._load_arcconfig()

        assert result["PHAB_URL"] == "https://phorge.example.com/api/"

    def test_url_with_port(self, tmp_path):
        """Test URL with port gets normalized."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        arcconfig = tmp_path / ".arcconfig"
        arcconfig.write_text(
            json.dumps({"phabricator.uri": "https://phorge.example.com:8443"})
        )

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = self.phabfive._load_arcconfig()

        assert result["PHAB_URL"] == "https://phorge.example.com:8443/api/"

    def test_url_with_api_no_trailing_slash(self, tmp_path):
        """Test URL ending with /api (no trailing slash) gets normalized."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        arcconfig = tmp_path / ".arcconfig"
        arcconfig.write_text(
            json.dumps({"phabricator.uri": "https://phorge.example.com/api"})
        )

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            result = self.phabfive._load_arcconfig()

        assert result["PHAB_URL"] == "https://phorge.example.com/api/"
