# -*- coding: utf-8 -*-
"""Record a phabfive run as a Firefox Profiler profile.

A run is mostly waiting: on Conduit, on a retry's backoff, on PHAB_PACE, on
the person at the terminal. The recorder keeps a marker for each of those, and
for log records and cache lookups, and turns them into a processed profile the
Firefox Profiler opens as it is. Nothing is sampled. The samples in the profile
are generated from the markers instead, one per stretch of time, so the
timeline is coloured by what the run was waiting on and the call tree adds up
where the time went.

Recording is off until the command calls `profiler.start()`. Every hook in
phabfive asks the module-level `profiler` and does nothing while it is off,
so a program that imports phabfive never records. Writing the file, and
choosing whether to, is the command's decision too.

What a profile holds is chosen so that it can be shared: a Conduit call is
recorded with its parameters, but tokens, secrets and the text of
descriptions, comments and other transaction values are left out, and a
prompt is recorded by its question, never its answer.

Licensing: this module is part of phabfive and, like the rest of it, is
licensed under Apache-2.0. It was written from the public Firefox Profiler
profile format - the documentation and format definitions published with
https://github.com/firefox-devtools/profiler - and contains no code taken
from that project (MPL-2.0) or from any other implementation.
"""

import contextlib
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any
from urllib.parse import quote

from phabfive.exceptions import PhabfiveInputException

#: The processed profile format version written. The Firefox Profiler
#: upgrades older versions as it loads them, so a front-end newer than this
#: opens the file; one older refuses it.
PROCESSED_PROFILE_VERSION = 76

#: The Gecko profile format version the processed one corresponds to. Nothing
#: here comes from Gecko, but the field is required.
GECKO_PROFILE_VERSION = 37

#: Where `phabfive profile` opens a profile when PHAB_PROFILER_ORIGIN is unset
DEFAULT_ORIGIN = "https://profiler.dynamist.cloud"

#: How many profiles are kept when PHAB_PROFILE_KEEP is unset
DEFAULT_KEEP = 20

#: A profile's file name; sorting the names sorts them oldest first
PROFILE_GLOB = "profile-*.json"

#: The longest parameter string kept for one Conduit call
MAX_PARAMS_LENGTH = 1000

#: The longest single string value kept among those parameters
MAX_VALUE_LENGTH = 100

#: How long before a stretch ends the closing sample is placed, in ms. It
#: carries no weight, but stops the timeline painting the stretch's colour
#: into the next one.
CLOSING_SAMPLE_OFFSET = 0.1

# Parameter keys whose values are never recorded: credentials, and the free
# text a person wrote. Matched anywhere in the key, case-insensitively.
_PRIVATE_KEYS = re.compile(
    r"token|secret|password|passphrase|private|content|text|description|comment|body|message",
    re.IGNORECASE,
)

# Conduit and Arcanist tokens, wherever they turn up in a log message
_TOKEN = re.compile(r"\b(api|cli)-[a-z0-9]{20,}\b", re.IGNORECASE)

# Values that mean "off" for PHAB_PROFILE, from the environment or a yaml file
_FALSEY = {"0", "false", "no", "off", "", "none"}


class Category(IntEnum):
    """The profile's categories; each value is the category's index."""

    OTHER = 0
    IDLE = 1
    NETWORK = 2
    SLEEP = 3
    PHASE = 4
    LOGGING = 5
    CACHE = 6
    PHABFIVE = 7


#: Name and colour of each category, in index order. The Firefox Profiler
#: treats grey as the default category and transparent as idle.
CATEGORIES = {
    Category.OTHER: ("Other", "grey"),
    Category.IDLE: ("Waiting for input", "transparent"),
    Category.NETWORK: ("Network", "lightblue"),
    Category.SLEEP: ("Sleep", "orange"),
    Category.PHASE: ("Phase", "purple"),
    Category.LOGGING: ("Logging", "darkgrey"),
    Category.CACHE: ("Cache", "green"),
    Category.PHABFIVE: ("phabfive", "yellow"),
}


class MarkerPhase(IntEnum):
    """How a marker's times are read."""

    INSTANT = 0
    INTERVAL = 1


