#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Print which tests each Phorge and Phabricator version passes, from one CI run.

docs/support-matrix.md is measured by running the Kubernetes workflow against
many versions at once, each of which uploads its JUnit results as
test-results-<version>. This turns such a run into the grid the doc is written
from: one row per test that does not pass everywhere, one column per version,
and the versions that never got as far as testing, with the end of their log.

    python3 scripts/support_matrix.py 36415788571
    python3 scripts/support_matrix.py 36415788571 --all
    python3 scripts/support_matrix.py 36415788571 --json matrix.json

It reads the run through the gh CLI, so gh has to be logged in. Stdlib only,
like the other scripts here.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = "dynamist/phabfive"

# A deploy job is named after what it tests, see the decide job in k8s.yml
JOB_NAME = re.compile(r"Deploy and test (Phorge|Phabricator) (\S+) in k3d")

SYMBOLS = {
    "passed": ".",
    "failed": "F",
    "xfailed": "x",
    "xpassed": "X",
    "skipped": "s",
    "missing": "-",
}


def version_of(job_name):
    """The VERSION a deploy job tested, or None for any other job.

    "Phorge 2025.51" tested 2025.51 and "Phabricator stable" phabricator-stable.
    """
    match = JOB_NAME.fullmatch(job_name)
    if not match:
        return None
    product, ref = match.groups()
    return f"phabricator-{ref}" if product == "Phabricator" else ref


def version_order(version):
    """Oldest first: Phabricator, then the Phorge releases, then stable and master."""
    if version.startswith("phabricator-"):
        return (0, (), version != "phabricator-stable", version)
    if re.fullmatch(r"\d{4}\.\d+", version):
        return (1, tuple(int(part) for part in version.split(".")), False, "")
    return (2, (), version != "stable", version)


def outcomes(junit_xml):
    """{test id: (outcome, message)} from a pytest JUnit report.

    A strict xfail that passed is reported by pytest as a failure whose message
    starts [XPASS(strict)], which here is its own outcome, xpassed: it means a
    known gap has closed, not that something broke.
    """
    found = {}
    for case in ET.fromstring(junit_xml).iter("testcase"):
        test = f"{case.get('classname')}::{case.get('name')}"
        outcome, message = "passed", ""
        for child in case:
            message = child.get("message") or ""
            if child.tag in ("failure", "error"):
                outcome = "xpassed" if message.startswith("[XPASS") else "failed"
            elif child.tag == "skipped":
                outcome = (
                    "xfailed" if child.get("type") == "pytest.xfail" else "skipped"
                )
        found[test] = (outcome, message)
    return found


def grid(results, versions, show_all=False):
    """The text grid of results {version: {test: (outcome, message)}}.

    Only tests that do not pass on every version, unless show_all.
    """
    tests = sorted(
        {test for found in results.values() for test in found},
        key=lambda t: t.split("::")[-1],
    )
    rows = []
    for test in tests:
        row = [
            results.get(version, {}).get(test, ("missing", ""))[0]
            for version in versions
        ]
        if show_all or set(row) != {"passed"}:
            rows.append((test.split("::")[-1], row))
    width = max([len(name) for name, _ in rows] + [4])
    heads = [version.replace("phabricator-", "phab-") for version in versions]
    lines = [" " * width + "  " + " ".join(heads)]
    for name, row in rows:
        cells = (
            SYMBOLS[outcome].center(len(head)) for outcome, head in zip(row, heads)
        )
        lines.append(name.ljust(width) + "  " + " ".join(cells))
    return "\n".join(lines)


def gh(*args):
    return subprocess.run(
        ["gh", *args, "-R", REPO], check=True, capture_output=True, text=True
    ).stdout


def deploy_jobs(run):
    """{version: (job id, whether it deployed)} of the run's deploy jobs."""
    jobs = {}
    for job in json.loads(gh("run", "view", run, "--json", "jobs"))["jobs"]:
        version = version_of(job["name"])
        if version:
            steps = {step["name"]: step["conclusion"] for step in job["steps"]}
            jobs[version] = (job["databaseId"], steps.get("Deploy") == "success")
    return jobs


def failure_tail(job, lines=15):
    """The end of the Deploy step of a job that did not deploy, PHP notices left out."""
    log = subprocess.run(
        ["gh", "api", f"repos/{REPO}/actions/jobs/{job}/logs"],
        capture_output=True,
        text=True,
    ).stdout
    log = re.sub(r"\x1b\[[0-9;]*m", "", log)
    kept = [
        re.sub(r"^\S+Z ", "", line)
        for line in log.splitlines()
        if "ERROR 8192" not in line and not re.match(r"\S+Z\s+#\d+ ", line)
    ]
    start = next((i for i, line in enumerate(kept) if "cannot start" in line), None)
    return kept[start : start + lines] if start is not None else kept[-lines:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run", help="id of a Kubernetes workflow run")
    parser.add_argument(
        "--all", action="store_true", help="list every test, not only those that differ"
    )
    parser.add_argument(
        "--json", metavar="FILE", help="also write every result to FILE"
    )
    args = parser.parse_args(argv)

    # A job still running has not deployed yet, which is not the same as failing to
    status = json.loads(gh("run", "view", args.run, "--json", "status"))["status"]
    if status != "completed":
        sys.exit(
            f"Error: run {args.run} is {status.replace('_', ' ')}, wait until it completes"
        )

    jobs = deploy_jobs(args.run)
    if not jobs:
        sys.exit(f"Error: run {args.run} has no deploy jobs")
    versions = sorted(jobs, key=version_order)

    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [
                "gh",
                "run",
                "download",
                args.run,
                "-R",
                REPO,
                "-p",
                "test-results-*",
                "-D",
                tmp,
            ],
            capture_output=True,
        )
        for version in versions:
            for report in sorted(Path(tmp, f"test-results-{version}").glob("*.xml")):
                results.setdefault(version, {}).update(outcomes(report.read_text()))

    print(f"Run {args.run}: " + ", ".join(versions))
    print()
    for version in versions:
        counts = {}
        for outcome, _ in results.get(version, {}).values():
            counts[outcome] = counts.get(outcome, 0) + 1
        summary = (
            ", ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items()))
            or "no results"
        )
        print(
            f"  {version:>20}: {summary}"
            + ("" if jobs[version][1] else " (did not deploy)")
        )
    print()
    print(grid(results, versions, show_all=args.all))
    print()
    print(
        "  " + "  ".join(f"{symbol} {outcome}" for outcome, symbol in SYMBOLS.items())
    )

    for version in versions:
        if not jobs[version][1]:
            print(f"\n{version} did not deploy:")
            print("\n".join("    " + line for line in failure_tail(jobs[version][0])))

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "run": args.run,
                    "versions": {v: {"deployed": jobs[v][1]} for v in versions},
                    "results": results,
                },
                indent=1,
            )
        )


if __name__ == "__main__":
    main()
