# -*- coding: utf-8 -*-

"""scripts/support_matrix.py turns a CI run into the grid docs/support-matrix.md is written from.

These cover the parts that do not talk to GitHub: which version a deploy job
tested, the order versions are shown in, what each JUnit result means, and
whether a weekly run agrees with the run the doc cites. The xpass matters most: a strict xfail that passes is a known gap that closed, and
reading it as an ordinary failure would hide exactly the change the support
matrix exists to record.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from support_matrix import (  # noqa: E402
    bump_measured,
    cited_run,
    grid,
    outcomes,
    report,
    verdict,
    version_of,
    version_order,
)

DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "support-matrix.md"

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.e2e.test_cli" name="test_passes"/>
  <testcase classname="tests.e2e.test_cli" name="test_fails">
    <failure message="AssertionError: assert False">trace</failure>
  </testcase>
  <testcase classname="tests.e2e.test_cli" name="test_errors">
    <error message="failed on setup">trace</error>
  </testcase>
  <testcase classname="tests.e2e.test_project" name="test_known_gap">
    <skipped type="pytest.xfail" message="before Phorge 2026.27: crashes"/>
  </testcase>
  <testcase classname="tests.e2e.test_project" name="test_gap_closed">
    <failure message="[XPASS(strict)] before Phorge 2025.51: no status constraint"/>
  </testcase>
  <testcase classname="tests.k8s.test_smoke" name="test_skipped">
    <skipped type="pytest.skip" message="no PHORGE_VERSION"/>
  </testcase>
</testsuite></testsuites>
"""


class TestVersionOf:
    def test_a_phorge_job_tested_its_version(self):
        assert version_of("Deploy and test Phorge 2025.51 in k3d") == "2025.51"
        assert version_of("Deploy and test Phorge stable in k3d") == "stable"

    def test_a_phabricator_job_tested_phabricator_of_its_branch(self):
        assert (
            version_of("Deploy and test Phabricator stable in k3d")
            == "phabricator-stable"
        )
        assert (
            version_of("Deploy and test Phabricator master in k3d")
            == "phabricator-master"
        )

    def test_other_jobs_are_not_deploy_jobs(self):
        assert version_of("Validate manifests") is None
        assert version_of("Coexistence with other apps in one cluster") is None


def test_versions_run_from_oldest_to_newest():
    versions = [
        "master",
        "2025.51",
        "phabricator-master",
        "stable",
        "2023.17",
        "phabricator-stable",
        "2026.27",
    ]
    assert sorted(versions, key=version_order) == [
        "phabricator-stable",
        "phabricator-master",
        "2023.17",
        "2025.51",
        "2026.27",
        "stable",
        "master",
    ]


def test_releases_sort_by_number_not_text():
    assert sorted(["2025.9", "2025.18"], key=version_order) == ["2025.9", "2025.18"]


class TestOutcomes:
    def setup_method(self):
        self.found = {
            test.split("::")[-1]: outcome
            for test, (outcome, _) in outcomes(JUNIT).items()
        }

    def test_each_kind_of_result(self):
        assert self.found == {
            "test_passes": "passed",
            "test_fails": "failed",
            "test_errors": "failed",
            "test_known_gap": "xfailed",
            "test_gap_closed": "xpassed",
            "test_skipped": "skipped",
        }

    def test_a_strict_xpass_is_not_a_failure(self):
        """pytest reports it as a failure, but it means a gap closed."""
        assert self.found["test_gap_closed"] == "xpassed"

    def test_the_message_is_kept(self):
        _, message = outcomes(JUNIT)["tests.e2e.test_project::test_known_gap"]
        assert message == "before Phorge 2026.27: crashes"


class TestGrid:
    results = {
        "phabricator-stable": {
            "t::test_same": ("passed", ""),
            "t::test_gap": ("xfailed", ""),
        },
        "stable": {"t::test_same": ("passed", ""), "t::test_gap": ("passed", "")},
    }

    def test_only_tests_that_differ(self):
        text = grid(self.results, ["phabricator-stable", "stable"])
        assert "test_gap" in text
        assert "test_same" not in text

    def test_every_test_on_request(self):
        assert "test_same" in grid(
            self.results, ["phabricator-stable", "stable"], show_all=True
        )

    def test_a_version_without_the_test_shows_it_missing(self):
        text = grid(
            {"stable": {"t::test_gap": ("failed", "")}, "master": {}},
            ["stable", "master"],
        )
        assert text.splitlines()[1].split()[1:] == ["F", "-"]

    def test_phabricator_columns_are_shortened(self):
        assert grid(self.results, ["phabricator-stable", "stable"]).splitlines()[
            0
        ].split() == [
            "phab-stable",
            "stable",
        ]