class MarkerType(str, Enum):
    """A marker's `data.type`, which picks the schema it is shown with."""

    PHASE = "Phase"
    NETWORK = "Network"
    SLEEP = "Sleep"
    USER_INPUT = "UserInput"
    COMMAND = "Command"
    CACHE = "Cache"
    LOG = "Log"


@dataclass(slots=True)
class Marker:
    """Something that happened, at a time or for a while.

    Times are milliseconds since the recording started. An instant marker
    has no end time.
    """

    name: str
    category: Category
    start_time: float
    end_time: float | None
    data: dict


@dataclass(slots=True)
class Interval:
    """A stretch of time a run spent waiting on one thing.

    `stack` is the frames below the root that the stretch's samples carry,
    `priority` decides which of two overlapping intervals the time belongs
    to.
    """

    start_time: float
    end_time: float
    stack: tuple[str, ...]
    category: Category
    priority: int


@dataclass(slots=True)
class Segment:
    """A stretch of the timeline and the stack its time is charged to."""

    start_time: float
    end_time: float
    stack: tuple[str, ...]
    category: Category


@dataclass(slots=True)
class Frame:
    """One entry of the frame table: a function name and its category."""

    name: str
    category: Category


#: Which waits take the time when several overlap, the highest first. A
#: retry's backoff happens inside the Conduit call it repeats, and a prompt
#: can be shown while a request is out, so the outer wait never hides the
#: inner one.
WAITING_PRIORITY = {
    Category.IDLE: 4,
    Category.SLEEP: 3,
    Category.NETWORK: 2,
    Category.CACHE: 1,
    Category.PHASE: 0,
}


