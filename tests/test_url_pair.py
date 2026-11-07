# -*- coding: utf-8 -*-

"""Configuring an endpoint and a browsable address separately (issue #540).

`PHORGE_HOST` is the instance: the root of every link phabfive hands back and,
as a rule, where it calls Conduit, at `PHORGE_HOST/api/`. Those are the same
address almost everywhere, and they come apart behind an ingress or
split-horizon DNS -- a caller inside a Kubernetes cluster reaches Phorge at a
Service name, while the report it renders is read by people who need links
that open in a browser.

`PHORGE_API_HOST` carries the other half when they differ. The tests below pin
what makes the split safe rather
than merely possible: an endpoint on its own is refused instead of guessed at,
and everything keyed by instance -- the token in `~/.arcrc`, the cache
directory -- keeps keying on the endpoint, so adopting the split does not
orphan what is already stored.
"""

import json
import os
from unittest import mock

import appdirs
import pytest

from phabfive import cache
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException


API = "http://web.phorge.svc.cluster.local/api/"
WEB = "http://phorge.localhost"
HOST = "https://phorge.example.com"
URL = f"{HOST}/api/"
TOKEN = "api-" + "a" * 28

SETTINGS = ("PHORGE_HOST", "PHORGE_API_HOST", "PHORGE_TOKEN")


@pytest.fixture
def phabricator():
    """Replace the Conduit client, recording every client that is built."""
    with mock.patch("phabfive.core.Phabricator") as factory:
        yield factory


