# -*- coding: utf-8 -*-
"""What the Phorge under test has, for tests that differ between versions.

`make test-k8s` and `make test-e2e` pass the VERSION it was built from as
PHORGE_VERSION. docs/support-matrix.md is the user-facing account of the same
gaps, keep the two in step.
"""

# python std lib
import os
import re

# 3rd party imports
import pytest


def phorge_older_than(release):
    """Whether the Phorge under test is a release before `release`, e.g. "2026.27".

    Phabricator, which Phorge was forked from, is older than every release. A
    branch (stable, master), or no version at all, counts as current.
    """
    version = os.environ.get("PHORGE_VERSION", "")
    if version.startswith("phabricator-"):
        return True
    if not re.fullmatch(r"\d{4}\.\d+", version):
        return False
    return tuple(map(int, version.split("."))) < tuple(map(int, release.split(".")))


def missing_before(release, reason):
    """Expect a test to fail on a Phorge before `release`, which lacks what it needs.

    Strict, so the test fails as soon as it passes there: either phabfive
    learned to work without it, and this mark and docs/support-matrix.md
    should say so, or the version was misjudged.
    """
    return pytest.mark.xfail(
        phorge_older_than(release),
        reason=f"before Phorge {release}: {reason}",
        strict=True,
    )


#: Expect a test that reads more than one page of tasks to fail on Phorge
#: 2023.32, whose `maniphest.search` cannot read the cursor of a next page on
#: PHP 8.1 and newer (fixed in 2023.49, and older releases run on PHP 8.0).
#: Not strict: it only fails once the instance holds more than 100 tasks,
#: which depends on what ran before.
no_task_paging = pytest.mark.xfail(
    os.environ.get("PHORGE_VERSION", "") == "2023.32",
    reason="Phorge 2023.32: maniphest.search cannot read a second page",
    strict=False,
)
