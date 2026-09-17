#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Run a built phabfive and prove it works, before it is signed or published.

v0.10.0-rc.1 shipped six standalone executables that could not start at all:
phabfive imported click without declaring it, so a fresh resolve installed no
click and importing phabfive.cli died. Every release job reported success,
because nothing in the pipeline ever ran what it had just built.

This is what runs it. One script for every artifact -- the one-file
executables, the wheel installed into a clean venv, and the tree inside the
container image -- so the checks cannot drift apart between them.

    python scripts/smoke.py --executable dist/phabfive-linux-amd64
    python scripts/smoke.py --venv /tmp/fresh-venv
    python scripts/smoke.py --venv .venv --expect-version 0.10.0.dev0

Deliberately imports nothing outside the standard library: it has to run on a
bare CI runner and inside a distro image that has no pip.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

# A token is format-checked before any request is made, so a smoke token has
# to look real: exactly 32 characters of [a-zA-Z0-9-] (constants.VALIDATORS).
SMOKE_TOKEN = "api-smoketest0000000000000000000"

# The discard port. Refuses instantly rather than hanging, which is what makes
# an offline connection-error check fast and reliable.
DEAD_URL = "http://127.0.0.1:9/api/"

# Every command group phabfive exposes. Reaching each one's help proves its
# module was actually bundled, which is what the PyInstaller --hidden-import
# list is guessing at.
COMMAND_GROUPS = ["maniphest", "diffusion", "passphrase", "paste", "user", "cache"]

# How much of PEP 440 normalization reaches --version depends on how phabfive
# was installed: a pip install of the wheel reports pyproject's "0.10.0-dev.0"
# verbatim, while uv's editable install reports the normalized "0.10.0.dev0".
# So only require that it looks like a version, and compare versions with
# canonical_version() rather than as strings.
VERSION_PATTERN = re.compile(r"^\d+\.\d+[0-9A-Za-z.\-+]*$")

# A frozen build that lost a module reports it as a traceback on stderr and
# still exits non-zero, which a "did it fail?" check alone would accept.
IMPORT_FAILURES = [
    "ModuleNotFoundError",
    "ImportError",
    "Traceback (most recent call last)",
]


class Failure(Exception):
    """A check that did not hold."""


def resolve_executable(args) -> str:
    """The phabfive to run: given directly, or found inside a venv."""
    if args.executable:
        if not os.path.isfile(args.executable):
            sys.exit(f"no such executable: {args.executable}")
        return os.path.abspath(args.executable)

    for relative in ("bin/phabfive", "Scripts/phabfive.exe", "Scripts/phabfive"):
        candidate = os.path.join(args.venv, *relative.split("/"))
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    sys.exit(f"no phabfive console script in venv: {args.venv}")


def smoke_env(home: str, url: str = DEAD_URL) -> dict:
    """An environment no real configuration can leak into.

    phabfive reads /etc/phabfive.yaml, ~/.config/phabfive.yaml, ~/.arcrc and
    a .arcconfig in the git root. A smoke run that picked one up could talk to
    a real instance, or block on the interactive setup wizard. Pointing HOME
    at an empty directory and running from there closes every path but the
    environment variables set here, which take precedence anyway.
    """
    env = dict(os.environ)
    env.update(
        {
            "HOME": home,
            "USERPROFILE": home,
            "PHAB_URL": url,
            "PHAB_TOKEN": SMOKE_TOKEN,
            "PHAB_CACHE": "0",
            "NO_COLOR": "1",
        }
    )
    # Inherited from the caller these would re-enable the completion cache or
    # point it back at a real directory.
    env.pop("PHAB_CACHE_DIR", None)
    env.pop("PHAB_SPACE", None)
    return env


def run(executable: str, arguments: list, home: str, timeout: int, env_extra=None):
    """Run phabfive once and return (returncode, combined output)."""
    env = smoke_env(home)
    if env_extra:
        env.update(env_extra)

    try:
        completed = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            # cwd is the empty HOME, so .arcconfig discovery walks a tree that
            # has no git root and no config to find.
            cwd=home,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise Failure(f"timed out after {timeout}s -- it is probably waiting for input")

    return completed.returncode, completed.stdout + completed.stderr


def expect_success(executable, arguments, home, timeout):
    """Run phabfive and require a clean exit."""
    code, output = run(executable, arguments, home, timeout)
    if code != 0:
        raise Failure(f"exited {code}\n{indent(output)}")
    return output


def indent(text: str) -> str:
    lines = text.strip().splitlines() or ["(no output)"]
    return "\n".join(f"    | {line}" for line in lines[:20])


def canonical_version(version: str) -> str:
    """Compare versions without caring which separators survived.

    "0.10.0-dev.0", "0.10.0.dev0" and "0.10.0dev0" are the same release; so
    are tag v0.10.0-rc.1 and the "0.10.0rc1" importlib.metadata may report.
    Dropping the separators makes all of them compare equal, which is enough
    to catch a binary built from the wrong revision.
    """
    return re.sub(r"[-._]", "", version).lower()


