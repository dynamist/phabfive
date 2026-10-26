# -*- coding: utf-8 -*-
"""Recording a command's profile, and `phabfive profile` to open one.

A run is recorded only when asked for: `--profile` on the command line, or
PHAB_PROFILE in the environment or configuration. `ProfileSession` wraps the
whole command, and saves the profile when it ends however it ends.

`phabfive profile` hands a saved profile to the Firefox Profiler through its
`/from-url/` route: the file is served once from 127.0.0.1 and fetched by
the browser, so it never goes anywhere else unless somebody uploads it from
the profiler.

Like phabfive.profiler, this is phabfive's own Apache-2.0 code, written from
the public Firefox Profiler documentation and containing no code from any
other implementation.
"""

import json
import logging
import os
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import typer

from phabfive.cli import clock
from phabfive.cli.output import _get_output_format
from phabfive.exceptions import PhabfiveInputException
from phabfive.profiler import (
    Category,
    MarkerType,
    list_profiles,
    profile_dir,
    profile_keep,
    profiler,
    profiler_origin,
    summarize_profile,
    truthy,
)

log = logging.getLogger(__name__)

#: Commands that are never recorded: opening a profile, and the REPL, which
#: is a session rather than a command
UNRECORDED_COMMANDS = {"profile", "repl"}

#: Options that answer without running a command
_INFO_OPTIONS = {
    "--help",
    "--version",
    "-V",
    "--skill",
    "--install-completion",
    "--show-completion",
}

#: The profiler's view a profile opens in
DEFAULT_VIEW = "marker-chart"


def _read_config():
    """The configuration, or {} when it cannot be read.

    A broken configuration is the command's to report, not the recorder's.
    """
    try:
        from phabfive.core import Phabfive

        conf, _ = Phabfive.read_config()
        return conf
    except Exception:
        return {}


def wants_profile(args):
    """Whether this run is to be recorded, from its arguments and configuration.

    `--profile` asks for one run. Otherwise PHAB_PROFILE decides, read from
    the environment when it is set there, so that only a run that has to
    pays for reading the configuration files.
    """
    if "--profile" in args:
        return True
    if "PHAB_PROFILE" in os.environ:
        return truthy(os.environ["PHAB_PROFILE"])
    return truthy(_read_config().get("PHAB_PROFILE"))


def begin(argv, command_words, name_command=None):
    """A ProfileSession for this run, or None when it is not to be recorded.

    Parameters
    ----------
    argv : list of str
        The arguments, after monogram and format preprocessing
    command_words : list of str
        The positional words of argv, the command's path first
    name_command : callable, optional
        Takes command_words and returns the command's name, such as
        "maniphest search". Called only when the profile is saved.
    """
    # Shell completion runs phabfive on every TAB
    if os.environ.get("_PHABFIVE_COMPLETE"):
        return None

    args = argv[1:]
    if "--" in args:
        args = args[: args.index("--")]
    if _INFO_OPTIONS.intersection(args):
        return None
    if command_words and command_words[0] in UNRECORDED_COMMANDS:
        return None
    if not wants_profile(args):
        return None

    return ProfileSession(command_words, name_command)


def _exit_status(exc_type, exc):
    """The exit status a command leaving with `exc` ends with."""
    if exc_type is None:
        return 0
    if issubclass(exc_type, SystemExit):
        code = exc.code if exc is not None else None
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    if issubclass(exc_type, KeyboardInterrupt):
        return 130
    return 1


def _console_is_quiet():
    """Whether -q turned the console down past warnings."""
    for handler in logging.getLogger().handlers:
        if handler.get_name() == "console":
            return handler.level > logging.WARNING
    return False


class ProfileSession:
    """Records the command it wraps, and saves the profile as it leaves."""

    def __init__(self, command_words, name_command=None):
        self.command_words = list(command_words)
        self.name_command = name_command
        self.path = None

    def __enter__(self):
        profiler.start(
            "phabfive", start_wall=clock.STARTED_WALL, start_perf=clock.STARTED_PERF
        )
        profiler.add_startup_phase()
        self._started = profiler.now()
        return self

    def __exit__(self, exc_type, exc, tb):
        status = _exit_status(exc_type, exc)
        name = self._command_name()
        profiler.name = f"phabfive {name}".strip()
        profiler.interval(
            name or "phabfive",
            Category.PHASE,
            MarkerType.PHASE,
            self._started,
            profiler.now(),
            exitCode=status,
        )
        profiler.stop()
        self.save()
        return False

    def _command_name(self):
        if self.name_command is not None:
            try:
                return self.name_command(self.command_words)
            except Exception as e:
                log.debug(f"Could not name the command: {e}")
        return " ".join(self.command_words[:2])

    def save(self):
        """Write the profile, saying where on stderr. Never raises."""
        try:
            conf = _read_config()
            self.path = profiler.save(profile_dir(conf), profile_keep(conf))
        except Exception as e:
            typer.echo(f"Warning: the profile was not saved: {e}", err=True)
            return None

        if not _console_is_quiet():
            typer.echo(
                f"Profile saved to {self.path}, open it with: phabfive profile",
                err=True,
            )
        return self.path


