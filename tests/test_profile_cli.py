# -*- coding: utf-8 -*-
"""Recording a command with --profile or PHAB_PROFILE, and `phabfive profile`."""

import json
import sys
import threading
import urllib.error
import urllib.request
from unittest import mock
from urllib.parse import unquote

import pytest
from typer.testing import CliRunner

from phabfive.cli import _command_name, _command_words, app, cli_entrypoint
from phabfive.cli import profile as profile_cli
from phabfive.cli import preprocess_monograms
from phabfive.cli.profile import ProfileServer, begin, describe_profile, wants_profile
from phabfive.profiler import Profiler, list_profiles, profiler

ORIGIN = "https://profiler.example.com"


@pytest.fixture
def profiles(tmp_path):
    """The directory conftest points PHAB_PROFILE_DIR at."""
    return tmp_path / "profiles"


def run_entrypoint(monkeypatch, *args):
    """Run the command as `phabfive <args>` would, returning its exit status."""
    monkeypatch.setattr(sys, "argv", ["phabfive", *args])
    try:
        cli_entrypoint()
    except SystemExit as e:
        return e.code or 0
    return 0


class TestWhenARunIsRecorded:
    def test_a_plain_run_is_not(self):
        assert begin(["phabfive", "cache", "info"], ["cache", "info"]) is None

    def test_profile_asks_for_one_run(self):
        assert begin(["phabfive", "--profile", "cache", "info"], ["cache", "info"])

    def test_phab_profile_in_the_environment_asks_for_every_run(self, monkeypatch):
        monkeypatch.setenv("PHAB_PROFILE", "true")
        assert begin(["phabfive", "cache", "info"], ["cache", "info"])

    def test_phab_profile_in_the_configuration_asks_for_every_run(self, monkeypatch):
        monkeypatch.delenv("PHAB_PROFILE")
        with mock.patch.object(
            profile_cli, "_read_config", return_value={"PHAB_PROFILE": True}
        ):
            assert wants_profile(["cache", "info"]) is True
        with mock.patch.object(profile_cli, "_read_config", return_value={}):
            assert wants_profile(["cache", "info"]) is False

    def test_the_environment_spares_reading_the_configuration(self, monkeypatch):
        monkeypatch.setenv("PHAB_PROFILE", "1")
        with mock.patch.object(profile_cli, "_read_config") as read:
            assert wants_profile([]) is True
        read.assert_not_called()

    @pytest.mark.parametrize(
        "args",
        [
            ["--profile", "--help"],
            ["--profile", "maniphest", "--help"],
            ["--profile", "--version"],
            ["--profile", "--skill"],
        ],
    )
    def test_options_that_run_nothing_are_not(self, args):
        assert begin(["phabfive", *args], _command_words(["phabfive", *args])) is None

    @pytest.mark.parametrize("command", ["profile", "repl"])
    def test_profile_and_repl_are_not(self, command):
        assert begin(["phabfive", "--profile", command], [command]) is None

    def test_shell_completion_is_not(self, monkeypatch):
        monkeypatch.setenv("_PHABFIVE_COMPLETE", "complete_bash")
        assert begin(["phabfive", "--profile", "cache"], ["cache"]) is None

    def test_arguments_after_a_double_dash_are_not_options(self):
        argv = ["phabfive", "maniphest", "comment", "T1", "--", "--profile"]
        assert begin(argv, ["maniphest", "comment", "T1"]) is None


class TestNamingTheCommand:
    def test_the_words_skip_options_and_their_values(self):
        argv = [
            "phabfive",
            "-vv",
            "--profile",
            "--format",
            "json",
            "maniphest",
            "search",
            "x",
        ]
        assert _command_words(argv) == ["maniphest", "search", "x"]

    def test_the_name_is_the_command_path_click_resolves(self):
        assert _command_name(["maniphest", "show", "T1"]) == "maniphest show"
        assert (
            _command_name(["diffusion", "repo", "show", "R1"]) == "diffusion repo show"
        )
        assert _command_name(["nonsense"]) == ""

    def test_a_monogram_after_profile_still_expands(self):
        assert preprocess_monograms(["phabfive", "--profile", "T123"]) == [
            "phabfive",
            "--profile",
            "maniphest",
            "show",
            "T123",
        ]


