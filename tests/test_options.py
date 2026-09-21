# -*- coding: utf-8 -*-

"""How an option that takes several values is read."""

# 3rd party imports
import pytest

# phabfive imports
from phabfive.options import split_list_option


@pytest.mark.parametrize(
    "values, expected",
    [
        (None, []),
        ([], []),
        ("@a", ["@a"]),
        (["@a,@b", "@c"], ["@a", "@b", "@c"]),
        (["@a, @b"], ["@a", "@b"]),
        (["@a,", ",@b", ""], ["@a", "@b"]),
        (["@a,@b", "@a"], ["@a", "@b"]),
    ],
)
def test_split_list_option(values, expected):
    assert split_list_option(values) == expected


def test_plus_is_not_a_separator():
    """In a search filter + means AND; a list of values has no use for it."""
    assert split_list_option(["a+b"]) == ["a+b"]
