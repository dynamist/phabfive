# -*- coding: utf-8 -*-

"""Constructing a phabfive class from a program.

The classes are the library, so constructing one has to work the way a
program needs: configured from arguments when it knows where it is talking
to, without a request until a call needs one, and without building a second
client for a sibling app it uses internally. The command's own behaviour -
discover the configuration, verify the connection straight away - is what
`phabfive.cli.apps` asks for explicitly.
"""

import json
import os
from unittest import mock

import pytest

from phabfive.core import Phabfive
from phabfive.diffusion import Diffusion
from phabfive.edit import Edit
from phabfive.exceptions import PhabfiveConfigException
from phabfive.maniphest import Maniphest
from phabfive.passphrase import Passphrase

URL = "https://phorge.example.com/api/"
TOKEN = "api-" + "a" * 28


@pytest.fixture
def phabricator():
    """Replace the Conduit client, recording every client that is built."""
    with mock.patch("phabfive.core.Phabricator") as factory:
        yield factory


class TestExplicitConfiguration:
    def test_url_and_token_configure_the_instance(self, phabricator):
        app = Phabfive(url=URL, token=TOKEN)

        assert app.conf["PHAB_URL"] == URL
        assert app.conf["PHAB_TOKEN"] == TOKEN
        assert app.url == "https://phorge.example.com"

    def test_nothing_is_discovered(self, phabricator, monkeypatch):
        """Neither the environment nor any file can change an explicit setup."""
        monkeypatch.setenv("PHAB_URL", "https://elsewhere.example.com/api/")
        monkeypatch.setenv("PHAB_SPACE", "S9")

        with mock.patch.object(Phabfive, "read_config") as read_config:
            app = Phabfive(url=URL, token=TOKEN)

        read_config.assert_not_called()
        assert app.conf["PHAB_URL"] == URL
        assert app.conf["PHAB_SPACE"] == "S1"

    def test_the_instance_address_is_enough(self, phabricator):
        """The /api/ suffix is added, since it is the usual thing to forget."""
        app = Phabfive(url="https://phorge.example.com", token=TOKEN)

        assert app.conf["PHAB_URL"] == URL

    def test_config_sets_other_values(self, phabricator):
        app = Phabfive(url=URL, token=TOKEN, config={"PHAB_SPACE": "S2"})

        assert app.conf["PHAB_SPACE"] == "S2"

    def test_config_alone_is_explicit(self, phabricator):
        app = Phabfive(config={"PHAB_URL": URL, "PHAB_TOKEN": TOKEN})

        assert app.conf["PHAB_URL"] == URL

    def test_arguments_override_config(self, phabricator):
        other = "https://other.example.com/api/"
        app = Phabfive(url=other, config={"PHAB_URL": URL, "PHAB_TOKEN": TOKEN})

        assert app.conf["PHAB_URL"] == other

    def test_an_unknown_key_is_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException, match="PHAB_SPCAE"):
            Phabfive(url=URL, token=TOKEN, config={"PHAB_SPCAE": "S2"})

    def test_a_missing_token_is_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException, match="PHAB_TOKEN"):
            Phabfive(url=URL)

    def test_a_malformed_token_is_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException, match="PHAB_TOKEN is malformed"):
            Phabfive(url=URL, token="too-short")

    def test_counts_as_an_explicit_host(self, phabricator):
        assert Phabfive(url=URL, token=TOKEN).has_explicit_phab_url()

    def test_app_classes_take_the_same_arguments(self, phabricator):
        app = Maniphest(url=URL, token=TOKEN)

        assert app.conf["PHAB_URL"] == URL


class TestNoRequestUntilUsed:
    def test_constructing_makes_no_request(self, phabricator):
        app = Phabfive(url=URL, token=TOKEN)

        phabricator.assert_not_called()
        assert not app.phab.is_built

    def test_the_first_call_builds_the_client(self, phabricator):
        app = Phabfive(url=URL, token=TOKEN)

        app.phab.user.whoami()

        phabricator.assert_called_once_with(host=URL, token=TOKEN)
        phabricator.return_value.update_interfaces.assert_called_once_with()

    def test_verify_checks_the_connection_now(self, phabricator):
        Phabfive(url=URL, token=TOKEN, verify=True)

        phabricator.return_value.user.whoami.assert_called_once_with()


class TestSiblingApps:
    """An app another app uses shares its client instead of building one."""

    def test_diffusion_shares_its_client_with_passphrase(self, phabricator):
        diffusion = Diffusion(url=URL, token=TOKEN)

        assert isinstance(diffusion.passphrase, Passphrase)
        assert diffusion.passphrase.phab is diffusion.phab
        assert diffusion.passphrase.conf is diffusion.conf
        assert diffusion.passphrase.url == diffusion.url

    def test_edit_shares_its_client_with_maniphest(self, phabricator):
        edit = Edit(url=URL, token=TOKEN)

        assert isinstance(edit.maniphest, Maniphest)
        assert edit.maniphest.phab is edit.phab

    def test_one_client_is_built_between_them(self, phabricator):
        diffusion = Diffusion(url=URL, token=TOKEN)

        diffusion.phab.user.whoami()
        diffusion.passphrase.phab.user.whoami()

        phabricator.assert_called_once()

    def test_the_sibling_is_made_once(self, phabricator):
        diffusion = Diffusion(url=URL, token=TOKEN)

        assert diffusion.passphrase is diffusion.passphrase

    def test_the_sibling_can_be_replaced(self, phabricator):
        diffusion = Diffusion(url=URL, token=TOKEN)
        replacement = mock.MagicMock()

        diffusion.passphrase = replacement

        assert diffusion.passphrase is replacement


class TestSelectHost:
    """Choosing between several ~/.arcrc hosts is the caller's decision."""

    @pytest.fixture
    def arcrc(self, tmp_path):
        path = tmp_path / ".arcrc"
        path.write_text(
            json.dumps(
                {
                    "hosts": {
                        "https://phorge-a.example.com/api/": {"token": "a" * 32},
                        "https://phorge-b.example.com/api/": {"token": "b" * 32},
                    }
                }
            )
        )
        os.chmod(path, 0o600)
        with mock.patch.object(os.path, "expanduser", return_value=str(path)):
            yield path

    def test_the_callback_chooses(self, arcrc):
        offered = []

        def choose(hosts):
            offered.extend(hosts)
            return hosts[1]

        result = Phabfive._load_arcrc({}, select_host=choose)

        assert offered == [
            "https://phorge-a.example.com/api/",
            "https://phorge-b.example.com/api/",
        ]
        assert result == {
            "PHAB_URL": "https://phorge-b.example.com/api/",
            "PHAB_TOKEN": "b" * 32,
        }
