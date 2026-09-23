"""
Tests for config.py's remotes.cfg / remotes.shadow parsing.

The regression tests below pin down two bugs that shipped once already.
Neither of them raised an error - they quietly produced the wrong result,
which is exactly how they survived as long as they did. See the module
docstring in config.py for what each one did.
"""

import pytest

from config import (
    PVE_PORT,
    Remote,
    RemoteNode,
    _parse_nodes,
    _parse_stanzas,
    _split_address,
    load_remotes,
)

# Exact content confirmed against a live PDM instance - a two-node cluster
# writes "nodes" as two separate lines, not one space-separated line. This
# is the input that exposed the original bug.
REAL_REMOTES_CFG = """pve: clstr1
\tauthid root@pam!pdm-admin-example
\tnodes pve1:8006
\tnodes pve2:8006
\ttoken -
"""

REAL_REMOTES_SHADOW = """pve: clstr1
\ttoken 11111111-2222-3333-4444-555555555555
"""


# ---------------------------------------------------------------------------
# Bug 1: repeated "nodes" lines used to silently overwrite each other.
# ---------------------------------------------------------------------------

def test_repeated_nodes_lines_both_survive_parsing():
    """
    The regression test: with the bug, fields["nodes"] would end up as
    just "pve2:8006" (the second line silently overwriting the first).
    Both nodes must survive.
    """
    stanzas = _parse_stanzas(REAL_REMOTES_CFG)
    assert len(stanzas) == 1
    fields = stanzas[0]["fields"]

    nodes = _parse_nodes(fields["nodes"])
    node_addresses = [node.address for node in nodes]

    assert node_addresses == ["pve1:8006", "pve2:8006"]


def test_single_nodes_line_with_multiple_entries_still_works():
    """
    The original, always-worked case: multiple nodes on ONE "nodes" line,
    whitespace-separated - must keep working after the accumulate-on-repeat
    fix, not just the repeated-line case.
    """
    stanzas = _parse_stanzas(
        "pve: clstr1\n\tnodes pve1:8006 pve2:8006\n\ttoken -\n"
    )
    fields = stanzas[0]["fields"]
    nodes = _parse_nodes(fields["nodes"])
    assert [n.address for n in nodes] == ["pve1:8006", "pve2:8006"]


def test_parse_nodes_with_fingerprint():
    nodes = _parse_nodes("192.168.0.11:8006,fingerprint=7F:B6:AA pve1:8006")
    assert nodes[0].address == "192.168.0.11:8006"
    assert nodes[0].fingerprint == "7F:B6:AA"
    assert nodes[1].address == "pve1:8006"
    assert nodes[1].fingerprint == ""


def test_load_remotes_merges_cfg_and_shadow(tmp_path):
    cfg_path = tmp_path / "remotes.cfg"
    shadow_path = tmp_path / "remotes.shadow"
    cfg_path.write_text(REAL_REMOTES_CFG)
    shadow_path.write_text(REAL_REMOTES_SHADOW)

    remotes = load_remotes(cfg_path, shadow_path)

    assert set(remotes.keys()) == {"clstr1"}
    remote = remotes["clstr1"]
    assert remote.authid == "root@pam!pdm-admin-example"
    assert [n.address for n in remote.nodes] == ["pve1:8006", "pve2:8006"]
    assert remote.token == "11111111-2222-3333-4444-555555555555"


def test_load_remotes_skips_placeholder_token(tmp_path):
    """
    remotes.cfg's own "token -" line (a placeholder PDM writes when it
    doesn't have one) must never be treated as a real token - it comes
    from remotes.cfg itself, not remotes.shadow, and should never override
    a real token even if it somehow appeared there.
    """
    cfg_path = tmp_path / "remotes.cfg"
    shadow_path = tmp_path / "remotes.shadow"
    cfg_path.write_text(REAL_REMOTES_CFG)
    shadow_path.write_text("pve: clstr1\n\ttoken -\n")

    remotes = load_remotes(cfg_path, shadow_path)
    assert remotes["clstr1"].token is None


def test_load_remotes_missing_shadow_file_leaves_token_none(tmp_path):
    cfg_path = tmp_path / "remotes.cfg"
    cfg_path.write_text(REAL_REMOTES_CFG)
    missing_shadow_path = tmp_path / "does_not_exist.shadow"

    remotes = load_remotes(cfg_path, missing_shadow_path)
    assert remotes["clstr1"].token is None


def test_load_remotes_missing_cfg_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_remotes(tmp_path / "nope.cfg", tmp_path / "nope.shadow")


def test_pve_auth_header_format():
    remote = Remote(
        name="clstr1",
        authid="pdm-api-proxy@pve!proxy",
        nodes=[RemoteNode(address="pve1:8006", fingerprint="")],
        token="11111111-2222-3333-4444-555555555555",
    )
    assert remote.pve_auth_header == "PVEAPIToken=pdm-api-proxy@pve!proxy=11111111-2222-3333-4444-555555555555"


def test_pve_auth_header_raises_without_token():
    remote = Remote(name="clstr1", authid="pdm-api-proxy@pve!proxy", nodes=[], token=None)
    with pytest.raises(ValueError):
        _ = remote.pve_auth_header


