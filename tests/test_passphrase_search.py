# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli.passphrase import passphrase_app
from phabfive.exceptions import PhabfiveConfigException
from phabfive.passphrase import Passphrase

runner = CliRunner()


def _credential(id_, name, type_):
    return {
        "id": id_,
        "phid": f"PHID-CDTL-{id_}",
        "name": name,
        "type": type_,
        "username": None,
    }


CREDENTIALS = {
    "PHID-CDTL-1": _credential(1, "Deploy password", "password"),
    "PHID-CDTL-2": _credential(2, "API token", "token"),
    "PHID-CDTL-3": _credential(3, "Deploy key", "ssh-key-text"),
    "PHID-CDTL-4": _credential(4, "Notes", "note"),
}


@pytest.fixture
def passphrase():
    with patch("phabfive.passphrase.core.Phabfive.__init__", return_value=None):
        passphrase = Passphrase()
    passphrase.phab = MagicMock()
    passphrase.url = "https://phorge.example.com"
    passphrase.phab.passphrase.query.return_value = {"data": CREDENTIALS}
    # Dates come from transaction.search; not needed for these tests
    passphrase._get_credential_dates = MagicMock(return_value=(None, None))
    return passphrase


def _names(credentials):
    return [c["name"] for c in credentials]


class TestSearchWithoutCredentials:
    @pytest.mark.parametrize("empty", [[], {}])
    def test_returns_empty_list(self, passphrase, empty):
        """Conduit returns "data": [] when there are no credentials."""
        passphrase.phab.passphrase.query.return_value = {"data": empty}

        assert passphrase.search_passphrases(credential_type="key") == []
        assert passphrase.search_passphrases(query="deploy") == []


class TestSearchByType:
    @pytest.mark.parametrize(
        "credential_type, expected",
        [
            ("password", ["Deploy password"]),
            ("token", ["API token"]),
            ("key", ["Deploy key"]),
            ("ssh", ["Deploy key"]),
            ("note", ["Notes"]),
            ("PASSWORD", ["Deploy password"]),
        ],
    )
    def test_filters_by_type(self, passphrase, credential_type, expected):
        result = passphrase.search_passphrases(credential_type=credential_type)
        assert _names(result) == expected

    @pytest.mark.parametrize("credential_type", ["bogus", "pasword"])
    def test_rejects_unknown_type_before_querying(self, passphrase, credential_type):
        with pytest.raises(PhabfiveConfigException) as excinfo:
            passphrase.search_passphrases(credential_type=credential_type)

        assert str(excinfo.value) == (
            f"Invalid type '{credential_type}'. "
            "Valid choices: password, token, key, ssh, note"
        )
        passphrase.phab.passphrase.query.assert_not_called()

    def test_combines_type_and_name(self, passphrase):
        result = passphrase.search_passphrases(query="deploy", credential_type="key")
        assert _names(result) == ["Deploy key"]


class TestSearchCommand:
    @patch("phabfive.cli.passphrase._get_passphrase_app")
    def test_unknown_type_is_a_clean_error(self, mock_get_app, passphrase):
        mock_get_app.return_value = passphrase

        result = runner.invoke(passphrase_app, ["search", "--type", "bogus"])

        assert result.exit_code == 1
        assert "ERROR: Invalid type 'bogus'" in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)

    @patch("phabfive.cli.passphrase._get_passphrase_app")
    def test_no_credentials_is_not_an_error(self, mock_get_app, passphrase):
        passphrase.phab.passphrase.query.return_value = {"data": []}
        mock_get_app.return_value = passphrase

        result = runner.invoke(passphrase_app, ["search", "--type", "key"])

        assert result.exit_code == 0
        assert "No credentials found matching the criteria" in result.output
