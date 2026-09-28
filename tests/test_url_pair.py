# -*- coding: utf-8 -*-

"""Configuring an endpoint and a browsable address separately (issue #540).

`PHAB_URL` is one string doing two jobs: the Conduit endpoint phabfive calls,
and the root of every link it hands back. Those are the same address almost
everywhere, and they come apart behind an ingress or split-horizon DNS -- a
caller inside a Kubernetes cluster reaches Phorge at a Service name, while the
report it renders is read by people who need links that open in a browser.

`PHAB_API_URL` and `PHAB_WEB_URL` carry the two halves when they differ. The
tests below pin the two things that make the split safe rather than merely
possible: half a pair is refused instead of guessed at, and everything keyed by
instance -- the token in `~/.arcrc`, the cache directory -- keeps keying on the
endpoint, so adopting the split does not orphan what is already stored.
"""

import json
import os
from unittest import mock

import appdirs
import pytest

from phabfive import cache
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException


API = "http://phorge.phorge.svc.cluster.local/api/"
WEB = "http://phorge.localhost"
URL = "https://phorge.example.com/api/"
TOKEN = "api-" + "a" * 28

SETTINGS = ("PHAB_URL", "PHAB_API_URL", "PHAB_WEB_URL", "PHAB_TOKEN")


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

    def test_phab_url_alone_is_unchanged(self, phabricator):
        app = Phabfive(url=URL, token=TOKEN)

        assert app.conf["PHAB_URL"] == URL
        assert app.url == "https://phorge.example.com"

    def test_the_pair_defaults_to_unset(self, phabricator):
        """An empty pair has to mean "unset", or every existing run would fail."""
        app = Phabfive(url=URL, token=TOKEN)

        assert app.conf["PHAB_API_URL"] == ""
        assert app.conf["PHAB_WEB_URL"] == ""


class TestTwoAddresses:
    """The split case: dial one address, publish links rooted at the other."""

    def test_the_endpoint_is_dialled(self, phabricator):
        app = Phabfive(token=TOKEN, config={"PHAB_API_URL": API, "PHAB_WEB_URL": WEB})
        app.phab.client

        assert phabricator.call_args.kwargs["host"] == API

    def test_the_links_are_rooted_at_the_browsable_address(self, phabricator):
        app = Phabfive(token=TOKEN, config={"PHAB_API_URL": API, "PHAB_WEB_URL": WEB})

        assert app.url == WEB
        assert f"{app.url}/T28" == "http://phorge.localhost/T28"

    def test_phab_url_holds_the_endpoint(self, phabricator):
        """The pair resolves into PHAB_URL, so downstream reads one setting."""
        app = Phabfive(token=TOKEN, config={"PHAB_API_URL": API, "PHAB_WEB_URL": WEB})

        assert app.conf["PHAB_URL"] == API

    def test_the_endpoint_suffix_is_forgiven(self, phabricator):
        """As for PHAB_URL: the instance's address is enough."""
        app = Phabfive(
            token=TOKEN,
            config={
                "PHAB_API_URL": "http://phorge.phorge.svc.cluster.local",
                "PHAB_WEB_URL": WEB,
            },
        )

        assert app.conf["PHAB_API_URL"] == API

    def test_a_trailing_slash_on_the_browsable_address_is_dropped(self, phabricator):
        """Links are built by appending, so a kept slash would double it."""
        app = Phabfive(
            token=TOKEN,
            config={"PHAB_API_URL": API, "PHAB_WEB_URL": "http://phorge.localhost/"},
        )

        assert app.url == WEB


class TestHalfAPair:
    """Half a pair is refused, because the other half cannot be derived.

    Stripping `/api/` off an endpoint to guess the browsable address is exactly
    the assumption that fails in the only situation the pair exists for.
    """

    def test_an_endpoint_without_a_browsable_address_is_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException) as error:
            Phabfive(token=TOKEN, config={"PHAB_API_URL": API})

        assert "PHAB_API_URL is configured without PHAB_WEB_URL" in str(error.value)

    def test_a_browsable_address_without_an_endpoint_is_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException) as error:
            Phabfive(token=TOKEN, config={"PHAB_WEB_URL": WEB})

        assert "PHAB_WEB_URL is configured without PHAB_API_URL" in str(error.value)

    def test_the_refusal_says_what_to_do_instead(self, phabricator):
        with pytest.raises(PhabfiveConfigException) as error:
            Phabfive(token=TOKEN, config={"PHAB_API_URL": API})

        assert "PHAB_URL alone" in str(error.value)

    def test_an_endpoint_as_the_browsable_address_is_refused(self, phabricator):
        """The likeliest mistake is pasting the endpoint into both settings."""
        with pytest.raises(PhabfiveConfigException) as error:
            Phabfive(token=TOKEN, config={"PHAB_API_URL": API, "PHAB_WEB_URL": API})

        assert "PHAB_WEB_URL is malformed" in str(error.value)