def truthy(value, default=False):
    """Interpret a configuration value that may be a bool, an int or a string."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in _FALSEY


def redact_text(text):
    """`text` with any Conduit or Arcanist token in it shortened to its prefix."""
    return _TOKEN.sub(lambda match: f"{match.group(1)}-…", text)


def _clip(text, length):
    return text if len(text) <= length else text[: length - 1] + "…"


def _flatten(value, prefix, pairs):
    """Append `value` to `pairs` the way Conduit puts it on the wire.

    Nested mappings and lists become `constraints[projects][0]=...` keys.
    Values under a private key are replaced, and transactions keep only
    their type: the value of a description or comment transaction is text
    somebody wrote.
    """
    key = prefix.rsplit("[", 1)[-1].rstrip("]")
    if _PRIVATE_KEYS.search(key):
        pairs.append((prefix, "…"))
    elif isinstance(value, dict):
        if prefix.startswith("transactions[") and "type" in value:
            pairs.append((f"{prefix}[type]", str(value["type"])))
            return
        for name, item in value.items():
            _flatten(item, f"{prefix}[{name}]" if prefix else str(name), pairs)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _flatten(item, f"{prefix}[{index}]", pairs)
    else:
        pairs.append((prefix, _clip(str(value), MAX_VALUE_LENGTH)))


def describe_params(params):
    """A Conduit call's parameters as a query string, with private values left out.

    Parameters
    ----------
    params : dict
        The keyword arguments the call was made with

    Returns
    -------
    str
        `key=value` pairs joined by `&`, at most MAX_PARAMS_LENGTH long, or ""
    """
    pairs = []
    for name, value in (params or {}).items():
        _flatten(value, str(name), pairs)
    query = "&".join(
        f"{quote(key, safe='[]')}={quote(value, safe='')}" for key, value in pairs
    )
    return _clip(query, MAX_PARAMS_LENGTH)


def _field(key, label, fmt="string", hidden=False):
    entry = {"key": key, "label": label, "format": fmt}
    if hidden:
        entry["hidden"] = True
    return entry


def _marker_schema():
    """The schemas of phabfive's marker types. Network is the profiler's own."""
    every_view = ["marker-chart", "marker-table"]
    return [
        {
            "name": MarkerType.PHASE.value,
            "display": every_view + ["timeline-overview"],
            "chartLabel": "{marker.name}",
            "tableLabel": "{marker.name}",
            "description": "A stage of the command",
            "fields": [_field("exitCode", "Exit status", "integer")],
        },
        {
            "name": MarkerType.SLEEP.value,
            "display": every_view + ["timeline-overview"],
            "chartLabel": "{marker.data.reason}",
            "tableLabel": "Sleep: {marker.data.reason}",
            "description": "phabfive waiting on purpose",
            "fields": [_field("reason", "Reason")],
        },
        {
            "name": MarkerType.USER_INPUT.value,
            "display": every_view + ["timeline-overview"],
            "chartLabel": "{marker.data.question}",
            "tableLabel": "Waiting for input: {marker.data.question}",
            "description": "Waiting for an answer at the terminal; the answer is not recorded",
            "fields": [_field("question", "Question")],
        },
        {
            "name": MarkerType.COMMAND.value,
            "display": every_view + ["timeline-overview"],
            "chartLabel": "{marker.data.command}",
            "tableLabel": "{marker.data.command}",
            "description": "A program phabfive ran and waited for",
            "fields": [
                _field("command", "Command"),
                _field("exitCode", "Exit status", "integer"),
            ],
        },
        {
            "name": MarkerType.CACHE.value,
            "display": every_view,
            "chartLabel": "{marker.data.operation} {marker.data.namespace}",
            "tableLabel": "Cache {marker.data.operation} {marker.data.namespace}",
            "description": "A lookup in phabfive's on-disk cache",
            "fields": [
                _field("operation", "Operation"),
                _field("namespace", "Namespace"),
                _field("outcome", "Outcome"),
            ],
        },
        {
            "name": MarkerType.LOG.value,
            "display": every_view,
            "chartLabel": "{marker.data.message}",
            "tableLabel": "{marker.data.level} {marker.data.logger}: {marker.data.message}",
            "description": "A log record",
            "fields": [
                _field("level", "Level"),
                _field("logger", "Logger"),
                _field("message", "Message"),
            ],
        },
    ]


class Profiler:
    """Collects markers while recording, and builds a profile from them.

    Parameters
    ----------
    enabled : bool
        Whether to record from the start. phabfive's own instance is built
        with False, and only the command starts it.
    """

    def __init__(self, enabled=False):
        self.enabled = enabled
        self.name = "phabfive"
        self.start_wall = time.time()
        self.start_perf = time.perf_counter()
        self.markers: list[Marker] = []
        self._next_load_id = 0
        self._main_thread = threading.main_thread().ident

    def start(self, name="phabfive", start_wall=None, start_perf=None):
        """Begin recording, dropping anything recorded before.

        `start_wall` and `start_perf` move the start back to an earlier
        `time.time()` and `time.perf_counter()` pair, so time spent before
        the recorder existed - importing phabfive - is part of the profile.
        """
        self.name = name
        self.start_wall = time.time() if start_wall is None else start_wall
        self.start_perf = time.perf_counter() if start_perf is None else start_perf
        self.markers = []
        self._next_load_id = 0
        self.enabled = True

    def stop(self):
        """Stop recording. What was recorded is kept, to build or save."""
        self.enabled = False

    def now(self):
        """Milliseconds since the recording started."""
        return (time.perf_counter() - self.start_perf) * 1000

    def _append(self, marker):
        # Markers from other threads land on the one track, saying where they
        # came from. list.append is atomic, so this needs no lock.
        thread = threading.current_thread()
        if thread.ident != self._main_thread:
            marker.data.setdefault("thread", thread.name)
        self.markers.append(marker)

    def interval(self, name, category, marker_type, start_time, end_time, **data):
        """Record an interval marker whose times are already known."""
        if not self.enabled:
            return
        data["type"] = MarkerType(marker_type).value
        self._append(Marker(name, Category(category), start_time, end_time, data))

    def add_startup_phase(self):
        """Record the time from the start of the recording to now as start-up.

        Meant for a recording started with an earlier `start_perf`, where
        that time went on starting Python and importing phabfive.
        """
        self.interval(STARTUP_PHASE, Category.PHASE, MarkerType.PHASE, 0.0, self.now())

    def instant(self, name, category, marker_type, **data):
        """Record a marker with no duration."""
        if not self.enabled:
            return
        data["type"] = MarkerType(marker_type).value
        self._append(Marker(name, Category(category), self.now(), None, data))

    @contextlib.contextmanager
    def marker(self, name, category, marker_type, **data):
        """Record the block as an interval marker.

        Yields the marker's data, so the block can add to it - an exit
        status, an outcome. The marker is recorded even when the block
        raises.
        """
        if not self.enabled:
            yield data
            return
        data["type"] = MarkerType(marker_type).value
        start = self.now()
        try:
            yield data
        finally:
            self._append(Marker(name, Category(category), start, self.now(), data))

    def phase(self, name):
        """A stage of the command, such as loading its configuration."""
        return self.marker(name, Category.PHASE, MarkerType.PHASE)

    def sleep(self, reason):
        """phabfive waiting on purpose: a retry's backoff, PHAB_PACE."""
        return self.marker("Sleep", Category.SLEEP, MarkerType.SLEEP, reason=reason)

    def user_input(self, question):
        """Waiting at the terminal. Only the question is recorded."""
        return self.marker(
            "Waiting for input",
            Category.IDLE,
            MarkerType.USER_INPUT,
            question=redact_text(str(question)),
        )

    def command(self, argv):
        """Running another program and waiting for it, such as $EDITOR."""
        return self.marker(
            os.path.basename(str(argv[0])),
            Category.IDLE,
            MarkerType.COMMAND,
            command=os.path.basename(str(argv[0])),
        )

    def cache(self, operation, namespace):
        """A read or write of the on-disk cache."""
        return self.marker(
            f"Cache {operation}",
            Category.CACHE,
            MarkerType.CACHE,
            operation=operation,
            namespace=namespace,
        )

    def log(self, record):
        """Record a `logging.LogRecord` as an instant marker."""
        if not self.enabled:
            return
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        self.instant(
            record.levelname,
            Category.LOGGING,
            MarkerType.LOG,
            level=record.levelname,
            logger=record.name,
            message=redact_text(message),
        )

    @contextlib.contextmanager
    def network(self, method, url, params=None):
        """Record a Conduit call as the pair of markers a network load is.

        `url` is the endpoint's address, and the parameters are appended to
        it as a query string with private values left out. Yields a dict the
        block can set `responseStatus` in. An exception marks the load
        cancelled, with the exception's class as its status, and is raised
        again.
        """
        if not self.enabled:
            yield {}
            return

        self._next_load_id += 1
        load_id = self._next_load_id
        query = describe_params(params)
        uri = f"{url}?{query}" if query else url
        name = f"Load {load_id}: {uri}"
        start = self.now()
        self._append(
            Marker(
                name,
                Category.NETWORK,
                start,
                start,
                {
                    "type": MarkerType.NETWORK.value,
                    "id": load_id,
                    "URI": uri,
                    "pri": 0,
                    "status": "STATUS_START",
                    "startTime": start,
                    "endTime": start,
                },
            )
        )

        result: dict = {}
        status = "STATUS_STOP"
        try:
            yield result
        except BaseException as e:
            status = "STATUS_CANCEL"
            result["requestStatus"] = type(e).__name__
            # PhabfiveConnectionException carries the HTTP status, if any
            http_status = getattr(e, "status", None)
            if http_status:
                result["responseStatus"] = http_status
            raise
        finally:
            if status == "STATUS_STOP":
                result.setdefault("responseStatus", 200)
                result.setdefault("contentType", "application/json")
            end = self.now()
            data = {
                "type": MarkerType.NETWORK.value,
                "id": load_id,
                "URI": uri,
                "pri": 0,
                "status": status,
                "startTime": start,
                "endTime": end,
                "requestStart": start,
                "responseStart": end,
                "responseEnd": end,
                **result,
            }
            self._append(Marker(name, Category.NETWORK, start, end, data))

    # Building the profile

    def _waiting_interval(self, marker):
        """The Interval a marker spends waiting on, or None if it is not a wait."""
        if marker.end_time is None:
            return None
        data = marker.data
        kind = data.get("type")
        if kind == MarkerType.NETWORK.value:
            if data.get("status") == "STATUS_START":
                return None
            method = data["URI"].split("?", 1)[0].rsplit("/", 1)[-1]
            stack = ("Conduit", method)
        elif kind == MarkerType.SLEEP.value:
            stack = ("Sleep", data.get("reason", ""))
        elif kind == MarkerType.USER_INPUT.value:
            stack = ("Waiting for input",)
        elif kind == MarkerType.COMMAND.value:
            stack = ("Waiting for input", data.get("command", marker.name))
        elif kind == MarkerType.CACHE.value:
            stack = ("Cache", f"{data.get('operation')} {data.get('namespace')}")
        elif kind == MarkerType.PHASE.value and marker.name == STARTUP_PHASE:
            stack = (STARTUP_PHASE,)
        else:
            return None
        return Interval(
            marker.start_time,
            marker.end_time,
            stack,
            marker.category,
            WAITING_PRIORITY.get(marker.category, 0),
        )

    def build_profile(self, end_time=None):
        """The processed profile of what was recorded, as a JSON-ready dict.

        `end_time` is where the profile stops, in ms; now when omitted.
        """
        duration = self.now() if end_time is None else end_time
        duration = max(
            [duration]
            + [marker.end_time or marker.start_time for marker in self.markers]
        )
        intervals = [
            interval
            for interval in map(self._waiting_interval, self.markers)
            if interval is not None
        ]
        segments = build_segments(intervals, duration)
        return _ProfileBuilder(self, duration).build(segments)

    def save(self, directory, keep=DEFAULT_KEEP):
        """Write the profile to `directory`, then prune the oldest beyond `keep`.

        The file and its directory are made private: a profile names the
        tasks and projects a command asked about. `keep` of 0 keeps every
        profile.

        Returns
        -------
        pathlib.Path
            The file written
        """
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.start_wall))
        path = directory / f"profile-{stamp}-{os.getpid()}.json"

        payload = json.dumps(self.build_profile(), separators=(",", ":"))
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temporary, path)

        prune_profiles(directory, keep)
        return path


