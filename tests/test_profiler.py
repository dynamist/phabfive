# -*- coding: utf-8 -*-
"""The recorder and the profiles it builds.

The format is checked through phabfive's own builder: the tables hold
together the way the processed profile format says they must, rather than
being compared with a profile from anywhere else.
"""

import json
import logging
import os
import stat
import threading
import time

import pytest

from phabfive.exceptions import PhabfiveInputException
from phabfive.profiler import (
    PROCESSED_PROFILE_VERSION,
    STARTUP_PHASE,
    Category,
    Interval,
    MarkerPhase,
    Profiler,
    ProfilerHandler,
    build_segments,
    describe_params,
    list_profiles,
    profile_dir,
    profile_keep,
    profiler,
    profiler_origin,
    redact_text,
)

# The scripted transport tests/test_retry.py runs the real client against
from tests import test_retry
from tests.test_retry import conduit, ok, status

TOKEN = "api-" + "a" * 28


@pytest.fixture
def recorder():
    p = Profiler()
    p.start("phabfive test")
    return p


class FakeClock:
    """Moves `time.perf_counter` only when told to."""

    def __init__(self, monkeypatch):
        self.now = 1000.0
        monkeypatch.setattr("phabfive.profiler.time.perf_counter", lambda: self.now)

    def advance(self, ms):
        self.now += ms / 1000


@pytest.fixture
def clock(monkeypatch):
    return FakeClock(monkeypatch)


@pytest.fixture
def timed(clock):
    p = Profiler()
    p.start("phabfive test")
    return p


def interval(start, end, category, *stack):
    from phabfive.profiler import WAITING_PRIORITY

    return Interval(start, end, tuple(stack), category, WAITING_PRIORITY[category])


class TestDisabled:
    def test_the_shared_recorder_is_off_by_default(self):
        assert Profiler().enabled is False
        assert profiler.enabled is False

    def test_nothing_is_recorded_while_off(self):
        p = Profiler()
        with p.phase("a"), p.sleep("b"), p.user_input("c"), p.cache("read", "users"):
            pass
        with p.network("user.whoami", "https://x/api/user.whoami") as data:
            data["responseStatus"] = 200
        p.instant("d", Category.OTHER, "Log")
        p.log(logging.makeLogRecord({"msg": "e"}))
        assert p.markers == []

    def test_a_disabled_network_block_still_raises(self):
        with pytest.raises(RuntimeError):
            with Profiler().network("m", "u"):
                raise RuntimeError


class TestMarkers:
    def test_an_interval_marker_spans_its_block(self, timed, clock):
        with timed.phase("Configuring") as data:
            clock.advance(5)
            data["exitCode"] = 0

        (marker,) = timed.markers
        assert marker.name == "Configuring"
        assert marker.category == Category.PHASE
        assert marker.end_time - marker.start_time == pytest.approx(5)
        assert marker.data == {"type": "Phase", "exitCode": 0}

    def test_a_marker_is_kept_when_its_block_raises(self, recorder):
        with pytest.raises(ValueError):
            with recorder.sleep("retry 1: maniphest.search"):
                raise ValueError
        assert recorder.markers[0].data["reason"] == "retry 1: maniphest.search"

    def test_start_moves_the_clock_back(self, clock):
        p = Profiler()
        p.start("x", start_wall=time.time() - 0.2, start_perf=clock.now - 0.2)
        p.add_startup_phase()
        (marker,) = p.markers
        assert marker.name == STARTUP_PHASE
        assert (marker.start_time, marker.end_time) == (0.0, pytest.approx(200))

    def test_start_drops_an_earlier_recording(self, recorder):
        recorder.instant("old", Category.OTHER, "Log")
        recorder.start("again")
        assert recorder.markers == []

    def test_a_question_is_recorded_without_its_answer(self, recorder):
        with recorder.user_input("Apply T1? [y,n,a,q,?]"):
            pass
        assert recorder.markers[0].data == {
            "type": "UserInput",
            "question": "Apply T1? [y,n,a,q,?]",
        }

    def test_a_command_is_recorded_by_its_program_only(self, recorder):
        with recorder.command(["/usr/bin/vim"]) as data:
            data["exitCode"] = 0
        assert recorder.markers[0].data == {
            "type": "Command",
            "command": "vim",
            "exitCode": 0,
        }

    def test_markers_from_another_thread_say_which(self, recorder):
        worker = threading.Thread(
            target=lambda: recorder.instant("x", Category.OTHER, "Log"),
            name="fetcher",
        )
        worker.start()
        worker.join()
        recorder.instant("y", Category.OTHER, "Log")

        assert recorder.markers[0].data["thread"] == "fetcher"
        assert "thread" not in recorder.markers[1].data


