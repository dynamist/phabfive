# -*- coding: utf-8 -*-

import re


def test_status_choices():
    from phabfive.constants import REPO_STATUS_CHOICES

    assert "active" in REPO_STATUS_CHOICES
    assert "inactive" in REPO_STATUS_CHOICES


def test_host_validator_takes_an_origin():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHORGE_HOST"]

    # The root an instance is reached at, as _resolve_hosts normalizes it
    assert re.match(pattern, "http://127.0.0.1")
    assert re.match(pattern, "https://phabricator.example.com")
    assert re.match(pattern, "http://my-phab.domain.tld")
    # With a port (issue #53)
    assert re.match(pattern, "http://phabricator.domain.tld:81")
    assert re.match(pattern, "https://localhost:8080")
    # IPv6 addresses (must be in brackets), with and without a port
    assert re.match(pattern, "http://[::1]")
    assert re.match(pattern, "http://[2001:db8::1]")
    assert re.match(pattern, "http://[::ffff:127.0.0.1]")
    assert re.match(pattern, "https://[2001:db8::1]:443")


def test_host_validator_refuses_a_path_and_a_bare_name():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHORGE_HOST"]

    assert not re.match(pattern, "ftp://example.com")
    assert not re.match(pattern, "http://example.com/phorge")
    assert not re.match(pattern, "http://example.com/api/")
    assert not re.match(pattern, "http://:80")
    assert not re.match(pattern, "example.com")
    # Invalid IPv6 (missing brackets)
    assert not re.match(pattern, "http://::1")
    assert not re.match(pattern, "http://2001:db8::1")


def test_web_host_validator_takes_an_origin_or_nothing():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHORGE_WEB_HOST"]

    assert re.match(pattern, "")
    assert re.match(pattern, "https://phorge.example.com")
    assert re.match(pattern, "http://phorge.localhost:8080")
    # An endpoint pasted into the web half is a named error, not a bad link
    assert not re.match(pattern, "https://phorge.example.com/api/")
    assert not re.match(pattern, "https://phorge.example.com/x")


def test_api_host_validator_without_port():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHORGE_API_HOST"]

    # Valid URLs without port
    assert re.match(pattern, "http://127.0.0.1/api/")
    assert re.match(pattern, "http://127.0.0.1/api")
    assert re.match(pattern, "https://phabricator.example.com/api/")
    assert re.match(pattern, "http://my-phab.domain.tld/api/")
    # Empty is unset
    assert re.match(pattern, "")


def test_api_host_validator_with_port():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHORGE_API_HOST"]

    # Valid URLs with port (issue #53)
    assert re.match(pattern, "http://phabricator.domain.tld:81/api/")
    assert re.match(pattern, "https://localhost:8080/api/")
    assert re.match(pattern, "http://127.0.0.1:3000/api")
    assert re.match(pattern, "http://dev.example.com:443/api/")


def test_api_host_validator_with_ipv6():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHORGE_API_HOST"]

    # Valid URLs with IPv6 addresses (must be in brackets)
    assert re.match(pattern, "http://[::1]/api/")
    assert re.match(pattern, "https://[::1]/api/")
    assert re.match(pattern, "http://[2001:db8::1]/api/")
    assert re.match(pattern, "http://[fe80::1]/api/")
    assert re.match(pattern, "http://[::ffff:127.0.0.1]/api/")


def test_api_host_validator_with_ipv6_and_port():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHORGE_API_HOST"]

    # Valid URLs with IPv6 addresses and port
    assert re.match(pattern, "http://[::1]:8080/api/")
    assert re.match(pattern, "https://[2001:db8::1]:443/api/")
    assert re.match(pattern, "http://[fe80::1]:3000/api")


def test_api_host_validator_invalid():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHORGE_API_HOST"]

    # Invalid URLs
    assert not re.match(pattern, "ftp://example.com/api/")
    assert not re.match(pattern, "http://example.com/")
    assert not re.match(pattern, "http://example.com")
    assert not re.match(pattern, "http://:80/api/")
    assert not re.match(pattern, "example.com/api/")
    # Invalid IPv6 (missing brackets)
    assert not re.match(pattern, "http://::1/api/")
    assert not re.match(pattern, "http://2001:db8::1/api/")


def test_every_legacy_name_has_a_configurable_successor():
    from phabfive.constants import CONFIGURABLES, LEGACY_CONFIGURABLES

    assert set(LEGACY_CONFIGURABLES.values()) == set(CONFIGURABLES)
    assert all(old.startswith("PHAB_") for old in LEGACY_CONFIGURABLES)
    assert not any(old in CONFIGURABLES for old in LEGACY_CONFIGURABLES)


def test_maniphest_order_tables_agree():
    from phabfive.constants import (
        MANIPHEST_ORDER_CHOICES,
        MANIPHEST_ORDER_DEFAULT,
        MANIPHEST_ORDER_DIRECTIONS,
        MANIPHEST_ORDER_FIELDS,
    )

    assert MANIPHEST_ORDER_DEFAULT in MANIPHEST_ORDER_CHOICES
    assert set(MANIPHEST_ORDER_DIRECTIONS) == set(MANIPHEST_ORDER_FIELDS)

    for field in MANIPHEST_ORDER_FIELDS:
        assert field in MANIPHEST_ORDER_CHOICES
        if MANIPHEST_ORDER_DIRECTIONS[field] is None:
            # A directionless field has exactly one spelling
            assert f"{field}:asc" not in MANIPHEST_ORDER_CHOICES
        else:
            assert f"{field}:asc" in MANIPHEST_ORDER_CHOICES
            assert f"{field}:desc" in MANIPHEST_ORDER_CHOICES
