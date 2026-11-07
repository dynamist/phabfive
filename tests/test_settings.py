# -*- coding: utf-8 -*-

"""The settings' names, and the names they had before.

phabfive is configured the way glab is: `PHORGE_HOST` and `PHORGE_TOKEN`, with
`PHORGE_*` beside them. They used to be `PHAB_URL`, `PHAB_TOKEN` and `PHAB_*`,
and a configuration written for those still has to work - from the
environment, from a yaml file and from a program's `config` dict - with a
warning that says what to write instead, once per run rather than once per
read.
"""

import json
import logging
import os
from unittest import mock

import appdirs
import pytest

from phabfive import cache, settings
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException
from phabfive.profiler import profile_dir, profile_keep, profiler_origin
from phabfive.settings import deprecation_message, env_setting, modernize

HOST = "https://phorge.example.com"
URL = f"{HOST}/api/"
API = "http://web.phorge.svc.cluster.local/api/"
WEB = "http://phorge.localhost"
TOKEN = "api-" + "a" * 28
OTHER_TOKEN = "api-" + "b" * 28


@pytest.fixture
def phabricator():
    with mock.patch("phabfive.core.Phabricator") as factory:
        yield factory


@pytest.fixture
def only_the_environment(monkeypatch, tmp_path):
    """Configure from the environment alone, with no files to discover."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in list(os.environ):
        if name.startswith(("PHAB_", "PHORGE_")):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


@pytest.fixture
def warnings_logged(caplog):
    """The deprecation warnings a test logs, as their messages."""
    caplog.set_level(logging.WARNING, logger="phabfive.settings")

    def messages():
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == "phabfive.settings"
        ]

    return messages


class TestRenaming:
    def test_every_legacy_key_is_renamed(self):
        renamed, found = modernize({"PHAB_TOKEN": TOKEN, "PHAB_SPACE": "S2"})

        assert renamed == {"PHORGE_TOKEN": TOKEN, "PHORGE_SPACE": "S2"}
        assert found == ["PHAB_TOKEN", "PHAB_SPACE"]

    def test_other_keys_are_kept(self):
        renamed, found = modernize({"PHORGE_SPACE": "S2", "EDITOR": "vi"})

        assert renamed == {"PHORGE_SPACE": "S2", "EDITOR": "vi"}
        assert found == []

    def test_the_new_name_wins_over_the_old(self):
        renamed, _ = modernize({"PHAB_SPACE": "S2", "PHORGE_SPACE": "S3"})

        assert renamed["PHORGE_SPACE"] == "S3"

    def test_phab_url_becomes_the_host(self):
        renamed, _ = modernize({"PHAB_URL": URL})

        assert renamed == {"PHORGE_HOST": URL}

    def test_the_web_half_of_the_old_pair_wins_over_phab_url(self):
        """As the pair used to win over PHAB_URL."""
        renamed, _ = modernize(
            {"PHAB_URL": URL, "PHAB_WEB_URL": WEB, "PHAB_API_URL": API}
        )

        assert renamed == {"PHORGE_HOST": WEB, "PHORGE_API_HOST": API}

    def test_the_message_names_the_successors(self):
        message = deprecation_message(["PHAB_TOKEN", "PHAB_URL"], "the environment")

        assert message == (
            "PHAB_URL and PHAB_TOKEN in the environment are deprecated: "
            "use PHORGE_HOST and PHORGE_TOKEN instead"
        )

    def test_the_message_for_one_name(self):
        message = deprecation_message(["PHAB_SPACE"], "config")

        assert message == "PHAB_SPACE in config is deprecated: use PHORGE_SPACE instead"


class TestFromTheEnvironment:
    def test_the_old_names_still_configure_an_instance(
        self, only_the_environment, phabricator
    ):
        only_the_environment.setenv("PHAB_URL", URL)
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)
        only_the_environment.setenv("PHAB_SPACE", "S2")

        app = Phabfive()
        app.phab.client

        assert app.conf["PHORGE_HOST"] == HOST
        assert app.conf["PHORGE_API_HOST"] == URL
        assert app.conf["PHORGE_TOKEN"] == TOKEN
        assert app.conf["PHORGE_SPACE"] == "S2"
        assert phabricator.call_args.kwargs["host"] == URL

    def test_the_old_names_are_warned_about(
        self, only_the_environment, warnings_logged
    ):
        only_the_environment.setenv("PHAB_URL", URL)
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)

        Phabfive.read_config()

        assert warnings_logged() == [
            "PHAB_URL and PHAB_TOKEN in the environment are deprecated: "
            "use PHORGE_HOST and PHORGE_TOKEN instead"
        ]

    def test_the_new_names_are_not(self, only_the_environment, warnings_logged):
        only_the_environment.setenv("PHORGE_HOST", HOST)
        only_the_environment.setenv("PHORGE_TOKEN", TOKEN)

        Phabfive.read_config()

        assert warnings_logged() == []

    def test_the_warning_is_given_once_per_run(
        self, only_the_environment, warnings_logged
    ):
        """The cache, the profiler and the app each read the configuration."""
        only_the_environment.setenv("PHAB_URL", URL)
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)

        Phabfive.read_config()
        Phabfive.read_config()

        assert len(warnings_logged()) == 1

    def test_the_new_name_wins_over_the_old(self, only_the_environment):
        only_the_environment.setenv("PHAB_URL", "https://old.example.com/api/")
        only_the_environment.setenv("PHORGE_HOST", HOST)
        only_the_environment.setenv("PHORGE_TOKEN", TOKEN)

        conf, _ = Phabfive.read_config()

        assert conf["PHORGE_HOST"] == HOST

    def test_the_old_pair_becomes_host_and_api_host(self, only_the_environment):
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)
        only_the_environment.setenv("PHAB_API_URL", API)
        only_the_environment.setenv("PHAB_WEB_URL", WEB)

        conf, _ = Phabfive.read_config()

        assert conf["PHORGE_HOST"] == WEB
        assert conf["PHORGE_API_HOST"] == API

    def test_the_old_name_still_matches_the_token_in_arcrc(
        self, only_the_environment, tmp_path
    ):
        arcrc = tmp_path / ".arcrc"
        arcrc.write_text(
            json.dumps(
                {
                    "hosts": {
                        URL: {"token": TOKEN},
                        "https://other.example.com/api/": {"token": OTHER_TOKEN},
                    }
                }
            )
        )
        os.chmod(arcrc, 0o600)
        only_the_environment.setenv("PHAB_URL", URL)

        conf, explicit = Phabfive.read_config()

        assert conf["PHORGE_TOKEN"] == TOKEN
        assert explicit is True

    def test_the_old_name_keeps_an_existing_cache(self, only_the_environment):
        """Renaming the variables must not orphan what was cached under them."""
        only_the_environment.setenv("PHAB_URL", URL)
        only_the_environment.setenv("PHAB_TOKEN", TOKEN)
        before, _ = Phabfive.read_config()

        only_the_environment.delenv("PHAB_URL")
        only_the_environment.delenv("PHAB_TOKEN")
        only_the_environment.setenv("PHORGE_HOST", HOST)
        only_the_environment.setenv("PHORGE_TOKEN", TOKEN)
        after, _ = Phabfive.read_config()

        assert cache._instance_dir(before) == cache._instance_dir(after)


class TestFromAYamlFile:
    @pytest.fixture
    def user_config(self, only_the_environment, tmp_path):
        """A ~/.config/phabfive.yaml, by its content."""
        config_file = tmp_path / "phabfive.yaml"

        def write(text):
            config_file.write_text(text)
            os.chmod(config_file, 0o600)
            return mock.patch.object(
                appdirs, "user_config_dir", return_value=str(tmp_path / "phabfive")
            )

        return write

    def test_the_old_keys_still_configure_an_instance(self, user_config):
        with user_config(f"PHAB_URL: {URL}\nPHAB_TOKEN: {TOKEN}\nPHAB_SPACE: S2\n"):
            conf, _ = Phabfive.read_config()

        assert conf["PHORGE_HOST"] == HOST
        assert conf["PHORGE_TOKEN"] == TOKEN
        assert conf["PHORGE_SPACE"] == "S2"

    def test_the_old_keys_are_warned_about_by_file(self, user_config, warnings_logged):
        with user_config("PHAB_SPACE: S2\nPHAB_CACHE: false\n"):
            Phabfive.read_config()

        messages = warnings_logged()
        assert len(messages) == 1
        assert messages[0].startswith("PHAB_SPACE and PHAB_CACHE in ")
        assert messages[0].endswith(
            "phabfive.yaml are deprecated: use PHORGE_SPACE and PHORGE_CACHE instead"
        )

    def test_credentials_in_yaml_are_still_discouraged(
        self, user_config, warnings_logged
    ):
        """The older deprecation of a token in yaml names the new keys."""
        with user_config(f"PHORGE_HOST: {HOST}\nPHORGE_TOKEN: {TOKEN}\n"):
            with mock.patch("phabfive.core.log") as log:
                Phabfive.read_config()

        message = log.warning.call_args.args[0]
        assert "PHORGE_HOST/PHORGE_TOKEN" in message
        assert "phabfive user setup" in message
        assert warnings_logged() == []


class TestFromAProgram:
    def test_the_old_keys_still_configure_an_instance(self, phabricator):
        with pytest.warns(DeprecationWarning):
            app = Phabfive(
                config={"PHAB_URL": URL, "PHAB_TOKEN": TOKEN, "PHAB_SPACE": "S2"}
            )

        assert app.conf["PHORGE_HOST"] == HOST
        assert app.conf["PHORGE_TOKEN"] == TOKEN
        assert app.conf["PHORGE_SPACE"] == "S2"

    def test_the_warning_is_a_deprecation_warning_naming_the_caller(self, phabricator):
        with pytest.warns(DeprecationWarning) as caught:
            Phabfive(url=HOST, token=TOKEN, config={"PHAB_SPACE": "S2"})

        assert len(caught) == 1
        assert str(caught[0].message) == (
            "PHAB_SPACE in config is deprecated: use PHORGE_SPACE instead"
        )
        assert caught[0].filename == __file__

    def test_the_new_keys_warn_about_nothing(self, phabricator, recwarn):
        Phabfive(url=HOST, token=TOKEN, config={"PHORGE_SPACE": "S2"})

        assert [w for w in recwarn if issubclass(w.category, DeprecationWarning)] == []

    def test_an_unknown_key_is_still_refused(self, phabricator):
        with pytest.raises(PhabfiveConfigException, match="PHAB_SPCAE"):
            Phabfive(url=HOST, token=TOKEN, config={"PHAB_SPCAE": "S2"})


class TestReadStraightFromTheEnvironment:
    """The cache and the profiler read a setting without loading the rest."""

    def test_the_new_name_is_read(self, monkeypatch):
        monkeypatch.setenv("PHORGE_CACHE", "0")
        monkeypatch.delenv("PHAB_CACHE", raising=False)

        assert env_setting("PHORGE_CACHE") == "0"
        assert cache.enabled() is False

    def test_the_old_name_is_read_with_a_warning(self, monkeypatch, warnings_logged):
        monkeypatch.delenv("PHORGE_CACHE", raising=False)
        monkeypatch.setenv("PHAB_CACHE", "0")

        assert env_setting("PHORGE_CACHE") == "0"
        assert warnings_logged() == [
            "PHAB_CACHE in the environment is deprecated: use PHORGE_CACHE instead"
        ]

    def test_the_new_name_wins(self, monkeypatch, warnings_logged):
        monkeypatch.setenv("PHORGE_CACHE", "1")
        monkeypatch.setenv("PHAB_CACHE", "0")

        assert env_setting("PHORGE_CACHE") == "1"
        assert warnings_logged() == []

    def test_unset_is_none(self, monkeypatch):
        monkeypatch.delenv("PHORGE_CACHE", raising=False)
        monkeypatch.delenv("PHAB_CACHE", raising=False)

        assert env_setting("PHORGE_CACHE") is None

    def test_the_cache_directory_honours_the_old_name(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PHORGE_CACHE_DIR", raising=False)
        monkeypatch.setenv("PHAB_CACHE_DIR", str(tmp_path / "old"))

        assert cache.root().startswith(str(tmp_path / "old"))

    def test_the_profiler_honours_the_old_names(self, monkeypatch, tmp_path):
        for name in (
            "PHORGE_PROFILE_DIR",
            "PHORGE_PROFILE_KEEP",
            "PHORGE_PROFILER_ORIGIN",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("PHAB_PROFILE_DIR", str(tmp_path / "profiles"))
        monkeypatch.setenv("PHAB_PROFILE_KEEP", "3")
        monkeypatch.setenv("PHAB_PROFILER_ORIGIN", "https://profiler.example.com/")

        assert profile_dir() == tmp_path / "profiles"
        assert profile_keep() == 3
        assert profiler_origin() == "https://profiler.example.com"

    def test_the_profiler_warns_once_per_name(self, monkeypatch, warnings_logged):
        """A name the app's read already warned about is not warned about again."""
        monkeypatch.delenv("PHORGE_PROFILE_DIR", raising=False)
        monkeypatch.setenv("PHAB_PROFILE_DIR", "/tmp/profiles")
        settings.warn_legacy(["PHAB_URL", "PHAB_PROFILE_DIR"], "the environment")

        profile_dir()

        assert len(warnings_logged()) == 1