#: The phase from the process start to the command taking over
STARTUP_PHASE = "Loading Python modules"


def build_segments(intervals, duration):
    """Split `[0, duration]` into stretches, each charged to one wait.

    Where intervals overlap, the time goes to the one with the highest
    priority and, between equals, to the one that started last - the
    innermost. Time no interval covers is phabfive's own work, with an empty
    stack. Neighbouring stretches with the same stack are merged.

    Parameters
    ----------
    intervals : list of Interval
    duration : float
        The end of the profile, in ms

    Returns
    -------
    list of Segment
        In time order, covering the whole duration without gaps
    """
    boundaries = sorted(
        {0.0, float(duration)}
        | {
            point
            for interval in intervals
            for point in (interval.start_time, interval.end_time)
            if 0 <= point <= duration
        }
    )
    by_start = sorted(intervals, key=lambda interval: interval.start_time)
    active: list[Interval] = []
    upcoming = 0
    segments: list[Segment] = []

    for start, end in zip(boundaries, boundaries[1:]):
        while upcoming < len(by_start) and by_start[upcoming].start_time <= start:
            active.append(by_start[upcoming])
            upcoming += 1
        active = [interval for interval in active if interval.end_time > start]

        if active:
            chosen = max(
                active, key=lambda interval: (interval.priority, interval.start_time)
            )
            stack, category = chosen.stack, chosen.category
        else:
            stack, category = (), Category.PHABFIVE

        previous = segments[-1] if segments else None
        if previous and previous.stack == stack and previous.category == category:
            previous.end_time = end
        else:
            segments.append(Segment(start, end, stack, category))

    return segments


