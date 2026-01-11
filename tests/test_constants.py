# -*- coding: utf-8 -*-

import re


def test_status_choices():
    from phabfive.constants import REPO_STATUS_CHOICES

    assert "active" in REPO_STATUS_CHOICES
    assert "inactive" in REPO_STATUS_CHOICES


def test_phab_url_validator_without_port():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHAB_URL"]

    # Valid URLs without port
    assert re.match(pattern, "http://127.0.0.1/api/")
    assert re.match(pattern, "http://127.0.0.1/api")
    assert re.match(pattern, "https://phabricator.example.com/api/")
    assert re.match(pattern, "http://my-phab.domain.tld/api/")


def test_phab_url_validator_with_port():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHAB_URL"]

    # Valid URLs with port (issue #53)
    assert re.match(pattern, "http://phabricator.domain.tld:81/api/")
    assert re.match(pattern, "https://localhost:8080/api/")
    assert re.match(pattern, "http://127.0.0.1:3000/api")
    assert re.match(pattern, "http://dev.example.com:443/api/")


def test_phab_url_validator_with_ipv6():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHAB_URL"]

    # Valid URLs with IPv6 addresses (must be in brackets)
    assert re.match(pattern, "http://[::1]/api/")
    assert re.match(pattern, "https://[::1]/api/")
    assert re.match(pattern, "http://[2001:db8::1]/api/")
    assert re.match(pattern, "http://[fe80::1]/api/")
    assert re.match(pattern, "http://[::ffff:127.0.0.1]/api/")


def test_phab_url_validator_with_ipv6_and_port():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHAB_URL"]

    # Valid URLs with IPv6 addresses and port
    assert re.match(pattern, "http://[::1]:8080/api/")
    assert re.match(pattern, "https://[2001:db8::1]:443/api/")
    assert re.match(pattern, "http://[fe80::1]:3000/api")


def test_phab_url_validator_invalid():
    from phabfive.constants import VALIDATORS

    pattern = VALIDATORS["PHAB_URL"]

    # Invalid URLs
    assert not re.match(pattern, "ftp://example.com/api/")
    assert not re.match(pattern, "http://example.com/")
    assert not re.match(pattern, "http://example.com")
    assert not re.match(pattern, "http://:80/api/")
    assert not re.match(pattern, "example.com/api/")
    # Invalid IPv6 (missing brackets)
    assert not re.match(pattern, "http://::1/api/")
    assert not re.match(pattern, "http://2001:db8::1/api/")
