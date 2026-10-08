# -*- coding: utf-8 -*-

"""scripts/support_matrix.py turns a CI run into the grid docs/support-matrix.md is written from.

These cover the parts that do not talk to GitHub: which version a deploy job
tested, the order versions are shown in, and what each JUnit result means. The
last matters most: a strict xfail that passes is a known gap that closed, and
reading it as an ordinary failure would hide exactly the change the support
matrix exists to record.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from support_matrix import grid, outcomes, version_of, version_order  # noqa: E402

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