class _ProfileBuilder:
    """Lays out the tables of one processed profile."""

    def __init__(self, profiler, duration):
        self.profiler = profiler
        self.duration = duration
        self.strings: list[str] = []
        self._string_index: dict[str, int] = {}
        self.frames: list[Frame] = []
        self._frame_index: dict[tuple[str, Category], int] = {}
        # (frame, prefix stack or None) for each stack, in creation order
        self.stacks: list[tuple[int, int | None]] = []
        self._stack_index: dict[tuple[int, int | None], int] = {}

    def string(self, text):
        index = self._string_index.get(text)
        if index is None:
            index = self._string_index[text] = len(self.strings)
            self.strings.append(text)
        return index

    def frame(self, name, category):
        key = (name, category)
        index = self._frame_index.get(key)
        if index is None:
            index = self._frame_index[key] = len(self.frames)
            self.frames.append(Frame(name, category))
        return index

    def stack(self, frames):
        """The stack index for a path of (name, category) frames, root first."""
        prefix = None
        for name, category in frames:
            key = (self.frame(name, category), prefix)
            index = self._stack_index.get(key)
            if index is None:
                index = self._stack_index[key] = len(self.stacks)
                self.stacks.append(key)
            prefix = index
        return prefix

    def build(self, segments):
        root = (self.profiler.name, Category.PHABFIVE)
        samples: dict[str, list] = {"stack": [], "time": [], "weight": []}

        def sample(stack, at, weight):
            samples["stack"].append(stack)
            samples["time"].append(round(at, 3))
            samples["weight"].append(round(weight, 3))

        for segment in segments:
            frames = [root] + [(name, segment.category) for name in segment.stack]
            stack = self.stack(frames)
            length = segment.end_time - segment.start_time
            sample(stack, segment.start_time, length)
            if length > 2 * CLOSING_SAMPLE_OFFSET:
                sample(stack, segment.end_time - CLOSING_SAMPLE_OFFSET, 0)

        markers: dict[str, Any] = {
            "data": [],
            "name": [],
            "startTime": [],
            "endTime": [],
            "phase": [],
            "category": [],
        }
        ordered = sorted(self.profiler.markers, key=lambda marker: marker.start_time)
        for marker in ordered:
            markers["data"].append(marker.data)
            markers["name"].append(self.string(marker.name))
            markers["startTime"].append(round(marker.start_time, 3))
            if marker.end_time is None:
                markers["endTime"].append(None)
                markers["phase"].append(MarkerPhase.INSTANT.value)
            else:
                markers["endTime"].append(round(marker.end_time, 3))
                markers["phase"].append(MarkerPhase.INTERVAL.value)
            markers["category"].append(marker.category.value)
        markers["length"] = len(ordered)

        samples_table = {
            **samples,
            "weightType": "tracing-ms",
            "length": len(samples["stack"]),
        }

        thread = {
            "processType": "default",
            "processStartupTime": 0,
            "processShutdownTime": round(self.duration, 3),
            "registerTime": 0,
            "unregisterTime": round(self.duration, 3),
            "pausedRanges": [],
            "showMarkersInTimeline": True,
            "name": self.profiler.name,
            "processName": "phabfive",
            "isMainThread": True,
            "pid": str(os.getpid()),
            "tid": threading.main_thread().native_id or 0,
            "samples": samples_table,
            "markers": markers,
        }

        # Function names go into the string table after the markers', which
        # is fine: the table is shared and order does not matter.
        func_names = [self.string(frame.name) for frame in self.frames]
        frame_count = len(self.frames)
        has_category = 1 << 2

        shared = {
            "stackTable": {
                "frame": [frame for frame, _ in self.stacks],
                "prefixOffset": [
                    0 if prefix is None else index - prefix
                    for index, (_, prefix) in enumerate(self.stacks)
                ],
                "length": len(self.stacks),
            },
            "frameTable": {
                "flags": [has_category] * frame_count,
                "address": [0] * frame_count,
                "lib": [-1] * frame_count,
                "category": [frame.category.value for frame in self.frames],
                "subcategory": [0] * frame_count,
                "func": list(range(frame_count)),
                "nativeSymbol": [0] * frame_count,
                "innerWindowID": [0] * frame_count,
                "line": [0] * frame_count,
                "column": [0] * frame_count,
                "originalLocation": [0] * frame_count,
                "length": frame_count,
            },
            "funcTable": {
                "flags": [0] * frame_count,
                "name": func_names,
                "resource": [0] * frame_count,
                "source": [0] * frame_count,
                "lineNumber": [0] * frame_count,
                "columnNumber": [0] * frame_count,
                "originalLocation": [0] * frame_count,
                "length": frame_count,
            },
            "resourceTable": {"length": 0, "name": [], "host": [], "type": []},
            "nativeSymbols": {
                "libIndex": [],
                "address": [],
                "name": [],
                "functionSize": [],
                "length": 0,
            },
            "stringArray": self.strings,
            "sources": {
                "length": 0,
                "id": [],
                "filename": [],
                "startLine": [],
                "startColumn": [],
                "sourceMapURL": [],
                "content": [],
            },
            "sourceLocationTable": {
                "source": [],
                "line": [],
                "column": [],
                "length": 0,
            },
        }

        return {
            "meta": self._meta(),
            "libs": [],
            "counters": [],
            "shared": shared,
            "threads": [thread],
        }

    def _meta(self):
        from importlib.metadata import PackageNotFoundError, version

        try:
            product = f"phabfive {version('phabfive')}"
        except PackageNotFoundError:
            product = "phabfive"

        return {
            "interval": 1,
            "startTime": round(self.profiler.start_wall * 1000, 3),
            "profilingStartTime": 0,
            "profilingEndTime": round(self.duration, 3),
            "processType": 0,
            "product": product,
            "stackwalk": 0,
            "version": GECKO_PROFILE_VERSION,
            "preprocessedProfileVersion": PROCESSED_PROFILE_VERSION,
            "symbolicated": True,
            "symbolicationNotSupported": True,
            "platform": sys.platform,
            "categories": [
                {"name": name, "color": color, "subcategories": ["Other"]}
                for name, color in (CATEGORIES[category] for category in Category)
            ],
            "markerSchema": _marker_schema(),
            "initialSelectedThreads": [0],
            "keepProfileThreadOrder": True,
        }