# Opening a profile


class ProfileServer(HTTPServer):
    """Serves one file, once, to one origin.

    Bound to 127.0.0.1 on a port the system picks. Any other path is a 404,
    and the CORS header names the profiler's origin rather than `*`, so no
    other page can read the file while it is on offer.
    """

    def __init__(self, path, origin):
        self.profile_path = Path(path)
        self.origin = origin
        self.route = "/" + quote(self.profile_path.name)
        self.served = False
        super().__init__(("127.0.0.1", 0), _ProfileHandler)

    @property
    def profile_url(self):
        return f"http://127.0.0.1:{self.server_address[1]}{self.route}"

    def profiler_url(self, view=DEFAULT_VIEW):
        """The Firefox Profiler address that loads this server's file."""
        return f"{self.origin}/from-url/{quote(self.profile_url, safe='')}/{view}/"

    def serve_once(self, timeout):
        """Answer requests until the file has been fetched or `timeout` passes.

        Returns
        -------
        bool
            Whether the file was fetched
        """
        deadline = time.monotonic() + timeout
        while not self.served:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.timeout = min(remaining, 1.0)
            self.handle_request()
        return True


class _ProfileHandler(BaseHTTPRequestHandler):
    server: ProfileServer

    def do_GET(self):
        server = self.server
        if self.path != server.route:
            self.send_error(404)
            return
        try:
            body = server.profile_path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", server.origin)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        server.served = True

    def log_message(self, format, *args):
        log.debug("profile server: " + format % args)


def describe_profile(path):
    """What `--list` shows for one saved profile."""
    path = Path(path)
    record = {
        "Path": str(path),
        "Started": None,
        "Command": None,
        "Duration": None,
        "Size": path.stat().st_size,
    }
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
        meta = profile["meta"]
        record["Started"] = datetime.fromtimestamp(meta["startTime"] / 1000).isoformat(
            timespec="seconds"
        )
        record["Command"] = profile["threads"][0]["name"]
        record["Duration"] = round(meta["profilingEndTime"] / 1000, 3)
    except Exception:
        pass
    return record


def _emit_list(records, output_format):
    from phabfive.cli.output import is_machine_format
    from phabfive.json_output import emit_records

    if output_format == "value":
        for record in records:
            typer.echo(record["Path"])
        return
    if output_format in ("json", "jsonl"):
        emit_records(records, output_format)
        return
    if is_machine_format(output_format):
        from io import StringIO

        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(records, stream)
        print(stream.getvalue(), end="")
        return

    if not records:
        typer.echo(
            "No profiles saved yet. Record one with: phabfive --profile <command>"
        )
        return

    from rich.console import Console
    from rich.table import Table

    from phabfive.cli.cache import _human_size

    table = Table(show_header=True, header_style="bold")
    for column in ["Started", "Command", "Duration", "Size", "File"]:
        table.add_column(column)
    for record in records:
        duration = record["Duration"]
        table.add_row(
            record["Started"] or "-",
            record["Command"] or "-",
            "-" if duration is None else f"{duration:.2f}s",
            _human_size(record["Size"]),
            Path(record["Path"]).name,
        )
    Console().print(table)


def _summarize(path, output_format):
    """Print summarize_profile's answer for the profile at `path`."""
    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
        record = {"Path": str(path), **summarize_profile(profile)}
    except (OSError, ValueError) as e:
        typer.echo(f"Error: {path} could not be read: {e}", err=True)
        raise typer.Exit(1)
    except PhabfiveInputException as e:
        typer.echo(f"Error: {path} is {e}", err=True)
        raise typer.Exit(1)

    if record["Started"] is not None:
        record["Started"] = datetime.fromtimestamp(record["Started"] / 1000).isoformat(
            timespec="seconds"
        )

    if output_format in ("json", "jsonl"):
        from phabfive.json_output import emit_record

        emit_record(record, output_format)
        return
    if output_format in ("yaml", "value"):
        from io import StringIO

        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(record, stream)
        print(stream.getvalue(), end="")
        return

    _display_summary(record)


