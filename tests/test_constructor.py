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

HOST = "https://phorge.example.com"
URL = f"{HOST}/api/"
TOKEN = "api-" + "a" * 28


@pytest.fixture
def phabricator():
    """Replace the Conduit client, recording every client that is built."""
    with mock.patch("phabfive.core.Phabricator") as factory:
        yield factory


class TestExplicitConfiguration:
    def test_url_and_token_configure_the_instance(self, phabricator):
        app = Phabfive(url=HOST, token=TOKEN)

        assert app.conf["PHORGE_HOST"] == HOST
        assert app.conf["PHORGE_API_HOST"] == URL
        assert app.conf["PHORGE_TOKEN"] == TOKEN
        assert app.url == HOST

    def test_nothing_is_discovered(self, phabricator, monkeypatch):
        """Neither the environment nor any file can change an explicit setup."""
        monkeypatch.setenv("PHORGE_HOST", "https://elsewhere.example.com/api/")
        monkeypatch.setenv("PHORGE_SPACE", "S9")

        with mock.patch.object(Phabfive, "read_config") as read_config:
            app = Phabfive(url=URL, token=TOKEN)

        read_config.assert_not_called()
        assert app.conf["PHORGE_HOST"] == HOST
        assert app.conf["PHORGE_SPACE"] == "S1"

    def test_the_endpoint_is_forgiven(self, phabricator):
        """A pasted /api/ endpoint names the instance as well as its root."""
        app = Phabfive(url=URL, token=TOKEN)

        assert app.conf["PHORGE_HOST"] == HOST
        assert app.conf["PHORGE_API_HOST"] == URL

    def test_a_bare_host_is_reached_over_https(self, phabricator):
        """No scheme means https."""
        app = Phabfive(url="phorge.example.com", token=TOKEN)

        assert app.conf["PHORGE_HOST"] == HOST
        assert app.conf["PHORGE_API_HOST"] == URL

    def test_config_sets_other_values(self, phabricator):
        app = Phabfive(url=URL, token=TOKEN, config={"PHORGE_SPACE": "S2"})

        assert app.conf["PHORGE_SPACE"] == "S2"

    def test_config_alone_is_explicit(self, phabricator):
        app = Phabfive(config={"PHORGE_HOST": HOST, "PHORGE_TOKEN": TOKEN})

        assert app.conf["PHORGE_HOST"] == HOST

    def test_arguments_override_config(self, phabricator):
        other = "https://other.example.com"
        app = Phabfive(url=other, config={"PHORGE_HOST": HOST, "PHORGE_TOKEN": TOKEN})

        assert app.conf["PHORGE_HOST"] == other

    def test_an_unknown_key_is_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException, match="PHORGE_SPCAE"):
            Phabfive(url=URL, token=TOKEN, config={"PHORGE_SPCAE": "S2"})

    def test_a_missing_token_is_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException, match="PHORGE_TOKEN"):
            Phabfive(url=URL)

    def test_a_malformed_token_is_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException, match="PHORGE_TOKEN is malformed"):
            Phabfive(url=URL, token="too-short")

    def test_counts_as_an_explicit_host(self, phabricator):
        assert Phabfive(url=URL, token=TOKEN).has_explicit_host()

    def test_the_old_name_of_the_explicit_host_check_still_answers(self, phabricator):
        assert Phabfive(url=URL, token=TOKEN).has_explicit_phab_url()

    def test_app_classes_take_the_same_arguments(self, phabricator):
        app = Maniphest(url=HOST, token=TOKEN)

        assert app.conf["PHORGE_HOST"] == HOST


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
            "PHORGE_HOST": "https://phorge-b.example.com/api/",
            "PHORGE_TOKEN": "b" * 32,
        }

    def test_without_a_callback_a_terminal_is_not_prompted(self, arcrc):
        """A library never prompts; that is the command's decision."""
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            with mock.patch("InquirerPy.inquirer.select") as select:
                with pytest.raises(PhabfiveConfigException, match="Multiple hosts"):
                    Phabfive._load_arcrc({})

        select.assert_not_called()

    def test_a_declining_callback_explains_how_to_choose(self, arcrc):
        with pytest.raises(PhabfiveConfigException, match="set PHORGE_HOST"):
            Phabfive._load_arcrc({}, select_host=lambda hosts: None)


class TestProcessEnvironment:
    def test_reading_the_configuration_changes_no_variable(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_DIRS", raising=False)
        before = dict(os.environ)

        Phabfive.read_config()

        assert dict(os.environ) == before

    @pytest.mark.parametrize("platform", ["linux", "freebsd14"])
    def test_the_system_configuration_is_in_etc(self, monkeypatch, platform):
        monkeypatch.setattr("sys.platform", platform)
        monkeypatch.setenv("XDG_CONFIG_DIRS", "/somewhere/else")

        assert Phabfive._site_config_base() == "/etc/phabfive"


class TestLibraryOutputDefaults:
    """Without the command setting them, output options suit a program."""

    def test_links_are_plain_strings(self, phabricator):
        app = Phabfive(url=URL, token=TOKEN)

        link = app.format_link("https://phorge.example.com/T1", "T1")

        assert link == "https://phorge.example.com/T1"
        assert type(link) is str

    def test_no_rich_line_width_limit(self, phabricator):
        app = Phabfive(url=URL, token=TOKEN)

        app.check_line_width("x" * (Phabfive.MAX_LINE_WIDTH + 1))


class TestPerInstanceLookups:
    """Maniphest's status map is fetched once per instance, not per class."""

    def test_fetched_once_per_instance(self, phabricator):
        app = Maniphest(url=URL, token=TOKEN)
        with mock.patch(
            "phabfive.maniphest.core.fetch_api_status_map", return_value={"a": 1}
        ) as fetch:
            assert app._get_api_status_map() == {"a": 1}
            assert app._get_api_status_map() == {"a": 1}

        fetch.assert_called_once()

    def test_two_instances_do_not_share_or_evict(self, phabricator):
        one = Maniphest(url=URL, token=TOKEN)
        two = Maniphest(url="https://other.example.com/api/", token=TOKEN)
        with mock.patch(
            "phabfive.maniphest.core.fetch_api_status_map",
            side_effect=lambda phab: {"phab": phab},
        ) as fetch:
            for _ in range(3):
                assert one._get_api_status_map() == {"phab": one.phab}
                assert two._get_api_status_map() == {"phab": two.phab}

        assert fetch.call_count == 2
