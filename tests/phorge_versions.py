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


# Repositories report isHosted, without which phabfive calls a hosted one not hosted
NO_IS_HOSTED = missing_before("2025.51", "repositories have no isHosted field")