class TestARecordedRun:
    def test_profile_saves_one_and_says_where(self, monkeypatch, profiles, capsys):
        status = run_entrypoint(
            monkeypatch, "--profile", "--format=json", "cache", "info"
        )

        assert status == 0
        (path,) = list_profiles(profiles)
        assert f"Profile saved to {path}" in capsys.readouterr().err
        saved = json.loads(path.read_text())
        assert saved["threads"][0]["name"] == "phabfive cache info"
        markers = saved["threads"][0]["markers"]
        names = [saved["shared"]["stringArray"][i] for i in markers["name"]]
        assert "Loading Python modules" in names
        phase = markers["data"][names.index("cache info")]
        assert phase == {"type": "Phase", "exitCode": 0}

    def test_a_plain_run_saves_nothing(self, monkeypatch, profiles):
        assert run_entrypoint(monkeypatch, "--format=json", "cache", "info") == 0
        assert list_profiles(profiles) == []

    def test_the_exit_status_is_kept_and_recorded(self, monkeypatch, profiles):
        status = run_entrypoint(monkeypatch, "--profile", "cache", "clear", "nonsense")

        assert status == 1
        (path,) = list_profiles(profiles)
        saved = json.loads(path.read_text())
        exit_codes = [
            data.get("exitCode")
            for data in saved["threads"][0]["markers"]["data"]
            if data["type"] == "Phase"
        ]
        assert 1 in exit_codes

    def test_quiet_keeps_the_notice_off_stderr(self, monkeypatch, profiles, capsys):
        run_entrypoint(monkeypatch, "-q", "--profile", "--format=json", "cache", "info")
        assert "Profile saved" not in capsys.readouterr().err
        assert len(list_profiles(profiles)) == 1

    def test_a_profile_that_cannot_be_saved_is_a_warning(self, monkeypatch, capsys):
        monkeypatch.setenv("PHAB_PROFILE_KEEP", "-1")
        status = run_entrypoint(
            monkeypatch, "--profile", "--format=json", "cache", "info"
        )
        assert status == 0
        assert "the profile was not saved: PHAB_PROFILE_KEEP" in capsys.readouterr().err

    def test_the_recorder_is_off_afterwards(self, monkeypatch):
        run_entrypoint(monkeypatch, "--profile", "--format=json", "cache", "info")
        assert profiler.enabled is False


def _saved(profiles, name="profile-20261026-101500-1.json", command="phabfive x"):
    p = Profiler()
    p.start(command)
    profiles.mkdir(parents=True, exist_ok=True)
    path = p.save(profiles)
    return path.rename(profiles / name)


runner = CliRunner()


class TestList:
    def test_json_lists_each_profile(self, profiles):
        older = _saved(
            profiles, "profile-20261026-101500-1.json", "phabfive cache info"
        )
        newer = _saved(
            profiles, "profile-20261026-101600-1.json", "phabfive user whoami"
        )

        result = runner.invoke(app, ["--format=json", "profile", "--list"])

        assert result.exit_code == 0, result.output
        records = json.loads(result.output)
        assert [r["Path"] for r in records] == [str(older), str(newer)]
        assert [r["Command"] for r in records] == [
            "phabfive cache info",
            "phabfive user whoami",
        ]
        assert all(isinstance(r["Duration"], float) for r in records)

    def test_value_prints_the_paths(self, profiles):
        path = _saved(profiles)
        result = runner.invoke(app, ["--format=value", "profile", "--list"])
        assert result.output == f"{path}\n"

    def test_nothing_saved_says_how_to_record(self, profiles):
        result = runner.invoke(app, ["--format=rich", "profile", "--list"])
        assert "phabfive --profile <command>" in result.output

    def test_an_unreadable_profile_is_still_listed(self, profiles):
        profiles.mkdir(parents=True)
        broken = profiles / "profile-20261026-101500-1.json"
        broken.write_text("{not json")
        assert describe_profile(broken)["Command"] is None


