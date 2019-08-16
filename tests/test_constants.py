# -*- coding: utf-8 -*-

# 3rd party imports
import pytest


def test_status_choices():
    from phabfive.constants import REPO_STATUS_CHOICES

    assert "active" in REPO_STATUS_CHOICES
    assert "inactive" in REPO_STATUS_CHOICES