# ---------------------------------------------------------------------------
# Bug 2: base_url_for_node used to take the port from nodes[0] only, and
# address splitting was not IPv6-safe.
# ---------------------------------------------------------------------------

def test_base_url_for_node_uses_that_nodes_own_port():
    """
    This pins down the fix, and deliberately asserts the OPPOSITE of the
    old behavior: the port used to come from the first configured node
    and be applied to every node in the remote. remotes.cfg carries an
    address per node, so a cluster with one node behind a different port
    had no way to say so before this fix.
    """
    remote = Remote(
        name="clstr1",
        authid="pdm-api-proxy@pve!proxy",
        nodes=[RemoteNode(address="pve1:8006", fingerprint=""), RemoteNode(address="pve2:9999", fingerprint="")],
        token="secret",
    )
    assert remote.base_url_for_node("pve1") == "https://pve1:8006/api2/json"
    assert remote.base_url_for_node("pve2") == "https://pve2:9999/api2/json"


def test_base_url_for_node_falls_back_to_default_port_without_colon():
    remote = Remote(
        name="clstr1",
        authid="pdm-api-proxy@pve!proxy",
        nodes=[RemoteNode(address="pve1", fingerprint="")],
        token="secret",
    )
    assert remote.base_url_for_node("pve1") == "https://pve1:8006/api2/json"


def test_base_url_for_an_unconfigured_node_borrows_the_first_nodes_port():
    """
    The node name here comes from the CALLER's request path, not from
    remotes.cfg, so a miss is entirely reachable - notably when
    remotes.cfg lists addresses as IPs and the caller addresses nodes by
    name, where every single lookup misses.

    Falling back to a hardcoded 8006 would throw away a cluster's
    non-default port that the pre-fix first-node-port behavior got right.
    The first node's port is the fallback instead, so the fix is a strict
    improvement over the old behavior rather than a trade.
    """
    remote = Remote(
        name="clstr1",
        authid="pdm-api-proxy@pve!proxy",
        nodes=[RemoteNode(address="192.168.0.11:9999", fingerprint="")],
        token="secret",
    )
    assert remote.base_url_for_node("pve9") == "https://pve9:9999/api2/json"


def test_base_url_for_an_unconfigured_node_uses_the_default_without_any_nodes():
    remote = Remote(name="clstr1", authid="pdm-api-proxy@pve!proxy", nodes=[], token="secret")
    assert remote.base_url_for_node("pve9") == "https://pve9:8006/api2/json"


def test_node_by_name_finds_the_configured_node():
    pve1 = RemoteNode(address="pve1:8006", fingerprint="")
    pve2 = RemoteNode(address="pve2:9999", fingerprint="")
    remote = Remote(name="clstr1", authid="a", nodes=[pve1, pve2], token="secret")

    assert remote.node_by_name("pve2") is pve2
    assert remote.node_by_name("pve9") is None


# ---------------------------------------------------------------------------
# Address parsing - the IPv6 case is what made the per-node fix concrete
# rather than theoretical.
# ---------------------------------------------------------------------------

def test_hostname_and_port_are_split_per_node():
    node = RemoteNode(address="192.168.0.11:8006", fingerprint="")
    assert node.hostname == "192.168.0.11"
    assert node.port == 8006


def test_an_address_without_a_port_gets_the_default():
    node = RemoteNode(address="pve1", fingerprint="")
    assert node.hostname == "pve1"
    assert node.port == PVE_PORT


def test_a_bracketed_ipv6_literal_keeps_its_port():
    """
    The concrete failure: address.split(":")[0] on "[fe80::1]:8006" yields
    "[fe80", which is neither a hostname nor an error - it is a string
    that goes on to be used as one.
    """
    node = RemoteNode(address="[fe80::1]:8006", fingerprint="")
    assert node.hostname == "fe80::1"
    assert node.port == 8006


def test_a_bare_ipv6_literal_is_not_mistaken_for_a_host_and_port():
    # Unbracketed, so there is no port to find: the trailing ":1" is part
    # of the address, not a port number.
    node = RemoteNode(address="fe80::1", fingerprint="")
    assert node.hostname == "fe80::1"
    assert node.port == PVE_PORT


def test_an_ipv6_hostname_is_bracketed_for_the_url():
    # https://fe80::1:8006/... is unparseable; the brackets are not
    # cosmetic.
    node = RemoteNode(address="[fe80::1]:8006", fingerprint="")
    assert node.url_host == "[fe80::1]"

    remote = Remote(name="clstr1", authid="a", nodes=[node], token="secret")
    assert remote.base_url_for_node("fe80::1") == "https://[fe80::1]:8006/api2/json"


def test_an_unparseable_port_falls_back_instead_of_raising():
    # A malformed address should cost that node, not the whole config load.
    node = RemoteNode(address="pve1:not-a-port", fingerprint="")
    assert node.port == PVE_PORT


def test_split_address_unterminated_bracket_falls_back():
    hostname, port = _split_address("[fe80::1")
    assert hostname == "[fe80::1"
    assert port == PVE_PORT