def _display_summary(record):
    """The summary as a few tables, for a person at a terminal."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    status = record["ExitStatus"]
    console.print(f"[bold]{record['Command'] or 'phabfive'}[/bold]")
    console.print(
        f"Started {record['Started'] or '-'}, took {record['Duration']:.2f}s"
        + ("" if status is None else f", exit status {status}")
    )
    console.print(f"[dim]{record['Path']}[/dim]")

    def table(title, columns, rows):
        if not rows:
            return
        grid = Table(
            title=title, title_justify="left", show_header=True, header_style="bold"
        )
        for column in columns:
            grid.add_column(column, justify="left" if column == columns[0] else "right")
        for row in rows:
            grid.add_row(*row)
        console.print()
        console.print(grid)

    table(
        "Where the time went",
        ["Activity", "Time", "Share"],
        [
            (entry["Activity"], f"{entry['Seconds']:.3f}s", f"{entry['Share']:.0%}")
            for entry in record["Time"]
        ],
    )
    table(
        "Conduit calls",
        ["Method", "Calls", "Failed", "Total", "Slowest"],
        [
            (
                entry["Method"],
                str(entry["Calls"]),
                str(entry["Failed"]) if entry["Failed"] else "-",
                f"{entry['Total']:.3f}s",
                f"{entry['Slowest']:.3f}s",
            )
            for entry in record["Conduit"]
        ],
    )
    table(
        "Sleeps",
        ["Reason", "Count", "Total"],
        [
            (entry["Reason"], str(entry["Count"]), f"{entry['Total']:.3f}s")
            for entry in record["Sleeps"]
        ],
    )
    if record["Problems"]:
        console.print()
        console.print("[bold]Warnings and errors[/bold]")
        for problem in record["Problems"]:
            console.print(f"  {problem['Level']}: {problem['Message']}", markup=False)


def profile_command(
    ctx: typer.Context,
    path: Optional[Path] = typer.Argument(
        None,
        help="The profile to open. Default: the newest one saved.",
        dir_okay=False,
    ),
    list_profiles_: bool = typer.Option(
        False,
        "--list",
        help="List the saved profiles instead of opening one.",
    ),
    summary: bool = typer.Option(
        False,
        "--summary",
        help="Summarise where the time went in the terminal instead of opening a browser.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Print the address to open rather than opening a browser.",
    ),
    timeout: float = typer.Option(
        300,
        "--timeout",
        min=1,
        help="Seconds to wait for the profiler to fetch the profile.",
    ),
) -> None:
    """Open a recorded profile in the Firefox Profiler.

    Record a command with `phabfive --profile <command>`, or set
    PHAB_PROFILE to record every one. The profile is served once from
    127.0.0.1 and opened in the profiler at PHAB_PROFILER_ORIGIN, so it
    stays on this machine unless you upload it from there.

    \b
    Examples:
        phabfive --profile maniphest search --assigned=@me
        phabfive profile
        phabfive profile --list
        phabfive profile --summary
        phabfive --format=json profile --summary
        phabfive profile ~/.local/share/phabfive/profiles/profile-20261026-101500-4242.json
        PHAB_PROFILER_ORIGIN=https://profiler.firefox.com phabfive profile
    """
    if list_profiles_ and summary:
        typer.echo("Error: --list and --summary cannot be combined", err=True)
        raise typer.Exit(1)

    conf = _read_config()
    directory = profile_dir(conf)

    if list_profiles_:
        records = [describe_profile(found) for found in list_profiles(directory)]
        _emit_list(records, _get_output_format(ctx))
        return

    if path is None:
        saved = list_profiles(directory)
        if not saved:
            typer.echo(
                f"Error: no profiles in {directory}. "
                "Record one with: phabfive --profile <command>",
                err=True,
            )
            raise typer.Exit(1)
        path = saved[-1]
    elif not path.is_file():
        typer.echo(f"Error: {path} is not a file", err=True)
        raise typer.Exit(1)

    if summary:
        _summarize(path, _get_output_format(ctx))
        return

    server = ProfileServer(path, profiler_origin(conf))
    try:
        url = server.profiler_url()
        typer.echo(f"Serving {path} once at {server.profile_url}", err=True)
        typer.echo(f"Open: {url}", err=True)
        if not no_browser:
            import webbrowser

            webbrowser.open_new_tab(url)
        if not server.serve_once(timeout):
            typer.echo(
                f"Error: the profiler did not fetch the profile within {timeout:g}s",
                err=True,
            )
            raise typer.Exit(1)
    except KeyboardInterrupt:
        raise typer.Exit(130)
    finally:
        server.server_close()


__all__ = [
    "ProfileServer",
    "ProfileSession",
    "begin",
    "describe_profile",
    "profile_command",
    "wants_profile",
]