#: What summarize_profile calls the time no wait accounts for
OWN_WORK = "phabfive's own work"


def _seconds(ms):
    return round(ms / 1000, 3)


def summarize_profile(profile):
    """Where a recorded run's time went, from a profile phabfive saved.

    Reads the profile's own tables, so it answers for a file from any
    phabfive that wrote this format version, not only the running one.

    Parameters
    ----------
    profile : dict
        A profile as `Profiler.build_profile` returns it, or as loaded from
        a saved file

    Returns
    -------
    dict
        Command, Started (epoch ms), Duration and ExitStatus; Time, the
        seconds under each top-level entry of the call tree - Conduit,
        Sleep, Waiting for input, start-up, or OWN_WORK - most first; Conduit, the calls per method,
        slowest in total first; Sleeps, per reason; and Problems, the log
        records at WARNING and above. Durations are in seconds.

    Raises
    ------
    PhabfiveInputException
        When the profile is not one phabfive wrote
    """
    try:
        meta = profile["meta"]
        shared = profile["shared"]
        (thread,) = profile["threads"]
        strings = shared["stringArray"]
        stack_frames = shared["stackTable"]["frame"]
        prefix_offsets = shared["stackTable"]["prefixOffset"]
        frame_funcs = shared["frameTable"]["func"]
        func_names = shared["funcTable"]["name"]
        samples = thread["samples"]
        markers = thread["markers"]
    except (KeyError, TypeError, ValueError) as e:
        raise PhabfiveInputException(
            f"not a profile phabfive wrote: missing {e}"
        ) from None

    def activity(stack):
        """The call tree's top-level entry a stack is under, below the root."""
        child = None
        while prefix_offsets[stack]:
            child = stack
            stack -= prefix_offsets[stack]
        if child is None:
            return OWN_WORK
        return strings[func_names[frame_funcs[stack_frames[child]]]]

    time_by_activity: dict[str, float] = {}
    for stack, weight in zip(samples["stack"], samples["weight"]):
        if stack is None or not weight:
            continue
        name = activity(stack)
        time_by_activity[name] = time_by_activity.get(name, 0.0) + weight

    duration = meta.get("profilingEndTime") or sum(time_by_activity.values())
    exit_status = None
    conduit: dict[str, dict] = {}
    sleeps: dict[str, dict] = {}
    problems = []

    for index in range(markers["length"]):
        data = markers["data"][index] or {}
        kind = data.get("type")
        start = markers["startTime"][index]
        end = markers["endTime"][index]
        length = (end - start) if end is not None else 0.0

        if kind == MarkerType.NETWORK.value and data.get("status") != "STATUS_START":
            method = data.get("URI", "").split("?", 1)[0].rsplit("/", 1)[-1]
            entry = conduit.setdefault(
                method,
                {
                    "Method": method,
                    "Calls": 0,
                    "Failed": 0,
                    "Total": 0.0,
                    "Slowest": 0.0,
                },
            )
            entry["Calls"] += 1
            entry["Failed"] += data.get("status") != "STATUS_STOP"
            entry["Total"] += length
            entry["Slowest"] = max(entry["Slowest"], length)
        elif kind == MarkerType.SLEEP.value:
            reason = data.get("reason", "")
            entry = sleeps.setdefault(
                reason, {"Reason": reason, "Count": 0, "Total": 0.0}
            )
            entry["Count"] += 1
            entry["Total"] += length
        elif kind == MarkerType.LOG.value:
            level = data.get("level", "")
            if logging.getLevelName(level) in range(
                logging.WARNING, logging.CRITICAL + 1
            ):
                problems.append({"Level": level, "Message": data.get("message", "")})
        elif kind == MarkerType.PHASE.value and "exitCode" in data:
            # Only the phase of the command as a whole carries one
            exit_status = data["exitCode"]

    for entry in conduit.values():
        entry["Total"] = _seconds(entry["Total"])
        entry["Slowest"] = _seconds(entry["Slowest"])
    for entry in sleeps.values():
        entry["Total"] = _seconds(entry["Total"])

    return {
        "Command": thread.get("name") or (strings[0] if strings else None),
        "Started": meta.get("startTime"),
        "Duration": _seconds(duration),
        "ExitStatus": exit_status,
        "Time": [
            {
                "Activity": name,
                "Seconds": _seconds(ms),
                "Share": round(ms / duration, 3) if duration else 0.0,
            }
            for name, ms in sorted(time_by_activity.items(), key=lambda item: -item[1])
        ],
        "Conduit": sorted(conduit.values(), key=lambda entry: -entry["Total"]),
        "Sleeps": sorted(sleeps.values(), key=lambda entry: -entry["Total"]),
        "Problems": problems,
    }


