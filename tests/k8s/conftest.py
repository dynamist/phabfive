# -*- coding: utf-8 -*-
"""Fixtures for tests against Phorge deployed in the shared k3d cluster.

Run with `make test-k8s`, which sets PHABFIVE_LIVE_TESTS and puts kubectl on
PATH.
"""

# python std lib
import json
import os
import subprocess
from pathlib import Path

# 3rd party imports
import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
KUBE_CONTEXT = "k3d-dynamist-dev"
NAMESPACE = "phorge"
TRAEFIK_404 = "404 page not found"


class Kubectl:
    def __call__(self, *args, check=True, namespace=NAMESPACE, timeout=300):
        cmd = ["kubectl", "--context", KUBE_CONTEXT, "-n", namespace, *args]
        return subprocess.run(
            cmd, check=check, capture_output=True, text=True, timeout=timeout
        )


class Conduit:
    def __init__(self, url, token):
        self.url, self.token = url, token

    def __call__(self, method, token=None, **params):
        data = {"api.token": token or self.token}
        for key, value in params.items():
            data.update(_flatten(key, value))
        response = requests.post(f"{self.url}/api/{method}", data=data, timeout=60)
        response.raise_for_status()
        body = response.json()
        assert body["error_code"] is None, f"{method}: {body['error_info']}"
        return body["result"]


def _flatten(key, value):
    """Encode nested params the way Conduit expects form data (constraints[names][0]=...)."""
    if isinstance(value, dict):
        return {
            k: v
            for sub, item in value.items()
            for k, v in _flatten(f"{key}[{sub}]", item).items()
        }
    if isinstance(value, list):
        return {
            k: v
            for i, item in enumerate(value)
            for k, v in _flatten(f"{key}[{i}]", item).items()
        }
    return {key: value}


@pytest.fixture(scope="session")
def phorge_url():
    return os.environ.get("PHORGE_URL", "http://phorge.localhost")


@pytest.fixture(scope="session")
def cdn_url():
    return os.environ.get("PHORGE_CDN_URL", "http://cdn.localhost")


@pytest.fixture(scope="session")
def conduit(phorge_url):
    return Conduit(
        phorge_url, os.environ.get("PHAB_TOKEN", "api-supersecr3tapikeyfordevelop1")
    )


@pytest.fixture(scope="session")
def kubectl():
    return Kubectl()


@pytest.fixture(scope="session")
def seed():
    """The records phorge/seed/ creates, keyed by module: users, teams, ..."""
    data = {}
    for path in sorted((ROOT / "phorge/seed/data").glob("*.json")):
        data[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
def admin_username():
    return os.environ.get("PHORGE_ADMIN_USER", "admin")