class TestNetwork:
    def test_a_call_is_a_start_and_a_stop_with_one_id(self, timed, clock):
        with timed.network("user.whoami", "https://x/api/user.whoami"):
            clock.advance(30)

        start, stop = timed.markers
        assert start.data["status"] == "STATUS_START"
        assert stop.data["status"] == "STATUS_STOP"
        assert start.data["id"] == stop.data["id"] == 1
        assert start.name == stop.name == "Load 1: https://x/api/user.whoami"
        assert start.end_time == stop.start_time
        assert stop.end_time - stop.start_time == pytest.approx(30)
        assert stop.data["responseStatus"] == 200

    def test_each_call_gets_its_own_id(self, recorder):
        for _ in range(2):
            with recorder.network("m", "u"):
                pass
        assert [m.data["id"] for m in recorder.markers] == [1, 1, 2, 2]

    def test_a_failed_call_is_cancelled_and_raised(self, recorder):
        from phabfive.exceptions import PhabfiveConnectionException

        with pytest.raises(PhabfiveConnectionException):
            with recorder.network("m", "u"):
                raise PhabfiveConnectionException("down", status=503)

        stop = recorder.markers[-1].data
        assert stop["status"] == "STATUS_CANCEL"
        assert stop["requestStatus"] == "PhabfiveConnectionException"
        assert stop["responseStatus"] == 503

    def test_the_parameters_ride_on_the_uri(self, recorder):
        with recorder.network("m", "https://x/api/m", {"limit": 5}):
            pass
        assert recorder.markers[0].data["URI"] == "https://x/api/m?limit=5"


class TestDescribeParams:
    def test_nested_values_are_flattened_as_on_the_wire(self):
        described = describe_params(
            {"constraints": {"projects": ["a b", "c"]}, "limit": 2}
        )
        assert described == (
            "constraints[projects][0]=a%20b&constraints[projects][1]=c&limit=2"
        )

    @pytest.mark.parametrize(
        "key",
        [
            "api.token",
            "secret",
            "password",
            "description",
            "comment",
            "content",
            "text",
        ],
    )
    def test_private_values_are_left_out(self, key):
        assert describe_params({key: "hush"}) == f"{key}=%E2%80%A6"

    def test_transactions_keep_only_their_type(self):
        described = describe_params(
            {
                "objectIdentifier": "T1",
                "transactions": [
                    {"type": "description", "value": "the whole body"},
                    {"type": "status", "value": "resolved"},
                ],
            }
        )
        assert "body" not in described
        assert "resolved" not in described
        assert "transactions[0][type]=description" in described
        assert "transactions[1][type]=status" in described

    def test_long_values_and_long_strings_are_clipped(self):
        assert len(describe_params({"q": "x" * 500})) < 120
        assert len(describe_params({f"k{i}": "v" * 90 for i in range(50)})) == 1000

    def test_nothing_is_empty(self):
        assert describe_params(None) == ""
        assert describe_params({}) == ""


class TestRedactText:
    def test_tokens_are_shortened_to_their_kind(self):
        assert redact_text(f"using {TOKEN} now") == "using api-… now"
        assert redact_text("cli-" + "b" * 28) == "cli-…"

    def test_other_text_is_kept(self):
        assert redact_text("api-docs and cli-tools") == "api-docs and cli-tools"


