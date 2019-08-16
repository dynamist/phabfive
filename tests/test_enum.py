# -*- coding: utf-8 -*-

# 3rd party imports
import pytest


def test_import_status():
    try:
        from phabfive.constants import Status
    except ImportError:
        pytest.fail("Unexpected ImportError")


def test_status_enum_value():
    """This tests that the __str__() method is returning our value as intended."""
    from phabfive.constants import Status

    assert str(Status.ACTIVE) == "active"
    assert str(Status.INACTIVE) == "inactive"