def list_profiles(directory):
    """The profiles in `directory`, oldest first. Empty if there is none."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(directory.glob(PROFILE_GLOB))


def prune_profiles(directory, keep):
    """Delete all but the newest `keep` profiles; `keep` of 0 keeps them all."""
    if keep <= 0:
        return
    profiles = list_profiles(directory)
    for old in profiles[: max(len(profiles) - keep, 0)]:
        with contextlib.suppress(OSError):
            old.unlink()


def profile_dir(conf=None):
    """Where profiles are kept: PHAB_PROFILE_DIR, or phabfive's data directory."""
    configured = os.environ.get("PHAB_PROFILE_DIR") or (conf or {}).get(
        "PHAB_PROFILE_DIR"
    )
    if configured:
        return Path(configured).expanduser()
    import appdirs

    return Path(appdirs.user_data_dir("phabfive")) / "profiles"


def profile_keep(conf=None):
    """How many profiles PHAB_PROFILE_KEEP keeps; 0 keeps every one.

    Raises
    ------
    PhabfiveInputException
        When the value is not a whole number of at least 0
    """
    value = os.environ.get("PHAB_PROFILE_KEEP")
    if value is None:
        value = (conf or {}).get("PHAB_PROFILE_KEEP")
    if value is None or str(value).strip() == "":
        return DEFAULT_KEEP
    try:
        keep = int(str(value).strip())
    except ValueError:
        keep = -1
    if keep < 0:
        raise PhabfiveInputException(
            f"PHAB_PROFILE_KEEP must be a whole number of at least 0, not {value!r}"
        )
    return keep