class TestLogHandler:
    def test_a_record_becomes_a_redacted_instant_marker(self, recorder):
        logger = logging.getLogger("phabfive.test.profiler")
        handler = ProfilerHandler(recorder)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.debug("token %s", TOKEN)
        finally:
            logger.removeHandler(handler)

        (marker,) = recorder.markers
        assert marker.end_time is None
        assert marker.category == Category.LOGGING
        assert marker.data == {
            "type": "Log",
            "level": "DEBUG",
            "logger": "phabfive.test.profiler",
            "message": "token api-…",
        }


class TestSegments:
    def test_uncovered_time_is_phabfives_own(self):
        assert [
            (s.start_time, s.end_time, s.stack) for s in build_segments([], 10)
        ] == [(0.0, 10.0, ())]

    def test_segments_cover_the_whole_run_in_order(self):
        segments = build_segments(
            [
                interval(2, 4, Category.NETWORK, "Conduit", "a"),
                interval(6, 8, Category.NETWORK, "Conduit", "b"),
            ],
            10,
        )
        assert [(s.start_time, s.end_time) for s in segments] == [
            (0, 2),
            (2, 4),
            (4, 6),
            (6, 8),
            (8, 10),
        ]
        assert [s.stack for s in segments][1::2] == [("Conduit", "a"), ("Conduit", "b")]

    def test_a_backoff_inside_a_call_takes_its_time(self):
        segments = build_segments(
            [
                interval(0, 10, Category.NETWORK, "Conduit", "m"),
                interval(3, 6, Category.SLEEP, "Sleep", "retry 1: m"),
            ],
            10,
        )
        assert [(s.start_time, s.end_time, s.category) for s in segments] == [
            (0, 3, Category.NETWORK),
            (3, 6, Category.SLEEP),
            (6, 10, Category.NETWORK),
        ]

    def test_between_equals_the_innermost_wins(self):
        segments = build_segments(
            [
                interval(0, 10, Category.NETWORK, "Conduit", "outer"),
                interval(4, 5, Category.NETWORK, "Conduit", "inner"),
            ],
            10,
        )
        assert [s.stack[-1] for s in segments] == ["outer", "inner", "outer"]

    def test_neighbours_with_one_stack_merge(self):
        segments = build_segments(
            [
                interval(0, 5, Category.NETWORK, "Conduit", "m"),
                interval(5, 10, Category.NETWORK, "Conduit", "m"),
            ],
            10,
        )
        assert [(s.start_time, s.end_time) for s in segments] == [(0, 10)]

    def test_a_prompt_outranks_everything(self):
        segments = build_segments(
            [
                interval(0, 10, Category.SLEEP, "Sleep", "x"),
                interval(2, 8, Category.IDLE, "Waiting for input"),
            ],
            10,
        )
        assert [s.category for s in segments] == [
            Category.SLEEP,
            Category.IDLE,
            Category.SLEEP,
        ]


def _recorded_run(timed, clock):
    """A small run: start-up, a configuration phase, two calls, a backoff, a prompt."""
    timed.interval(STARTUP_PHASE, Category.PHASE, "Phase", 0.0, 50.0)
    clock.advance(50)
    with timed.phase("Configuring and connecting"):
        clock.advance(5)
    with timed.network(
        "maniphest.search", "https://x/api/maniphest.search", {"limit": 1}
    ):
        clock.advance(20)
        with timed.sleep("retry 1: maniphest.search"):
            clock.advance(10)
        clock.advance(20)
    timed.instant("INFO", Category.LOGGING, "Log", message="m")
    with timed.user_input("Apply T1?"):
        clock.advance(100)
    with timed.cache("read", "users") as data:
        data["outcome"] = "hit"
        clock.advance(1)
    clock.advance(4)
    return timed.build_profile()


