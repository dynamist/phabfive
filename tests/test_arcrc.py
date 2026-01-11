# -*- coding: utf-8 -*-

"""Tests for .arcrc file support and secure file permissions (issue #123)."""

import json
import os
from unittest import mock

import pytest

from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException


class TestCheckSecurePermissions:
    """Tests for the _check_secure_permissions method."""

    def setup_method(self):
        """Create a Phabfive instance without initialization for testing."""
        self.phabfive = object.__new__(Phabfive)

    def test_nonexistent_file_passes(self, tmp_path):
        """Test that non-existent file doesn't raise error."""
        nonexistent = tmp_path / "nonexistent.yaml"
        # Should not raise
        self.phabfive._check_secure_permissions(str(nonexistent))

    def test_secure_permissions_600(self, tmp_path):
        """Test that 0600 permissions pass."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("PHAB_TOKEN: test")
        os.chmod(config_file, 0o600)
        # Should not raise
        self.phabfive._check_secure_permissions(str(config_file))

    def test_secure_permissions_400(self, tmp_path):
        """Test that 0400 permissions pass."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("PHAB_TOKEN: test")
        os.chmod(config_file, 0o400)
        # Should not raise
        self.phabfive._check_secure_permissions(str(config_file))

    def test_insecure_permissions_group_read(self, tmp_path):
        """Test that group readable (0640) raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("PHAB_TOKEN: test")
        os.chmod(config_file, 0o640)

        with pytest.raises(PhabfiveConfigException) as exc_info:
            self.phabfive._check_secure_permissions(str(config_file))

        assert "insecure permissions" in str(exc_info.value)
        assert "0o640" in str(exc_info.value)
        assert "chmod 600" in str(exc_info.value)

    def test_insecure_permissions_world_read(self, tmp_path):
        """Test that world readable (0644) raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("PHAB_TOKEN: test")
        os.chmod(config_file, 0o644)

        with pytest.raises(PhabfiveConfigException) as exc_info:
            self.phabfive._check_secure_permissions(str(config_file))

        assert "insecure permissions" in str(exc_info.value)
        assert "0o644" in str(exc_info.value)

    def test_insecure_permissions_group_write(self, tmp_path):
        """Test that group writable (0620) raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("PHAB_TOKEN: test")
        os.chmod(config_file, 0o620)

        with pytest.raises(PhabfiveConfigException) as exc_info:
            self.phabfive._check_secure_permissions(str(config_file))

        assert "insecure permissions" in str(exc_info.value)

    def test_insecure_permissions_world_execute(self, tmp_path):
        """Test that world executable (0701) raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("PHAB_TOKEN: test")
        os.chmod(config_file, 0o701)

        with pytest.raises(PhabfiveConfigException) as exc_info:
            self.phabfive._check_secure_permissions(str(config_file))

        assert "insecure permissions" in str(exc_info.value)


