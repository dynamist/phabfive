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

The weekly run is such a measurement, and the support-matrix workflow keeps the
doc's "Last measured on" line on it. --update-doc judges the run against the
run the doc cites: when every version deployed, nothing failed, no known gap
closed and the same versions were tested, it moves the line to this run and
exits 0. Otherwise it leaves the doc alone and exits 3, and --report holds what
changed, for a person to write into the doc:

    python3 scripts/support_matrix.py 36415788571 --update-doc docs/support-matrix.md --report report.md

It reads the run through the gh CLI, so gh has to be logged in. Stdlib only,
like the other scripts here.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = "dynamist/phabfive"

# A deploy job is named after what it tests, see the decide job in k8s.yml
JOB_NAME = re.compile(r"Deploy and test (Phorge|Phabricator) (\S+) in k3d")

# The line of docs/support-matrix.md that names the run the doc was written from
MEASURED = re.compile(
    r"^Last measured on \[(?P<date>\d{4}-\d{2}-\d{2})\]"
    r"\((?P<url>https://github\.com/[^/]+/[^/]+/actions/runs/(?P<run>\d+))\)\.$",
    re.MULTILINE,
)

# Exit status of --update-doc when the run does not agree with the doc
DRIFT = 3

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


def run_url(run):
    return f"https://github.com/{REPO}/actions/runs/{run}"


def cited_run(doc):
    """The id of the run the doc says it was measured on, or None."""
    match = MEASURED.search(doc)
    return match["run"] if match else None


def bump_measured(doc, date, url):
    """The doc with its "Last measured on" line moved to the run at url."""
    return MEASURED.sub(f"Last measured on [{date}]({url}).", doc, count=1)


def verdict(jobs, results, cited_versions):
    """What keeps this run from standing for the doc, empty when nothing does.

    jobs is {version: (job id, deployed, tests passed)}, results {version: {test: (outcome,
    message)}} and cited_versions the versions the run the doc cites tested,
    or None when that run cannot be read. A run that concluded success is not
    enough: master may fail without failing the run, and a version that
    uploaded no results would otherwise look like one with nothing to report.
    """
    problems = []
    for version in sorted(jobs, key=version_order):
        if not jobs[version][1]:
            problems.append(f"{version} did not deploy")
            continue
        found = results.get(version, {})
        if not found:
            problems.append(f"{version} reported no test results")
        elif not jobs[version][2] and not any(
            outcome in ("failed", "xpassed") for outcome, _ in found.values()
        ):
            problems.append(f"{version}: the tests did not run to the end")
        for outcome, what in (
            ("failed", "fails"),
            ("xpassed", "passes, so a known gap has closed"),
        ):
            for test in sorted(t for t, (o, _) in found.items() if o == outcome):
                problems.append(f"{version}: `{test.split('::')[-1]}` {what}")
    if cited_versions is None:
        problems.append("the run the doc cites cannot be read")
    else:
        for version in sorted(set(jobs) - set(cited_versions), key=version_order):
            problems.append(f"{version} is not on the page yet")
        for version in sorted(set(cited_versions) - set(jobs), key=version_order):
            problems.append(f"{version} is on the page but was not tested")
    return problems


# A GitHub issue body holds 65536 characters, and the report becomes one
REPORT_LIMIT = 60000


def report(verdict_lines, grid_lines, limit=REPORT_LIMIT):
    """The verdict and the grid as Markdown, the grid cut short to fit limit."""
    head = "\n".join(verdict_lines) + "\n\n```\n"
    tail = "```\n"
    body = ""
    for line in grid_lines:
        if len(head) + len(body) + len(line) + 40 + len(tail) > limit:
            body += "... cut short, run the script for the rest\n"
            break
        body += line + "\n"
    return head + body + tail


def gh(*args):
    return subprocess.run(
        ["gh", *args, "-R", REPO], check=True, capture_output=True, text=True
    ).stdout


def deploy_jobs(run):
    """{version: (job id, whether it deployed, whether its tests ran to the end)}.

    Tests that ran to the end with failures still count as ended: the JUnit
    says which failed. One that did not end, timed out or crashed before
    pytest wrote a result, may leave JUnit that only looks clean.
    """
    jobs = {}
    for job in json.loads(gh("run", "view", run, "--json", "jobs"))["jobs"]:
        version = version_of(job["name"])
        if version:
            steps = {step["name"]: step["conclusion"] for step in job["steps"]}
            jobs[version] = (
                job["databaseId"],
                steps.get("Deploy") == "success",
                steps.get("Test") == "success",
            )
    return jobs