@pytest.fixture
def only_the_environment(monkeypatch, tmp_path):
    """Configure from the environment alone, with no files to discover.

    `read_config` merges eight layers and `~/.arcrc` is one of them, so a test
    that does not move HOME reads whatever the developer happens to have.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


class TestOneAddress:
    """The single-setting case, which is every existing configuration."""

    def test_the_host_is_the_instance(self, phabricator):
        app = Phabfive(url=HOST, token=TOKEN)

        assert app.conf["PHORGE_HOST"] == HOST
        assert app.url == HOST

    def test_the_endpoint_is_derived_from_the_host(self, phabricator):
        """PHORGE_API_HOST holds the endpoint whether or not it was given."""
        app = Phabfive(url=HOST, token=TOKEN)
        app.phab.client

        assert app.conf["PHORGE_API_HOST"] == URL
        assert phabricator.call_args.kwargs["host"] == URL


class TestTwoAddresses:
    """The split case: dial one address, publish links rooted at the other."""

    def test_the_endpoint_is_dialled(self, phabricator):
        app = Phabfive(token=TOKEN, config={"PHORGE_HOST": WEB, "PHORGE_API_HOST": API})
        app.phab.client

        assert phabricator.call_args.kwargs["host"] == API

    def test_the_links_are_rooted_at_the_browsable_address(self, phabricator):
        app = Phabfive(token=TOKEN, config={"PHORGE_HOST": WEB, "PHORGE_API_HOST": API})

        assert app.url == WEB
        assert f"{app.url}/T28" == "http://phorge.localhost/T28"

    def test_the_api_host_holds_the_endpoint(self, phabricator):
        """The endpoint is settled into PHORGE_API_HOST, so downstream reads one setting."""
        app = Phabfive(token=TOKEN, config={"PHORGE_HOST": WEB, "PHORGE_API_HOST": API})

        assert app.conf["PHORGE_API_HOST"] == API

    def test_the_endpoint_suffix_is_added(self, phabricator):
        """As for PHORGE_HOST: the address is enough."""
        app = Phabfive(
            token=TOKEN,
            config={
                "PHORGE_HOST": WEB,
                "PHORGE_API_HOST": "http://web.phorge.svc.cluster.local",
            },
        )

        assert app.conf["PHORGE_API_HOST"] == API

    def test_a_trailing_slash_on_the_browsable_address_is_dropped(self, phabricator):
        """Links are built by appending, so a kept slash would double it."""
        app = Phabfive(
            token=TOKEN,
            config={"PHORGE_HOST": "http://phorge.localhost/", "PHORGE_API_HOST": API},
        )

        assert app.url == WEB

    def test_the_url_argument_is_the_host(self, phabricator):
        """`url=` names the instance, beside an endpoint in `config`."""
        app = Phabfive(url=WEB, token=TOKEN, config={"PHORGE_API_HOST": API})
        app.phab.client

        assert app.url == WEB
        assert phabricator.call_args.kwargs["host"] == API


class TestAnEndpointAlone:
    """An endpoint without a host is refused, because the host cannot be derived.

    Stripping `/api/` off an endpoint to guess the browsable address is exactly
    the assumption that fails in the only situation the split exists for.
    """

    def test_an_endpoint_without_a_host_is_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException) as error:
            Phabfive(token=TOKEN, config={"PHORGE_API_HOST": API})

        assert "PHORGE_HOST is not configured" in str(error.value)

    def test_the_refusal_says_what_to_do_instead(self, phabricator):
        with pytest.raises(PhabfiveConfigException) as error:
            Phabfive(token=TOKEN, config={"PHORGE_API_HOST": API})

        assert "set PHORGE_HOST" in str(error.value)

    def test_a_path_as_the_host_is_refused(self, phabricator):
        """A host is an origin: a sub-path would make links nobody can open."""
        with pytest.raises(PhabfiveConfigException) as error:
            Phabfive(
                token=TOKEN, config={"PHORGE_HOST": "https://phorge.example.com/x"}
            )

        assert "PHORGE_HOST is malformed" in str(error.value)


class TestFromTheEnvironment:
    """How a deployment actually configures this: two environment variables."""

    def test_the_pair_configures_an_instance(self, only_the_environment):
        only_the_environment.setenv("PHORGE_TOKEN", TOKEN)
        only_the_environment.setenv("PHORGE_HOST", WEB)
        only_the_environment.setenv("PHORGE_API_HOST", API)

        conf, _ = Phabfive.read_config()

        assert conf["PHORGE_API_HOST"] == API
        assert conf["PHORGE_HOST"] == WEB

    def test_an_endpoint_alone_is_refused(self, only_the_environment):
        only_the_environment.setenv("PHORGE_TOKEN", TOKEN)
        only_the_environment.setenv("PHORGE_API_HOST", API)

        with pytest.raises(
            PhabfiveConfigException, match="PHORGE_HOST is not configured"
        ):
            Phabfive()

    def test_an_endpoint_alone_counts_as_an_explicit_host(self, only_the_environment):
        """It names the instance to talk to, even though it is not enough."""
        only_the_environment.setenv("PHORGE_TOKEN", TOKEN)
        only_the_environment.setenv("PHORGE_API_HOST", API)

        _, explicit = Phabfive.read_config()

        assert explicit is True

    def test_the_pair_is_judged_after_every_layer_is_merged(
        self, only_the_environment, tmp_path
    ):
        """One half in a file and the other in the environment is a valid split.

        Splitting configuration across layers is the ordinary way to keep an
        address in a file and a secret in the environment. `user_config_dir`
        is patched rather than steered with XDG_CONFIG_HOME, which appdirs
        honours only on Linux.
        """
        config_file = tmp_path / "phabfive.yaml"
        config_file.write_text(f"PHORGE_HOST: {WEB}\n")
        os.chmod(config_file, 0o600)
        only_the_environment.setenv("PHORGE_TOKEN", TOKEN)
        only_the_environment.setenv("PHORGE_API_HOST", API)

        with mock.patch.object(
            appdirs, "user_config_dir", return_value=str(tmp_path / "phabfive")
        ):
            conf, _ = Phabfive.read_config()

        assert conf["PHORGE_API_HOST"] == API
        assert conf["PHORGE_HOST"] == WEB


class TestWhatIsKeyedByInstance:
    """A token and a cache belong to the endpoint they were obtained from."""

    def test_the_cache_follows_the_endpoint(self, only_the_environment):
        """Changing where readers browse must not throw the cache away."""
        only_the_environment.setenv("PHORGE_TOKEN", TOKEN)
        only_the_environment.setenv("PHORGE_API_HOST", API)

        only_the_environment.setenv("PHORGE_HOST", WEB)
        first, _ = Phabfive.read_config()
        only_the_environment.setenv("PHORGE_HOST", HOST)
        second, _ = Phabfive.read_config()

        assert cache._instance_dir(first) == cache._instance_dir(second)

    def test_adopting_the_pair_keeps_an_existing_cache(self, only_the_environment):
        """The same endpoint means the same cache, split or not."""
        only_the_environment.setenv("PHORGE_TOKEN", TOKEN)
        only_the_environment.setenv("PHORGE_HOST", API)
        plain, _ = Phabfive.read_config()

        only_the_environment.setenv("PHORGE_HOST", WEB)
        only_the_environment.setenv("PHORGE_API_HOST", API)
        split, _ = Phabfive.read_config()

        assert cache._instance_dir(plain) == cache._instance_dir(split)

    def test_the_token_in_arcrc_is_found_by_the_endpoint(
        self, only_the_environment, tmp_path
    ):
        """~/.arcrc keys a token by the address it authenticates against.

        A split configuration has to look the host up under PHORGE_API_HOST,
        or it asks for a host that was never written and finds no token.
        """
        arcrc = tmp_path / ".arcrc"
        arcrc.write_text(json.dumps({"hosts": {API: {"token": TOKEN}}}))
        os.chmod(arcrc, 0o600)
        only_the_environment.setenv("PHORGE_HOST", WEB)
        only_the_environment.setenv("PHORGE_API_HOST", API)

        conf, _ = Phabfive.read_config()

        assert conf["PHORGE_TOKEN"] == TOKEN
        assert conf["PHORGE_HOST"] == WEB