class TestLastMeasured:
    def test_the_doc_cites_a_run(self):
        assert cited_run(DOC.read_text()).isdigit()

    def test_a_doc_without_the_line_cites_nothing(self):
        assert cited_run("# Support Matrix\n\nMeasured some time.\n") is None

    def test_the_bump_changes_that_line_only(self):
        doc = DOC.read_text()
        url = "https://github.com/dynamist/phabfive/actions/runs/1"
        bumped = bump_measured(doc, "2030-01-07", url)
        changed = [
            (old, new)
            for old, new in zip(doc.splitlines(), bumped.splitlines())
            if old != new
        ]
        assert len(changed) == 1
        assert changed[0][1] == f"Last measured on [2030-01-07]({url})."
        assert cited_run(bumped) == "1"


class TestVerdict:
    jobs = {"phabricator-stable": (1, True, True), "stable": (2, True, True)}
    results = {
        "phabricator-stable": {"t::test_gap": ("xfailed", "")},
        "stable": {"t::test_gap": ("passed", "")},
    }

    def test_a_run_like_the_cited_one_agrees(self):
        assert verdict(self.jobs, self.results, self.jobs) == []

    def test_a_failure_disagrees(self):
        results = {**self.results, "stable": {"t::test_gap": ("failed", "")}}
        assert verdict(self.jobs, results, self.jobs) == ["stable: `test_gap` fails"]

    def test_a_closed_gap_disagrees(self):
        results = {
            **self.results,
            "phabricator-stable": {"t::test_gap": ("xpassed", "")},
        }
        assert verdict(self.jobs, results, self.jobs) == [
            "phabricator-stable: `test_gap` passes, so a known gap has closed"
        ]

    def test_a_version_that_did_not_deploy_disagrees(self):
        jobs = {**self.jobs, "stable": (2, False, False)}
        assert verdict(jobs, self.results, self.jobs) == ["stable did not deploy"]

    def test_tests_that_did_not_end_disagree_even_when_what_they_wrote_passed(self):
        jobs = {**self.jobs, "stable": (2, True, False)}
        assert verdict(jobs, self.results, self.jobs) == [
            "stable: the tests did not run to the end"
        ]

    def test_tests_that_failed_are_reported_as_failures_only(self):
        jobs = {**self.jobs, "stable": (2, True, False)}
        results = {**self.results, "stable": {"t::test_gap": ("failed", "")}}
        assert verdict(jobs, results, self.jobs) == ["stable: `test_gap` fails"]

    def test_a_version_without_results_disagrees(self):
        results = {"phabricator-stable": self.results["phabricator-stable"]}
        assert verdict(self.jobs, results, self.jobs) == [
            "stable reported no test results"
        ]

    def test_a_new_release_is_not_on_the_page_yet(self):
        jobs = {**self.jobs, "2027.05": (3, True, True)}
        results = {**self.results, "2027.05": {"t::test_gap": ("passed", "")}}
        assert verdict(jobs, results, self.jobs) == ["2027.05 is not on the page yet"]

    def test_a_version_no_longer_tested_disagrees(self):
        cited = {**self.jobs, "phabricator-master": (3, True, True)}
        assert verdict(self.jobs, self.results, cited) == [
            "phabricator-master is on the page but was not tested"
        ]

    def test_an_unreadable_cited_run_disagrees(self):
        assert verdict(self.jobs, self.results, None) == [
            "the run the doc cites cannot be read"
        ]


def test_the_report_fits_an_issue():
    text = report(["Run 1 does not agree:"], ["x" * 100] * 10000, limit=5000)
    assert len(text) <= 5000
    assert text.endswith("cut short, run the script for the rest\n```\n")