class TestProfile:
    @pytest.fixture
    def profile(self, timed, clock):
        return _recorded_run(timed, clock)

    def test_it_is_a_processed_profile_of_one_thread(self, profile):
        meta = profile["meta"]
        assert meta["preprocessedProfileVersion"] == PROCESSED_PROFILE_VERSION
        assert meta["profilingStartTime"] == 0
        assert meta["profilingEndTime"] == pytest.approx(210)
        assert meta["symbolicationNotSupported"] is True
        assert meta["product"].startswith("phabfive")
        (thread,) = profile["threads"]
        assert thread["name"] == "phabfive test"
        assert thread["isMainThread"] is True
        assert isinstance(thread["pid"], str)

    def test_it_serialises_to_json(self, profile):
        assert json.loads(json.dumps(profile)) == profile

    def test_every_table_column_is_as_long_as_the_table(self, profile):
        tables = dict(profile["shared"])
        tables.pop("stringArray")
        thread = profile["threads"][0]
        tables["samples"] = thread["samples"]
        tables["markers"] = thread["markers"]
        for name, table in tables.items():
            for column, values in table.items():
                if isinstance(values, list):
                    assert len(values) == table["length"], (name, column)

    def test_stacks_point_at_earlier_stacks_and_real_frames(self, profile):
        shared = profile["shared"]
        stacks, frames = shared["stackTable"], shared["frameTable"]
        for index in range(stacks["length"]):
            assert 0 <= stacks["prefixOffset"][index] <= index
            assert 0 <= stacks["frame"][index] < frames["length"]
        for func in frames["func"]:
            assert 0 <= func < shared["funcTable"]["length"]

    def test_names_are_indexes_into_the_string_table(self, profile):
        strings = profile["shared"]["stringArray"]
        names = profile["shared"]["funcTable"]["name"]
        names += profile["threads"][0]["markers"]["name"]
        assert all(0 <= name < len(strings) for name in names)
        assert "Conduit" in strings
        assert "maniphest.search" in strings

    def test_categories_are_indexes_into_the_category_list(self, profile):
        count = len(profile["meta"]["categories"])
        assert count == len(Category)
        assert all(0 <= c < count for c in profile["shared"]["frameTable"]["category"])
        assert all(0 <= c < count for c in profile["threads"][0]["markers"]["category"])

    def test_the_sample_weights_add_up_to_the_run(self, profile):
        samples = profile["threads"][0]["samples"]
        assert samples["weightType"] == "tracing-ms"
        assert sum(samples["weight"]) == pytest.approx(210, abs=0.01)
        assert samples["time"] == sorted(samples["time"])

    def test_each_stretch_closes_with_a_weightless_sample(self, profile):
        samples = profile["threads"][0]["samples"]
        assert samples["weight"][1] == 0
        assert samples["stack"][0] == samples["stack"][1]

    def test_markers_are_instants_or_intervals(self, profile):
        markers = profile["threads"][0]["markers"]
        for phase, end in zip(markers["phase"], markers["endTime"]):
            if phase == MarkerPhase.INSTANT:
                assert end is None
            else:
                assert phase == MarkerPhase.INTERVAL
                assert end is not None
        assert markers["startTime"] == sorted(markers["startTime"])

    def test_phabfives_marker_types_have_schemas(self, profile):
        schemas = {schema["name"] for schema in profile["meta"]["markerSchema"]}
        types = {data["type"] for data in profile["threads"][0]["markers"]["data"]}
        # Network is the profiler's own type, with its own schema
        assert types - {"Network"} <= schemas
        for schema in profile["meta"]["markerSchema"]:
            assert all({"key", "format"} <= set(field) for field in schema["fields"])


class TestSaving:
    def test_a_profile_is_written_privately(self, recorder, tmp_path):
        path = recorder.save(tmp_path / "profiles")

        assert path.name.startswith("profile-") and path.suffix == ".json"
        assert json.loads(path.read_text())["meta"]["preprocessedProfileVersion"]
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_the_oldest_beyond_keep_are_pruned(self, recorder, tmp_path):
        for stamp in ["20260101-000000-1", "20260102-000000-1", "20260103-000000-1"]:
            (tmp_path / f"profile-{stamp}.json").write_text("{}")

        path = recorder.save(tmp_path, keep=2)

        assert [p.name for p in list_profiles(tmp_path)] == [
            "profile-20260103-000000-1.json",
            path.name,
        ]

    def test_keep_zero_keeps_everything(self, recorder, tmp_path):
        for day in range(1, 6):
            (tmp_path / f"profile-2026010{day}-000000-1.json").write_text("{}")
        recorder.save(tmp_path, keep=0)
        assert len(list_profiles(tmp_path)) == 6

    def test_a_missing_directory_lists_nothing(self, tmp_path):
        assert list_profiles(tmp_path / "nowhere") == []