class TestWarningOnce:
    def test_each_name_is_warned_about_once_per_place(self, warnings_logged):
        settings.warn_legacy(["PHAB_URL"], "the environment")
        settings.warn_legacy(["PHAB_URL", "PHAB_TOKEN"], "the environment")
        settings.warn_legacy(["PHAB_URL"], "config")

        assert warnings_logged() == [
            "PHAB_URL in the environment is deprecated: use PHORGE_HOST instead",
            "PHAB_TOKEN in the environment is deprecated: use PHORGE_TOKEN instead",
            "PHAB_URL in config is deprecated: use PHORGE_HOST instead",
        ]

    def test_a_forgotten_warning_is_given_again(self, warnings_logged):
        settings.warn_legacy(["PHAB_URL"], "the environment")
        settings.forget_warnings()
        settings.warn_legacy(["PHAB_URL"], "the environment")

        assert len(warnings_logged()) == 2

    def test_a_quiet_read_neither_warns_nor_remembers(self, warnings_logged):
        with settings.quiet():
            settings.warn_legacy(["PHAB_URL"], "the environment")
        assert warnings_logged() == []

        settings.warn_legacy(["PHAB_URL"], "the environment")
        assert len(warnings_logged()) == 1

    def test_the_cache_reads_quietly(self, monkeypatch, warnings_logged):
        """Completion must not nag: the command warns in the cache's place."""
        monkeypatch.delenv("PHORGE_CACHE", raising=False)
        monkeypatch.setenv("PHAB_CACHE", "0")

        assert cache.enabled() is False
        assert warnings_logged() == []

    def test_the_profile_switch_reads_quietly(self, monkeypatch, warnings_logged):
        """It runs before the command has set logging up."""
        from phabfive.cli.profile import wants_profile

        monkeypatch.delenv("PHORGE_PROFILE", raising=False)
        monkeypatch.setenv("PHAB_PROFILE", "1")

        assert wants_profile([]) is True
        assert warnings_logged() == []