class TestFromTheEnvironment:
    """How a deployment actually configures this: two environment variables."""

    def test_the_pair_configures_an_instance(self, only_the_environment):
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)
        only_the_environment.setenv("PHAB_API_URL", API)
        only_the_environment.setenv("PHAB_WEB_URL", WEB)

        conf, _ = Phabfive.read_config()

        assert conf["PHAB_URL"] == API
        assert conf["PHAB_WEB_URL"] == WEB

    def test_half_a_pair_is_refused(self, only_the_environment):
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)
        only_the_environment.setenv("PHAB_API_URL", API)

        with pytest.raises(PhabfiveConfigException):
            Phabfive.read_config()

    def test_the_pair_is_judged_after_every_layer_is_merged(
        self, only_the_environment, tmp_path
    ):
        """One half in a file and the other in the environment is a valid split.

        A per-layer check would reject this, and splitting configuration across
        layers is the ordinary way to keep an address in a file and a secret in
        the environment. `user_config_dir` is patched rather than steered with
        XDG_CONFIG_HOME, which appdirs honours only on Linux.
        """
        config_file = tmp_path / "phabfive.yaml"
        config_file.write_text(f"PHAB_WEB_URL: {WEB}\n")
        os.chmod(config_file, 0o600)
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)
        only_the_environment.setenv("PHAB_API_URL", API)

        with mock.patch.object(
            appdirs, "user_config_dir", return_value=str(tmp_path / "phabfive")
        ):
            conf, _ = Phabfive.read_config()

        assert conf["PHAB_URL"] == API
        assert conf["PHAB_WEB_URL"] == WEB


class TestWhatIsKeyedByInstance:
    """A token and a cache belong to the endpoint they were obtained from."""

    def test_the_cache_follows_the_endpoint(self, only_the_environment):
        """Changing where readers browse must not throw the cache away."""
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)
        only_the_environment.setenv("PHAB_API_URL", API)

        only_the_environment.setenv("PHAB_WEB_URL", WEB)
        first, _ = Phabfive.read_config()
        only_the_environment.setenv("PHAB_WEB_URL", "https://phorge.example.com")
        second, _ = Phabfive.read_config()

        assert cache._instance_dir(first) == cache._instance_dir(second)

    def test_adopting_the_pair_keeps_an_existing_cache(self, only_the_environment):
        """The same endpoint means the same cache, split or not."""
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)
        only_the_environment.setenv("PHAB_URL", API)
        plain, _ = Phabfive.read_config()

        only_the_environment.delenv("PHAB_URL")
        only_the_environment.setenv("PHAB_API_URL", API)
        only_the_environment.setenv("PHAB_WEB_URL", WEB)
        split, _ = Phabfive.read_config()

        assert cache._instance_dir(plain) == cache._instance_dir(split)

    def test_the_token_in_arcrc_is_found_by_the_endpoint(
        self, only_the_environment, tmp_path
    ):
        """~/.arcrc keys a token by the address it authenticates against.

        A split configuration has to look the host up under PHAB_API_URL, or it
        asks for a host that was never written and finds no token.
        """
        arcrc = tmp_path / ".arcrc"
        arcrc.write_text(json.dumps({"hosts": {API: {"token": TOKEN}}}))
        os.chmod(arcrc, 0o600)
        only_the_environment.setenv("PHAB_API_URL", API)
        only_the_environment.setenv("PHAB_WEB_URL", WEB)

        conf, _ = Phabfive.read_config()

        assert conf["PHAB_TOKEN"] == TOKEN
        assert conf["PHAB_WEB_URL"] == WEB
