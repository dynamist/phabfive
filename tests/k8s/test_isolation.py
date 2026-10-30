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


def probe_from(kubectl, namespace, host, port, labels=None):
    """Try a TCP connection from a throwaway pod, return True when it connects.

    The pod runs to completion before its log is read: `kubectl run -i` loses
    the output when the container exits before kubectl attaches. `labels` are
    set on the pod, for policies that select pods as well as namespaces.
    """
    name = f"probe-{uuid.uuid4().hex[:8]}"
    command = f"nc -z -w 5 {host} {port} && echo OPEN || echo CLOSED"
    kubectl(
        "run",
        name,
        "--restart=Never",
        "--image=busybox:1.37",
        *(f"--labels={k}={v}" for k, v in (labels or {}).items()),
        "--",
        "sh",
        "-c",
        command,
        namespace=namespace,
    )
    try:
        kubectl(
            "wait",
            f"pod/{name}",
            "--for=jsonpath={.status.phase}=Succeeded",
            "--timeout=180s",
            namespace=namespace,
            timeout=200,
        )
        output = kubectl("logs", name, namespace=namespace).stdout
    finally:
        kubectl("delete", "pod", name, "--wait=false", namespace=namespace, check=False)
    assert output.strip() in ("OPEN", "CLOSED"), output
    return output.strip() == "OPEN"


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
        # Conduit admits only named pods, see test_conduit_clients_* below
        ("web.phorge.svc.cluster.local", 80),
        # git over SSH is reached through port-forward only, which enters the
        # pod's own network namespace and needs no NetworkPolicy
        ("ssh.phorge.svc.cluster.local", 22),
        ("mariadb.phorge.svc.cluster.local", 3306),
    ],
)
def test_other_namespaces_cannot_reach_phorge(kubectl, host, port):
    for namespace in other_namespaces(kubectl):
        reachable = probe_from(kubectl, namespace, host, port)
        assert not reachable, f"{host}:{port} is reachable from {namespace}"


# The pods k8s/base/networkpolicy.yaml admits to Phorge's Conduit port, by
# `app.kubernetes.io/name`, in any namespace of the app named here
CONDUIT_APP = "dynatron"
CONDUIT_CLIENTS = ["dynatron", "worker"]


@pytest.fixture(scope="module")
def client_namespace(kubectl):
    """A scratch namespace labelled as one of the Conduit app's instances.

    The policy's own test, so it needs no Dynatron deployed beside Phorge, and
    it is not labelled `dynamist.se/dev-app`, so nothing takes it for a dev app.
    """
    name = f"phorge-probe-{uuid.uuid4().hex[:8]}"
    kubectl("create", "namespace", name)
    try:
        kubectl("label", "namespace", name, f"app.kubernetes.io/part-of={CONDUIT_APP}")
        yield name
    finally:
        kubectl("delete", "namespace", name, "--wait=false", check=False)


@pytest.mark.parametrize("client", CONDUIT_CLIENTS)
def test_conduit_clients_reach_phorge(kubectl, client_namespace, client):
    assert probe_from(
        kubectl,
        client_namespace,
        "web.phorge.svc.cluster.local",
        80,
        labels={"app.kubernetes.io/name": client},
    )


def test_conduit_clients_namespace_admits_no_other_pod(kubectl, client_namespace):
    """Control for the test above: the namespace label alone admits nothing."""
    assert not probe_from(
        kubectl,
        client_namespace,
        "web.phorge.svc.cluster.local",
        80,
        labels={"app.kubernetes.io/name": "postgres"},
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