def check_version(executable, home, timeout, expected):
    """--version runs the whole eager import graph.

    Every module under phabfive/cli/ is imported at the top of
    phabfive/cli/__init__.py, so this alone is what catches an undeclared
    dependency like the missing click.
    """
    output = expect_success(executable, ["--version"], home, timeout).strip()

    if not VERSION_PATTERN.match(output):
        raise Failure(f"not a version: {output!r}")
    if expected and canonical_version(output) != canonical_version(expected):
        raise Failure(f"reported {output!r}, expected {expected!r}")

    return output


def check_help(executable, home, timeout):
    """Root help lists every group and ends with the agent footer."""
    output = expect_success(executable, ["--help"], home, timeout)

    for group in COMMAND_GROUPS + ["edit"]:
        if group not in output:
            raise Failure(f"--help does not mention {group!r}")

    # Written by AgentFooterGroup.format_epilog, a click rendering path.
    if "Are you an AI?" not in output:
        raise Failure("--help is missing the agent resources footer")

    return "lists " + ", ".join(COMMAND_GROUPS)


def check_group_help(executable, group, home, timeout):
    """Each group's help proves its module is really in the bundle."""
    expect_success(executable, [group, "--help"], home, timeout)
    return "ok"


def check_skill(executable, home, timeout):
    """--skill reads phabfive/SKILL.md through importlib.resources.

    A data file, not a module: a one-file build without --collect-data
    phabfive, or a wheel that dropped the non-Python file, fails only here.
    """
    output = expect_success(executable, ["--skill"], home, timeout)

    if not output.startswith("---"):
        raise Failure(f"does not start with YAML frontmatter: {output[:60]!r}")
    if "name: phabfive" not in output:
        raise Failure("frontmatter does not name the skill")

    lines = len(output.splitlines())
    if lines < 300:
        raise Failure(f"only {lines} lines -- SKILL.md looks truncated")

    return f"{lines} lines"


def check_completion(executable, home, timeout):
    """Drive click's completion protocol the way a shell does.

    This is the code path that broke: phabfive/cli/shell_completion.py builds
    click CompletionItems, and MonogramGroup offers the monogram letters.
    """
    code, output = run(
        executable,
        [],
        home,
        timeout,
        env_extra={
            "_PHABFIVE_COMPLETE": "complete_bash",
            "COMP_WORDS": "phabfive ",
            "COMP_CWORD": "1",
        },
    )

    if code != 0:
        raise Failure(f"completion exited {code}\n{indent(output)}")

    offered = output.split()
    for expected in ["maniphest", "T"]:
        if expected not in offered:
            raise Failure(f"completion did not offer {expected!r}: {offered[:12]}")

    return f"offers {len(offered)} candidates"


def check_offline_command(executable, arguments, home, timeout):
    """Run a real command against an endpoint that is not there.

    The only check that reaches lazily imported code. requests, phabfive.core,
    phabfive.display and the app modules are imported inside command bodies,
    so --version and --help never touch them -- requests was undeclared for
    exactly as long as click was, and no amount of help output would show it.

    Failing to connect is the expected outcome. What is being checked is that
    it fails *cleanly*, rather than dying on an import on the way there.
    """
    code, output = run(executable, arguments, home, timeout)

    for marker in IMPORT_FAILURES:
        if marker in output:
            raise Failure(
                f"broke on an import rather than on the network\n{indent(output)}"
            )

    if code == 0:
        raise Failure(f"unexpectedly succeeded against {DEAD_URL}\n{indent(output)}")
    if "Failed to connect to Phabricator API" not in output:
        raise Failure(f"no clean connection error\n{indent(output)}")

    return "clean connection error"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--executable", help="path to a phabfive executable")
    target.add_argument(
        "--venv", help="path to a venv holding a phabfive console script"
    )
    parser.add_argument(
        "--expect-version", help="exact PEP 440 version --version must report"
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="seconds per check (default: 120)"
    )
    args = parser.parse_args()

    executable = resolve_executable(args)
    print(f"smoke testing {executable}\n")

    checks = [
        (
            "--version",
            lambda home: check_version(
                executable, home, args.timeout, args.expect_version
            ),
        ),
        ("--help", lambda home: check_help(executable, home, args.timeout)),
    ]
    checks += [
        (
            f"{group} --help",
            (
                lambda g: (
                    lambda home: check_group_help(executable, g, home, args.timeout)
                )
            )(group),
        )
        for group in COMMAND_GROUPS
    ]
    checks += [
        ("--skill", lambda home: check_skill(executable, home, args.timeout)),
        (
            "shell completion",
            lambda home: check_completion(executable, home, args.timeout),
        ),
        (
            "user whoami (offline)",
            lambda home: check_offline_command(
                executable, ["user", "whoami"], home, args.timeout
            ),
        ),
        (
            "maniphest show (offline)",
            lambda home: check_offline_command(
                executable,
                ["--format=json", "maniphest", "show", "T1"],
                home,
                args.timeout,
            ),
        ),
    ]

    failures = []

    # A fresh HOME per check, so nothing one command writes can change what
    # the next one sees.
    for name, check in checks:
        with tempfile.TemporaryDirectory(prefix="phabfive-smoke-") as home:
            try:
                print(f"  ok   {name}: {check(home)}")
            except Failure as failure:
                print(f"  FAIL {name}: {failure}")
                failures.append(name)

    print()
    if failures:
        print(f"{len(failures)} of {len(checks)} checks failed: {', '.join(failures)}")
        return 1

    print(f"all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
