# -*- coding: utf-8 -*-
"""Phorge answers through the shared Traefik ingress, and to its Service name."""

# python std lib
import json
import os
import re
import subprocess

# 3rd party imports
import pytest
import requests

from tests.k8s.conftest import TRAEFIK_404


def test_home_page(phorge_url):
    response = requests.get(phorge_url, timeout=30)
    assert response.status_code == 200
    assert "Phorge" in response.text or "Phabricator" in response.text


def test_file_domain_is_routed_to_phorge(cdn_url):
    response = requests.get(cdn_url, timeout=30)
    assert TRAEFIK_404 not in response.text, "cdn.localhost is not routed to Phorge"


def test_unknown_host_is_not_routed(phorge_url):
    response = requests.get(
        "http://127.0.0.1/", headers={"Host": "nope.localhost"}, timeout=30
    )
    assert response.status_code == 404
    assert TRAEFIK_404 in response.text


def test_api_token(conduit):
    assert conduit("user.whoami")["userName"] == "admin"


def test_wrong_api_token_is_rejected(phorge_url):
    response = requests.post(
        f"{phorge_url}/api/user.whoami", data={"api.token": "api-wrong"}, timeout=30
    )
    assert response.json()["error_code"] is not None


def whoami_as_host(kubectl, host):
    """user.whoami sent to Apache in the Phorge pod, with `host` as the Host header.

    From inside the pod, since the NetworkPolicy admits only named namespaces
    to the Service. The Host header is all Phorge decides the site by.
    """
    script = """
    $context = stream_context_create(["http" => [
        "method" => "POST",
        "ignore_errors" => true,
        "header" => "Host: " . $argv[1] . "\\r\\n"
            . "Content-Type: application/x-www-form-urlencoded",
        "content" => "api.token=" . getenv("PHORGE_ADMIN_TOKEN"),
    ]]);
    echo file_get_contents("http://127.0.0.1/api/user.whoami", false, $context);
    """
    return kubectl("exec", "deploy/phorge", "--", "php", "-r", script, host).stdout


def test_service_name_is_answered(kubectl):
    body = whoami_as_host(kubectl, "web.phorge.svc.cluster.local")
    assert json.loads(body)["result"]["userName"] == "admin"


def test_unknown_host_is_not_answered(kubectl):
    """Control for the test above, Phorge must still check the Host header."""
    assert "Site Not Found" in whoami_as_host(kubectl, "nope.localhost")


def test_phorge_version(kubectl):
    """The deployed Phorge is the VERSION it was built from (make passes PHORGE_VERSION)."""
    version = os.environ.get("PHORGE_VERSION")
    if not version:
        pytest.skip("no PHORGE_VERSION, run through make test-k8s")
    deployed = kubectl("exec", "deploy/phorge", "--", "printenv", "PHORGE_VERSION")
    assert deployed.stdout.strip() == version, (
        "the deployed image is of another version"
    )
    if not re.fullmatch(r"\d{4}\.\d+", version):
        return  # a branch may have moved on since the build
    head = kubectl(
        "exec", "deploy/phorge", "--", "git", "-C", "/app/phorge", "rev-parse", "HEAD"
    )
    remote = subprocess.run(
        [
            "git",
            "ls-remote",
            "https://github.com/phorgeit/phorge.git",
            f"refs/tags/{version}^{{}}",
            f"refs/tags/{version}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    commits = [line.split()[0] for line in remote.stdout.splitlines()]
    assert head.stdout.strip() in commits, f"the deployed Phorge is not tag {version}"