def profiler_origin(conf=None):
    """The Firefox Profiler a profile is opened in, without a trailing slash."""
    origin = os.environ.get("PHAB_PROFILER_ORIGIN") or (conf or {}).get(
        "PHAB_PROFILER_ORIGIN"
    )
    return (origin or DEFAULT_ORIGIN).rstrip("/")


class ProfilerHandler(logging.Handler):
    """A logging handler that records each record as a marker."""

    def __init__(self, recorder=None):
        super().__init__(logging.DEBUG)
        self.recorder = recorder

    def emit(self, record):
        try:
            (self.recorder or profiler).log(record)
        except Exception:
            self.handleError(record)


#: The recorder phabfive's hooks report to. Off until the command starts it.
profiler = Profiler(enabled=False)


__all__ = [
    "CATEGORIES",
    "DEFAULT_KEEP",
    "DEFAULT_ORIGIN",
    "PROCESSED_PROFILE_VERSION",
    "STARTUP_PHASE",
    "WAITING_PRIORITY",
    "Category",
    "Frame",
    "Interval",
    "Marker",
    "MarkerPhase",
    "MarkerType",
    "Profiler",
    "ProfilerHandler",
    "Segment",
    "build_segments",
    "describe_params",
    "list_profiles",
    "profile_dir",
    "profile_keep",
    "profiler",
    "profiler_origin",
    "prune_profiles",
    "redact_text",
    "summarize_profile",
    "truthy",
]
