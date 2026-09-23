"""
Tests for main.py's node-resolution logic (resolve_node).

resolve_node used to be inlined in the generic_proxy() request handler and
fell back to `first_addr.split(":")[0]` for cluster-level paths, which is
not IPv6-safe (see config.py's module docstring). It is pulled out here as
a plain function so the fallback can be tested without spinning up the
FastAPI app.
"""

import pytest

from config import Remote, RemoteNode
from main import resolve_node


def test_resolve_node_reads_node_from_path():
    remote = Remote(name="clstr1", authid="a", nodes=[RemoteNode(address="pve1:8006", fingerprint="")])
    node = resolve_node("nodes/pve1/qemu/100/config", remote)
    assert node == "pve1"


def test_resolve_node_falls_back_to_first_node_for_cluster_level_paths():
    remote = Remote(
        name="clstr1",
        authid="a",
        nodes=[RemoteNode(address="pve1:8006", fingerprint=""), RemoteNode(address="pve2:8006", fingerprint="")],
    )
    node = resolve_node("cluster/nextid", remote)
    assert node == "pve1"


def test_resolve_node_fallback_is_ipv6_safe():
    """
    The regression test: the old `first_addr.split(":")[0]` fallback
    turned "[fe80::1]:8006" into the hostname "[fe80", which is not a
    valid PVE node name and would never resolve. resolve_node must use
    RemoteNode.hostname instead, which is bracket-aware.
    """
    remote = Remote(name="clstr1", authid="a", nodes=[RemoteNode(address="[fe80::1]:8006", fingerprint="")])
    node = resolve_node("cluster/nextid", remote)
    assert node == "fe80::1"


def test_resolve_node_raises_when_remote_has_no_nodes():
    remote = Remote(name="clstr1", authid="a", nodes=[])
    with pytest.raises(ValueError):
        resolve_node("cluster/nextid", remote)
