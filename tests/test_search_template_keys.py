# -*- coding: utf-8 -*-
"""The keys a search template may use are exactly the keys the CLI reads.

The CLI reads each template key with get_param(..., yaml_params, "key"), while
_load_search_config refuses any key not in SEARCH_TEMPLATE_KEYS. The two lists
drifted: created-before, updated-before, space, limit, show-policy and all were
documented and read, yet a template using them failed to load.
"""

import re
from pathlib import Path

import pytest

import phabfive.cli.maniphest as cli_maniphest
from phabfive.constants import SEARCH_TEMPLATE_KEYS
from phabfive.maniphest import Maniphest


def _keys_the_cli_reads():
    source = Path(cli_maniphest.__file__).read_text(encoding="utf-8")
    return set(re.findall(r'yaml_params,\s*"([a-z_-]+)"', source))


def test_the_cli_reads_template_keys():
    # Guards the regex below it: an empty set would make the next test pass
    assert {"tag", "status", "created-before"} <= _keys_the_cli_reads()


def test_every_key_the_cli_reads_is_accepted():
    assert _keys_the_cli_reads() == set(SEARCH_TEMPLATE_KEYS)


@pytest.mark.parametrize(
    "line",
    [
        "created-before: 1y",
        "updated-before: 1y",
        "space: S1",
        "limit: 0",
        "show-policy: true",
        "status: any",
        "all: true",
    ],
)
def test_template_with_key_loads(tmp_path, line):
    template = tmp_path / "search.yaml"
    template.write_text(f"search:\n  {line}\n")
    maniphest = Maniphest.__new__(Maniphest)

    configs = maniphest._load_search_config(str(template))

    key = line.split(":")[0]
    assert key in configs[0]["search"]