class TestLoadArcrc:
    """Tests for the _load_arcrc method."""

    def setup_method(self):
        """Create a Phabfive instance without initialization for testing."""
        # We need to bypass __init__ which requires actual config
        self.phabfive = object.__new__(Phabfive)

    def test_arcrc_not_found(self, tmp_path):
        """Test that missing .arcrc returns empty dict."""
        with mock.patch.object(os.path, "expanduser", return_value=str(tmp_path / ".arcrc")):
            result = self.phabfive._load_arcrc({})
        assert result == {}

    def test_arcrc_insecure_permissions_group_readable(self, tmp_path):
        """Test that .arcrc with group read permission raises error."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_path.write_text('{"hosts": {}}')
        os.chmod(arcrc_path, 0o640)  # group readable

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            with pytest.raises(PhabfiveConfigException) as exc_info:
                self.phabfive._load_arcrc({})

        assert "insecure permissions" in str(exc_info.value)
        assert "chmod 600" in str(exc_info.value)

    def test_arcrc_insecure_permissions_world_readable(self, tmp_path):
        """Test that .arcrc with world read permission raises error."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_path.write_text('{"hosts": {}}')
        os.chmod(arcrc_path, 0o644)  # world readable

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            with pytest.raises(PhabfiveConfigException) as exc_info:
                self.phabfive._load_arcrc({})

        assert "insecure permissions" in str(exc_info.value)

    def test_arcrc_secure_permissions(self, tmp_path):
        """Test that .arcrc with 0600 permissions is accepted."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge.example.com/api/": {
                    "token": "cli-abcdefghijklmnopqrstuvwxyz12"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        assert result["PHAB_URL"] == "https://phorge.example.com/api/"
        assert result["PHAB_TOKEN"] == "cli-abcdefghijklmnopqrstuvwxyz12"

    def test_arcrc_owner_only_read(self, tmp_path):
        """Test that .arcrc with 0400 permissions is accepted."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge.example.com/api/": {
                    "token": "cli-abcdefghijklmnopqrstuvwxyz12"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o400)  # owner read only

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        assert "PHAB_URL" in result

    def test_arcrc_invalid_json(self, tmp_path):
        """Test that invalid JSON in .arcrc returns empty dict."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_path.write_text("{ invalid json }")
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        assert result == {}

    def test_arcrc_no_hosts(self, tmp_path):
        """Test that .arcrc without hosts section returns empty dict."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {"config": {"some": "setting"}}
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        assert result == {}

    def test_arcrc_empty_hosts(self, tmp_path):
        """Test that .arcrc with empty hosts section returns empty dict."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {"hosts": {}}
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        assert result == {}


class TestArcrcSingleHost:
    """Tests for single host in .arcrc."""

    def setup_method(self):
        self.phabfive = object.__new__(Phabfive)

    def test_single_host_provides_url_and_token(self, tmp_path):
        """Test that single host in .arcrc provides both URL and token."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge.example.com/api/": {
                    "token": "cli-abcdefghijklmnopqrstuvwxyz12"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        assert result["PHAB_URL"] == "https://phorge.example.com/api/"
        assert result["PHAB_TOKEN"] == "cli-abcdefghijklmnopqrstuvwxyz12"

    def test_single_host_without_token(self, tmp_path):
        """Test that single host without token only provides URL."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge.example.com/api/": {}
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        assert result["PHAB_URL"] == "https://phorge.example.com/api/"
        assert "PHAB_TOKEN" not in result


class TestArcrcMultipleHosts:
    """Tests for multiple hosts in .arcrc."""

    def setup_method(self):
        self.phabfive = object.__new__(Phabfive)

    def test_multiple_hosts_without_phab_url_raises_error(self, tmp_path):
        """Test that multiple hosts without PHAB_URL set raises error."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge-a.example.com/api/": {
                    "token": "cli-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                },
                "https://phorge-b.example.com/api/": {
                    "token": "cli-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            with pytest.raises(PhabfiveConfigException) as exc_info:
                self.phabfive._load_arcrc({})

        error_msg = str(exc_info.value)
        assert "Multiple hosts found" in error_msg
        assert "phorge-a.example.com" in error_msg
        assert "phorge-b.example.com" in error_msg

    def test_multiple_hosts_with_matching_phab_url(self, tmp_path):
        """Test that multiple hosts with matching PHAB_URL returns token."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge-a.example.com/api/": {
                    "token": "cli-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                },
                "https://phorge-b.example.com/api/": {
                    "token": "cli-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        current_conf = {"PHAB_URL": "https://phorge-b.example.com/api/"}

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc(current_conf)

        # Should only return token, not URL (URL already set)
        assert "PHAB_URL" not in result
        assert result["PHAB_TOKEN"] == "cli-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    def test_multiple_hosts_with_non_matching_phab_url(self, tmp_path):
        """Test that multiple hosts with non-matching PHAB_URL returns empty."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge-a.example.com/api/": {
                    "token": "cli-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                },
                "https://phorge-b.example.com/api/": {
                    "token": "cli-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        current_conf = {"PHAB_URL": "https://other-server.example.com/api/"}

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc(current_conf)

        assert result == {}

    def test_multiple_hosts_with_default_uses_default(self, tmp_path):
        """Test that multiple hosts with config.default uses the default host."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge-a.example.com/api/": {
                    "token": "cli-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                },
                "https://phorge-b.example.com/api/": {
                    "token": "cli-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                }
            },
            "config": {
                "default": "https://phorge-b.example.com"
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        assert result["PHAB_URL"] == "https://phorge-b.example.com/api/"
        assert result["PHAB_TOKEN"] == "cli-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    def test_multiple_hosts_with_default_and_phab_url_uses_phab_url(self, tmp_path):
        """Test that PHAB_URL takes precedence over config.default."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge-a.example.com/api/": {
                    "token": "cli-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                },
                "https://phorge-b.example.com/api/": {
                    "token": "cli-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                }
            },
            "config": {
                "default": "https://phorge-b.example.com"
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        # PHAB_URL set to host A, but default is host B
        current_conf = {"PHAB_URL": "https://phorge-a.example.com/api/"}

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc(current_conf)

        # Should use host A's token (PHAB_URL takes precedence)
        assert "PHAB_URL" not in result  # URL already set, not overwritten
        assert result["PHAB_TOKEN"] == "cli-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_multiple_hosts_with_invalid_default_raises_error(self, tmp_path):
        """Test that invalid default (not matching any host) raises error."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge-a.example.com/api/": {
                    "token": "cli-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                },
                "https://phorge-b.example.com/api/": {
                    "token": "cli-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                }
            },
            "config": {
                "default": "https://nonexistent.example.com"
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            with pytest.raises(PhabfiveConfigException) as exc_info:
                self.phabfive._load_arcrc({})

        assert "Multiple hosts found" in str(exc_info.value)

    def test_multiple_hosts_default_without_api_suffix(self, tmp_path):
        """Test that default URL without /api/ suffix still matches."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge.example.com/api/": {
                    "token": "cli-abcdefghijklmnopqrstuvwxyz12"
                },
                "https://other.example.com/api/": {
                    "token": "cli-99999999999999999999999999999999"
                }
            },
            "config": {
                "default": "https://phorge.example.com"  # No /api/ suffix
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        assert result["PHAB_URL"] == "https://phorge.example.com/api/"
        assert result["PHAB_TOKEN"] == "cli-abcdefghijklmnopqrstuvwxyz12"


class TestArcrcUrlMatching:
    """Tests for URL matching between PHAB_URL and .arcrc hosts."""

    def setup_method(self):
        self.phabfive = object.__new__(Phabfive)

    def test_url_matching_with_trailing_slash(self, tmp_path):
        """Test URL matching works with/without trailing slash."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge.example.com/api/": {
                    "token": "cli-abcdefghijklmnopqrstuvwxyz12"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        # PHAB_URL without trailing slash should still match
        current_conf = {"PHAB_URL": "https://phorge.example.com/api"}

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc(current_conf)

        assert result["PHAB_TOKEN"] == "cli-abcdefghijklmnopqrstuvwxyz12"

    def test_url_matching_arcrc_without_trailing_slash(self, tmp_path):
        """Test URL matching when .arcrc has no trailing slash."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge.example.com/api": {
                    "token": "cli-abcdefghijklmnopqrstuvwxyz12"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        current_conf = {"PHAB_URL": "https://phorge.example.com/api/"}

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc(current_conf)

        assert result["PHAB_TOKEN"] == "cli-abcdefghijklmnopqrstuvwxyz12"

    def test_url_matching_with_port(self, tmp_path):
        """Test URL matching works with port numbers."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge.example.com:8443/api/": {
                    "token": "cli-abcdefghijklmnopqrstuvwxyz12"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        current_conf = {"PHAB_URL": "https://phorge.example.com:8443/api/"}

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc(current_conf)

        assert result["PHAB_TOKEN"] == "cli-abcdefghijklmnopqrstuvwxyz12"


class TestArcrcLegacyFormat:
    """Tests for legacy certificate-based .arcrc format."""

    def setup_method(self):
        self.phabfive = object.__new__(Phabfive)

    def test_legacy_cert_format_no_token(self, tmp_path):
        """Test that legacy cert format (no token) doesn't provide token."""
        arcrc_path = tmp_path / ".arcrc"
        arcrc_data = {
            "hosts": {
                "https://phorge.example.com/api/": {
                    "user": "someuser",
                    "cert": "some-certificate-string"
                }
            }
        }
        arcrc_path.write_text(json.dumps(arcrc_data))
        os.chmod(arcrc_path, 0o600)

        with mock.patch.object(os.path, "expanduser", return_value=str(arcrc_path)):
            result = self.phabfive._load_arcrc({})

        # Should provide URL but not token (cert-based auth not supported)
        assert result["PHAB_URL"] == "https://phorge.example.com/api/"
        assert "PHAB_TOKEN" not in result
