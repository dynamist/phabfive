# -*- coding: utf-8 -*-
"""Phorge keeps to its namespace in the shared cluster."""

# python std lib
import json
import subprocess
import uuid

# 3rd party imports
import pytest
import yaml

from tests.k8s.conftest import KUBE_CONTEXT, NAMESPACE, ROOT


def probe_from(kubectl, namespace, host, port):
    """Try a TCP connection from a throwaway pod, return True when it connects."""
    result = kubectl(
        "run",
        f"probe-{uuid.uuid4().hex[:8]}",
        "--rm",
        "-i",
        "--quiet",
        "--restart=Never",
        "--image=busybox:1.37",
        "--",
        "sh",
        "-c",
        f"nc -z -w 5 {host} {port} && echo OPEN || echo CLOSED",
        namespace=namespace,
        check=False,
        timeout=180,
    )
    assert "OPEN" in result.stdout or "CLOSED" in result.stdout, (
        result.stdout + result.stderr
    )
    return "OPEN" in result.stdout


def other_namespaces(kubectl):
    """default plus the namespaces of other dev apps in the shared cluster."""
    selector = f"dynamist.se/dev-app,dynamist.se/dev-app!={NAMESPACE}"
    others = json.loads(
        kubectl("get", "namespaces", "-l", selector, "-o", "json").stdout
    )["items"]
    return ["default", *(ns["metadata"]["name"] for ns in others)]


@pytest.mark.parametrize(
    "host,port",
    [
        ("phorge.phorge.svc.cluster.local", 80),
        ("mariadb.phorge.svc.cluster.local", 3306),
    ],
)
def test_other_namespaces_cannot_reach_phorge(kubectl, host, port):
    for namespace in other_namespaces(kubectl):
        assert not probe_from(kubectl, namespace, host, port), (
            f"{host}:{port} is reachable from {namespace}"
        )


def test_probe_detects_open_ports(kubectl):
    """Control for the tests above, a probe that can never connect would pass them."""
    assert probe_from(kubectl, "default", "traefik.kube-system.svc.cluster.local", 80)


def test_phorge_reaches_its_database(kubectl):
    result = kubectl(
        "exec",
        "deploy/phorge",
        "--",
        "sh",
        "-c",
        'mysql -h"$MYSQL_HOST" -u"$MYSQL_USER" -p"$MYSQL_PASS" -e "SELECT 1"',
    )
    assert "1" in result.stdout


def test_containers_have_requests_and_memory_limits(kubectl):
    pods = json.loads(kubectl("get", "pods", "-o", "json").stdout)["items"]
    for pod in pods:
        for container in pod["spec"]["containers"]:
            resources = container.get("resources", {})
            assert resources.get("requests"), (
                f"{pod['metadata']['name']}/{container['name']} has no requests"
            )
            assert "memory" in resources.get("limits", {}), (
                f"{pod['metadata']['name']} has no memory limit"
            )


def test_namespace_has_a_quota(kubectl):
    assert json.loads(kubectl("get", "resourcequota", "-o", "json").stdout)["items"]


def test_manifests_only_create_the_own_namespace_cluster_wide():
    api_resources = subprocess.run(
        [
            "kubectl",
            "--context",
            KUBE_CONTEXT,
            "api-resources",
            "--namespaced=false",
            "--no-headers",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    cluster_kinds = {line.split()[-1] for line in api_resources.splitlines()}
    rendered = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "k8s/overlays/ci")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    items = [item for item in yaml.safe_load_all(rendered) if item]
    cluster_wide = [
        (item["kind"], item["metadata"]["name"])
        for item in items
        if item["kind"] in cluster_kinds
    ]
    assert cluster_wide == [("Namespace", NAMESPACE)]