class TestOpen:
    def test_no_profiles_is_an_error(self, profiles):
        result = runner.invoke(app, ["profile", "--no-browser"])
        assert result.exit_code == 1
        assert "no profiles" in result.output

    def test_a_missing_file_is_an_error(self, tmp_path):
        result = runner.invoke(app, ["profile", str(tmp_path / "nope.json")])
        assert result.exit_code == 1

    def test_the_browser_is_sent_to_the_newest_profile(self, profiles, monkeypatch):
        _saved(profiles, "profile-20261026-101500-1.json")
        newest = _saved(profiles, "profile-20261026-101600-1.json")
        monkeypatch.setenv("PHAB_PROFILER_ORIGIN", ORIGIN + "/")
        fetched = {}
        fetchers = []

        def browser(url):
            # What the profiler page does with a /from-url/ address
            assert url.startswith(f"{ORIGIN}/from-url/")
            assert url.endswith("/marker-chart/")
            profile_url = unquote(
                url[len(f"{ORIGIN}/from-url/") : -len("/marker-chart/")]
            )

            def fetch():
                with urllib.request.urlopen(profile_url) as response:
                    fetched["origin"] = response.headers["Access-Control-Allow-Origin"]
                    fetched["body"] = response.read()

            fetchers.append(threading.Thread(target=fetch))
            fetchers[-1].start()
            return True

        with mock.patch("webbrowser.open_new_tab", side_effect=browser):
            result = runner.invoke(app, ["profile", "--timeout", "10"])
        fetchers[0].join(5)

        assert result.exit_code == 0, result.output
        assert fetched["origin"] == ORIGIN
        assert fetched["body"] == newest.read_bytes()

    def test_nobody_fetching_it_times_out(self, profiles):
        _saved(profiles)
        with mock.patch.object(ProfileServer, "serve_once", return_value=False):
            result = runner.invoke(app, ["profile", "--no-browser"])
        assert result.exit_code == 1
        assert "did not fetch" in result.output


class TestProfileServer:
    @pytest.fixture
    def server(self, profiles):
        server = ProfileServer(_saved(profiles), ORIGIN)
        yield server
        server.server_close()

    def _serve_in_background(self, server):
        thread = threading.Thread(target=server.serve_once, args=(10,))
        thread.start()
        return thread

    def test_it_listens_on_loopback_only(self, server):
        assert server.server_address[0] == "127.0.0.1"
        assert server.profile_url.startswith("http://127.0.0.1:")

    def test_the_profile_is_served_once_to_the_origin(self, server):
        thread = self._serve_in_background(server)
        with urllib.request.urlopen(server.profile_url) as response:
            assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
            assert response.headers["Cache-Control"] == "no-store"
            assert json.loads(response.read())["meta"]
        thread.join(5)
        assert server.served is True
        assert not thread.is_alive()

    def test_any_other_path_is_not_found(self, server):
        thread = self._serve_in_background(server)
        base = server.profile_url.rsplit("/", 1)[0]
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/etc/passwd")
        assert error.value.code == 404
        assert server.served is False
        urllib.request.urlopen(server.profile_url).read()
        thread.join(5)

    def test_it_gives_up_after_the_timeout(self, server):
        assert server.serve_once(0.2) is False

    def test_the_profiler_address_encodes_the_whole_profile_url(self, server):
        url = server.profiler_url()
        encoded = url[len(f"{ORIGIN}/from-url/") :].split("/")[0]
        assert unquote(encoded) == server.profile_url
        assert "/" not in encoded


class TestSummary:
    def test_json_summarises_the_newest_profile(self, profiles):
        _saved(profiles, "profile-20261026-101500-1.json", "phabfive cache info")
        newest = _saved(
            profiles, "profile-20261026-101600-1.json", "phabfive user whoami"
        )

        result = runner.invoke(app, ["--format=json", "profile", "--summary"])

        assert result.exit_code == 0, result.output
        summary = json.loads(result.output)
        assert summary["Path"] == str(newest)
        assert summary["Command"] == "phabfive user whoami"
        assert set(summary) >= {"Duration", "Time", "Conduit", "Sleeps", "Problems"}

    def test_a_person_gets_tables(self, profiles):
        path = _saved(profiles)
        result = runner.invoke(
            app, ["--format=rich", "profile", "--summary", str(path)]
        )
        assert result.exit_code == 0, result.output
        assert "Where the time went" in result.output

    def test_it_never_opens_a_browser(self, profiles):
        _saved(profiles)
        with mock.patch("webbrowser.open_new_tab") as browser:
            runner.invoke(app, ["--format=json", "profile", "--summary"])
        browser.assert_not_called()

    def test_a_file_that_is_not_a_profile_is_an_error(self, tmp_path):
        other = tmp_path / "other.json"
        other.write_text('{"hello": 1}')
        result = runner.invoke(app, ["profile", "--summary", str(other)])
        assert result.exit_code == 1
        assert "not a profile phabfive wrote" in result.output

    def test_list_and_summary_do_not_combine(self):
        result = runner.invoke(app, ["profile", "--list", "--summary"])
        assert result.exit_code == 1