class TestConfiguration:
    def test_keep_defaults_to_twenty(self, monkeypatch):
        assert profile_keep({}) == 20

    def test_keep_comes_from_the_configuration(self):
        assert profile_keep({"PHAB_PROFILE_KEEP": "3"}) == 3
        assert profile_keep({"PHAB_PROFILE_KEEP": 0}) == 0

    def test_the_environment_wins(self, monkeypatch):
        monkeypatch.setenv("PHAB_PROFILE_KEEP", "7")
        assert profile_keep({"PHAB_PROFILE_KEEP": 3}) == 7

    @pytest.mark.parametrize("value", ["-1", "many", "1.5"])
    def test_a_bad_keep_is_refused(self, value):
        with pytest.raises(PhabfiveInputException, match="PHAB_PROFILE_KEEP"):
            profile_keep({"PHAB_PROFILE_KEEP": value})

    def test_the_directory_follows_the_configuration(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PHAB_PROFILE_DIR")
        assert profile_dir({"PHAB_PROFILE_DIR": str(tmp_path)}) == tmp_path
        assert profile_dir({}).name == "profiles"

    def test_the_origin_loses_its_trailing_slash(self, monkeypatch):
        assert profiler_origin({}) == "https://profiler.dynamist.cloud"
        assert profiler_origin({"PHAB_PROFILER_ORIGIN": "http://localhost:4242/"}) == (
            "http://localhost:4242"
        )
        monkeypatch.setenv("PHAB_PROFILER_ORIGIN", "https://profiler.firefox.com")
        assert profiler_origin({}) == "https://profiler.firefox.com"


class TestHooks:
    """The calls phabfive makes reach the shared recorder."""

    @pytest.fixture
    def sleeps(self, monkeypatch):
        waits = []
        monkeypatch.setattr("phabfive.retry._sleep", waits.append)
        return waits

    @pytest.fixture
    def wire(self, monkeypatch):
        """test_retry's scripted transport, for the real client to call."""

        def install(*outcomes):
            transport = test_retry.Transport(outcomes)
            monkeypatch.setattr(
                "urllib3.connectionpool.HTTPConnectionPool._make_request",
                lambda pool, conn, method, url, **kw: transport(
                    pool, conn, method, url, **kw
                ),
            )
            return transport

        return install

    @pytest.fixture
    def recording(self):
        profiler.start("phabfive test")
        yield profiler
        profiler.stop()

    def test_a_conduit_call_and_its_retries_are_recorded(self, recording, wire, sleeps):
        wire(status(503), ok({"data": []}))

        conduit().maniphest.search(constraints={"query": "x"}, **{"api.token": TOKEN})

        kinds = [(m.data["type"], m.data.get("status")) for m in recording.markers]
        assert kinds == [
            ("Network", "STATUS_START"),
            ("Sleep", None),
            ("Network", "STATUS_STOP"),
        ]
        uri = recording.markers[0].data["URI"]
        assert uri.endswith(
            "/api/maniphest.search?constraints[query]=x&api.token=%E2%80%A6"
        )
        assert recording.markers[1].data["reason"] == "retry 1: maniphest.search"

    def test_a_failed_conduit_call_is_cancelled(self, recording, wire, sleeps):
        from phabfive.exceptions import PhabfiveConnectionException
        from phabfive.retry import RetryPolicy

        wire(status(503))

        with pytest.raises(PhabfiveConnectionException):
            conduit(RetryPolicy(retries=0)).maniphest.search()

        assert recording.markers[-1].data["status"] == "STATUS_CANCEL"
        assert recording.markers[-1].data["responseStatus"] == 503

    def test_a_cache_lookup_is_recorded(self, recording, enabled_cache):
        from phabfive import cache

        cache.set("users", "k", ["alice"])
        assert cache.get("users", "k") == ["alice"]
        assert cache.get("users", "missing") is cache.MISS

        assert [
            (m.data["operation"], m.data.get("outcome")) for m in recording.markers
        ] == [
            ("write", None),
            ("read", "hit"),
            ("read", "miss"),
        ]

    def test_the_pace_between_writes_is_recorded(self, recording, sleeps, monkeypatch):
        from phabfive.retry import Pacer

        pacer = Pacer(5)
        pacer.wait()
        pacer.wait()

        assert len(sleeps) == 1
        assert [m.data["reason"] for m in recording.markers] == ["PHAB_PACE"]

    def test_a_saved_profile_holds_no_token_or_written_text(
        self, recording, wire, sleeps
    ):
        wire(ok({"object": {"id": 1}}), ok({"data": []}))
        client = conduit()

        client.maniphest.edit(
            objectIdentifier="T1",
            transactions=[
                {"type": "description", "value": "the private body"},
                {"type": "comment", "value": "a private remark"},
            ],
        )
        client.passphrase.query(ids=[1], needSecrets=True, **{"api.token": TOKEN})
        logging.getLogger("phabfive.test").warning("with %s", TOKEN)

        text = json.dumps(recording.build_profile())
        for private in ["the private body", "a private remark", TOKEN]:
            assert private not in text
        assert "maniphest.edit" in text and "passphrase.query" in text


class TestSummary:
    @pytest.fixture
    def summary(self, timed, clock):
        from phabfive.profiler import summarize_profile

        return summarize_profile(_recorded_run(timed, clock))

    def test_the_run_as_a_whole(self, summary):
        assert summary["Command"] == "phabfive test"
        assert summary["Duration"] == pytest.approx(0.21)
        assert summary["ExitStatus"] is None

    def test_time_is_grouped_by_what_it_went_on(self, summary):
        time = {entry["Activity"]: entry["Seconds"] for entry in summary["Time"]}
        assert time == pytest.approx(
            {
                "Waiting for input": 0.1,
                STARTUP_PHASE: 0.05,
                "Conduit": 0.04,
                "Sleep": 0.01,
                "Cache": 0.001,
                "phabfive's own work": 0.009,
            }
        )
        assert [entry["Seconds"] for entry in summary["Time"]] == sorted(
            (entry["Seconds"] for entry in summary["Time"]), reverse=True
        )
        assert sum(entry["Share"] for entry in summary["Time"]) == pytest.approx(
            1, abs=0.01
        )

    def test_conduit_calls_are_counted_per_method(self, summary):
        assert summary["Conduit"] == [
            {
                "Method": "maniphest.search",
                "Calls": 1,
                "Failed": 0,
                "Total": pytest.approx(0.05),
                "Slowest": pytest.approx(0.05),
            }
        ]

    def test_sleeps_are_counted_per_reason(self, summary):
        assert summary["Sleeps"] == [
            {
                "Reason": "retry 1: maniphest.search",
                "Count": 1,
                "Total": pytest.approx(0.01),
            }
        ]

    def test_failures_warnings_and_the_exit_status(self, timed, clock):
        from phabfive.profiler import summarize_profile

        for _ in range(2):
            with pytest.raises(RuntimeError):
                with timed.network("user.whoami", "https://x/api/user.whoami"):
                    clock.advance(3)
                    raise RuntimeError
        timed.log(logging.makeLogRecord({"levelname": "WARNING", "msg": "slow down"}))
        timed.log(logging.makeLogRecord({"levelname": "DEBUG", "msg": "chatter"}))
        timed.interval("user whoami", Category.PHASE, "Phase", 0.0, 6.0, exitCode=1)

        summary = summarize_profile(timed.build_profile())

        assert summary["Conduit"][0]["Calls"] == 2
        assert summary["Conduit"][0]["Failed"] == 2
        assert summary["Problems"] == [{"Level": "WARNING", "Message": "slow down"}]
        assert summary["ExitStatus"] == 1

    def test_something_else_is_refused(self):
        from phabfive.profiler import summarize_profile

        with pytest.raises(
            PhabfiveInputException, match="not a profile phabfive wrote"
        ):
            summarize_profile({"meta": {}})