def download_results(run, directory, attempts=3):
    """Download the run's test-results-* artifacts into directory.

    A download that comes back short would read as versions with no results, a
    drift that never happened, so every artifact the run lists has to arrive,
    or it is retried and then raised. A run with none downloads nothing: gh
    run download fails when no artifact matches.
    """
    names = [
        name
        for name in gh_api(
            f"repos/{REPO}/actions/runs/{run}/artifacts",
            "--paginate",
            "--jq",
            ".artifacts[].name",
        ).split()
        if name.startswith("test-results-")
    ]
    if not names:
        return
    for attempt in range(1, attempts + 1):
        shutil.rmtree(directory, ignore_errors=True)
        done = subprocess.run(
            ["gh", "run", "download", run, "-R", REPO, "-p", "test-results-*"]
            + ["-D", directory],
            capture_output=True,
            text=True,
        )
        missing = [name for name in names if not Path(directory, name).is_dir()]
        if done.returncode == 0 and not missing:
            return
        if attempt == attempts:
            sys.exit(
                f"Error: could not download the results of run {run}: "
                + (done.stderr.strip() or "missing " + ", ".join(missing))
            )
        time.sleep(10 * attempt)


def gh_api(*args):
    return subprocess.run(
        ["gh", "api", *args], check=True, capture_output=True, text=True
    ).stdout


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
    parser.add_argument(
        "--update-doc",
        metavar="DOC",
        help="move DOC's Last measured line to this run if it agrees, else exit 3",
    )
    parser.add_argument(
        "--report",
        metavar="FILE",
        help="with --update-doc, write the verdict and the grid to FILE as Markdown",
    )
    args = parser.parse_args(argv)
    if args.report and not args.update_doc:
        parser.error("--report needs --update-doc")

    # A job still running has not deployed yet, which is not the same as failing to
    run = json.loads(gh("run", "view", args.run, "--json", "status,createdAt"))
    status = run["status"]
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
        download_results(args.run, tmp)
        for version in versions:
            for report in sorted(Path(tmp, f"test-results-{version}").glob("*.xml")):
                results.setdefault(version, {}).update(outcomes(report.read_text()))

    out = [f"Run {args.run}: " + ", ".join(versions), ""]
    for version in versions:
        counts = {}
        for outcome, _ in results.get(version, {}).values():
            counts[outcome] = counts.get(outcome, 0) + 1
        summary = (
            ", ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items()))
            or "no results"
        )
        out.append(
            f"  {version:>20}: {summary}"
            + ("" if jobs[version][1] else " (did not deploy)")
        )
    out += [
        "",
        grid(results, versions, show_all=args.all),
        "",
        "  " + "  ".join(f"{symbol} {outcome}" for outcome, symbol in SYMBOLS.items()),
    ]
    for version in versions:
        if not jobs[version][1]:
            out += ["", f"{version} did not deploy:"]
            out += ["    " + line for line in failure_tail(jobs[version][0])]
    print("\n".join(out))

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

    if args.update_doc:
        sys.exit(update_doc(args, run["createdAt"][:10], jobs, results, out))


def update_doc(args, date, jobs, results, out):
    """Judge the run against the doc, update it or report why not, the exit status."""
    doc_path = Path(args.update_doc)
    doc = doc_path.read_text()
    cited = cited_run(doc)
    if cited is None:
        problems = [f"{doc_path} has no Last measured on line naming a run"]
    else:
        try:
            cited_versions = deploy_jobs(cited)
        except subprocess.CalledProcessError:
            cited_versions = None
        problems = verdict(jobs, results, cited_versions)

    if problems:
        verdict_text = [
            f"Run [{args.run}]({run_url(args.run)}) does not agree with "
            + (f"run [{cited}]({run_url(cited)}), " if cited else "")
            + f"which `{doc_path}` was written from:",
            "",
            *(f"- {problem}" for problem in problems),
        ]
    else:
        doc_path.write_text(bump_measured(doc, date, run_url(args.run)))
        verdict_text = [
            f"Run [{args.run}]({run_url(args.run)}) agrees with run "
            f"[{cited}]({run_url(cited)}): `{doc_path}` is now measured on {date}."
        ]
    print("\n" + "\n".join(verdict_text))
    if args.report:
        Path(args.report).write_text(report(verdict_text, out))
    return DRIFT if problems else 0


if __name__ == "__main__":
    main()
