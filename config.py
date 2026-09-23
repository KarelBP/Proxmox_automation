"""
Parser for PDM remote config files and proxy-own configuration.

Reads:
  /etc/proxmox-datacenter-manager/remotes.cfg
  /etc/proxmox-datacenter-manager/remotes.shadow
  /etc/pdm-api-proxy/config.cfg
  /etc/pdm-api-proxy/tokens

The remotes.cfg / remotes.shadow format is owned by Proxmox Datacenter
Manager itself, which writes both files. This parser follows what PDM
actually produces rather than what the format looks like at first glance,
because two things here were wrong until they were fixed:

  1. _parse_stanzas used to store repeated field keys (e.g. two separate
     "nodes" lines for a two-node cluster — PDM writes one line per value,
     not one space-separated line) in a plain dict, so the second line
     silently overwrote the first and one node disappeared with no error.
     Repeated keys are now accumulated instead of overwritten.
  2. base_url_for_node used to take the port from the FIRST configured
     node only and apply it to every node in the remote. That discarded
     per-node information remotes.cfg explicitly carries, and address
     splitting via a bare ":" split was not IPv6-safe (it turned
     "[fe80::1]:8006" into hostname "[fe80"). Each node now parses its
     own hostname/port via _split_address, bracket-aware.

One consequence is easy to undo by accident: base_url_for_node falls back
to the FIRST configured node's port for a node name that is not listed in
remotes.cfg, not to the hard default port. See base_url_for_node for why
that distinction matters before changing it.
"""

import re
import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


REMOTES_CFG    = Path("/etc/proxmox-datacenter-manager/remotes.cfg")
REMOTES_SHADOW = Path("/etc/proxmox-datacenter-manager/remotes.shadow")
PROXY_CFG      = Path("/etc/pdm-api-proxy/config.cfg")
PROXY_TOKENS   = Path("/etc/pdm-api-proxy/tokens")

PDM_TLS_CERT = Path("/etc/proxmox-datacenter-manager/auth/api.pem")
PDM_TLS_KEY  = Path("/etc/proxmox-datacenter-manager/auth/api.key")

PVE_PORT = 8006


# ---------------------------------------------------------------------------
# Proxy configuration
# ---------------------------------------------------------------------------

@dataclass
class TLSServerConfig:
    """TLS configuration for the proxy's own HTTPS listener."""
    certfile: Path
    keyfile: Path

    def validate(self):
        if not self.certfile.exists():
            raise FileNotFoundError(f"Server TLS cert not found: {self.certfile}")
        if not self.keyfile.exists():
            raise FileNotFoundError(f"Server TLS key not found: {self.keyfile}")


@dataclass
class ProxyConfig:
    """Runtime configuration for the proxy itself."""
    tls_verify: bool | str = True   # True, False, or path to CA bundle
    listen_host: str = "127.0.0.1"
    listen_port: int = 9006
    tls_server: Optional[TLSServerConfig] = None

    @classmethod
    def load(cls, path: Path = PROXY_CFG) -> "ProxyConfig":
        """
        Load /etc/pdm-api-proxy/config.cfg

        Format:
            [proxy]
            listen_host = 0.0.0.0
            listen_port = 9006

            [tls]
            verify = true           # true / false / /path/to/ca-bundle.pem

            [tls_server]
            # Optional — if omitted the PDM TLS files are used:
            #   cert: /etc/proxmox-datacenter-manager/auth/api.pem
            #   key:  /etc/proxmox-datacenter-manager/auth/api.key
            certfile = /etc/proxmox-datacenter-manager/auth/api.pem
            keyfile  = /etc/proxmox-datacenter-manager/auth/api.key
        """
        cfg = configparser.ConfigParser()

        if path.exists():
            cfg.read(path)

        # ---- upstream TLS verification ----
        tls_raw = cfg.get("tls", "verify", fallback="true").strip()
        if tls_raw.lower() == "true":
            tls_verify: bool | str = True
        elif tls_raw.lower() == "false":
            tls_verify = False
        else:
            ca_path = Path(tls_raw)
            if not ca_path.exists():
                raise FileNotFoundError(f"CA bundle not found: {ca_path}")
            tls_verify = str(ca_path)

        # ---- server-side TLS (our HTTPS listener) ----
        # Defaults to the PDM TLS certificate (separate cert + key files).
        certfile = Path(cfg.get("tls_server", "certfile", fallback=str(PDM_TLS_CERT)))
        keyfile  = Path(cfg.get("tls_server", "keyfile",  fallback=str(PDM_TLS_KEY)))
        tls_server = TLSServerConfig(certfile=certfile, keyfile=keyfile)

        return cls(
            tls_verify=tls_verify,
            listen_host=cfg.get("proxy", "listen_host", fallback="127.0.0.1"),
            listen_port=int(cfg.get("proxy", "listen_port", fallback="9006")),
            tls_server=tls_server,
        )


def load_tokens(path: Path = PROXY_TOKENS) -> set[str]:
    """
    Load allowed proxy tokens from file (one token per line, # = comment).
    Returns an empty set if the file does not exist — startup will generate a
    temporary UUID token and log it.
    """
    tokens: set[str] = set()
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                tokens.add(line)
    return tokens


# ---------------------------------------------------------------------------
# PDM remotes
# ---------------------------------------------------------------------------

def _split_address(address: str) -> tuple[str, int]:
    """
    Split one remotes.cfg node address into (hostname, port).

    A plain rsplit(":") cannot tell a colon-separated port apart from an
    IPv6 literal, so this handles each form PDM can write explicitly:

        pve1                 -> ("pve1", 8006)          no port given
        pve1:8006            -> ("pve1", 8006)
        192.168.0.11:8006    -> ("192.168.0.11", 8006)
        [fe80::1]:8006       -> ("fe80::1", 8006)       bracketed literal
        fe80::1              -> ("fe80::1", 8006)       bare literal

    The returned hostname has no brackets, since that is what has to match
    a PVE node name. base_url_for_node puts the brackets back for the URL
    (see RemoteNode.url_host).

    A bare IPv6 literal is recognised by having more than one colon and no
    brackets — an unbracketed literal cannot carry a port unambiguously,
    so there is no port to look for. An unparseable port falls back to the
    default instead of raising: a malformed address should cost that one
    node, not the whole config load.
    """
    stripped_address = address.strip()

    if stripped_address.startswith("["):
        closing_bracket_index = stripped_address.find("]")
        if closing_bracket_index == -1:
            # Unterminated bracket — nothing sensible to split on.
            return stripped_address, PVE_PORT

        hostname = stripped_address[1:closing_bracket_index]
        remainder = stripped_address[closing_bracket_index + 1:]

        if not remainder.startswith(":"):
            return hostname, PVE_PORT

        port_str = remainder[1:]
        try:
            port = int(port_str)
        except ValueError:
            return hostname, PVE_PORT
        return hostname, port

    colon_count = stripped_address.count(":")

    if colon_count == 0:
        return stripped_address, PVE_PORT

    if colon_count > 1:
        # More than one colon with no brackets — bare IPv6 literal, no port.
        return stripped_address, PVE_PORT

    address_parts = stripped_address.rsplit(":", 1)
    hostname = address_parts[0]
    port_str = address_parts[1]

    try:
        port = int(port_str)
    except ValueError:
        return stripped_address, PVE_PORT

    return hostname, port


@dataclass
class RemoteNode:
    address: str        # e.g. 192.168.0.11:8006, hostname:8006, [fe80::1]:8006
    fingerprint: str    # e.g. 7F:B6:... (informational, not used for TLS)

    @property
    def hostname(self) -> str:
        """This node's hostname with the port removed — what PVE calls it."""
        hostname, _ = _split_address(self.address)
        return hostname

    @property
    def port(self) -> int:
        """
        This node's own port, defaulting to 8006 when the address carries
        none. Deliberately per-node: taking the port from the first
        configured node and applying it to every node (the old behavior)
        discarded information remotes.cfg explicitly provides.
        """
        _, port = _split_address(self.address)
        return port

    @property
    def url_host(self) -> str:
        """
        The hostname as it must appear in a URL: an IPv6 literal gets its
        brackets back, everything else is unchanged. Without this,
        https://fe80::1:8006/... would be unparseable.
        """
        hostname = self.hostname
        if ":" in hostname:
            return f"[{hostname}]"
        return hostname


@dataclass
class Remote:
    name: str
    authid: str                         # e.g. root@pam!pdm-admin-pdman
    nodes: list[RemoteNode] = field(default_factory=list)
    token: Optional[str] = None         # UUID secret from remotes.shadow

    @property
    def pve_auth_header(self) -> str:
        """Build the PVEAPIToken Authorization header value for the PVE REST API."""
        if not self.token:
            raise ValueError(f"Remote '{self.name}' has no token (shadow not readable?)")
        return f"PVEAPIToken={self.authid}={self.token}"

    def node_by_name(self, node: str) -> Optional[RemoteNode]:
        """The configured RemoteNode whose hostname is `node`, if any."""
        for remote_node in self.nodes:
            if remote_node.hostname == node:
                return remote_node
        return None

    def base_url_for_node(self, node: str) -> str:
        """
        Build the API base URL for one node, using THAT node's own address
        and port as configured in remotes.cfg — not the first node's port
        applied to every node (see the module docstring).

        A node name that is NOT in remotes.cfg keeps its name as the host
        (DNS resolves it, which is what this proxy has always relied on)
        and borrows the first configured node's port.

        That fallback carries more weight than it looks. The node name
        comes from the CALLER's request path
        (/api2/json/pve/remotes/{remote}/nodes/{node}/...), while
        remotes.cfg may well list its addresses as IPs — in which case
        every lookup misses and the fallback is what actually runs.
        Falling back to the hard default PVE_PORT would silently ignore a
        cluster's non-default port that the old first-node-port behavior
        got right. Borrowing the first node's port keeps that case working
        exactly as before, which makes the per-node fix above a strict
        improvement over the old behavior rather than a trade.
        """
        remote_node = self.node_by_name(node)

        if remote_node is not None:
            return f"https://{remote_node.url_host}:{remote_node.port}/api2/json"

        fallback_port = self._fallback_port()
        return f"https://{node}:{fallback_port}/api2/json"

    def _fallback_port(self) -> int:
        """
        The port to use for a node that is not listed in remotes.cfg: the
        first configured node's port, or the PVE default when this remote
        has no nodes at all.
        """
        if not self.nodes:
            return PVE_PORT

        first_node = self.nodes[0]
        return first_node.port


def _parse_stanzas(text: str) -> list[dict]:
    """
    Parse Proxmox stanza format:

        pve: remote_name
                key value
                key value

    Returns a list of dicts with keys 'type', 'name', 'fields'.
    """
    stanzas = []
    current = None

    for line in text.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue
        if stripped_line.startswith("#"):
            continue

        header_match = re.match(r'^(\w+):\s+(.+)$', line)
        if header_match:
            if current is not None:
                stanzas.append(current)
            stanza_type = header_match.group(1)
            stanza_name = header_match.group(2).strip()
            current = {"type": stanza_type, "name": stanza_name, "fields": {}}
            continue

        field_match = re.match(r'^\s+(\S+)\s+(.*)', line)
        if field_match and current is not None:
            field_key = field_match.group(1)
            field_value = field_match.group(2).strip()

            existing_value = current["fields"].get(field_key)
            if existing_value is None:
                current["fields"][field_key] = field_value
            else:
                # PDM repeats a key on its own line for each value instead
                # of putting all values on one line (confirmed: a two-node
                # cluster's remotes.cfg has TWO separate "nodes" lines, not
                # one "nodes host1:port host2:port" line). A plain dict
                # assignment here would silently overwrite the first node
                # with the second — accumulate with a space instead, since
                # _parse_nodes() below already splits multi-node values on
                # whitespace regardless of whether they came from one line
                # or several.
                current["fields"][field_key] = f"{existing_value} {field_value}"

    if current is not None:
        stanzas.append(current)

    return stanzas


def _parse_nodes(nodes_str: str) -> list[RemoteNode]:
    """
    Parse the nodes field value:
      '192.168.0.11:8006,fingerprint=7F:B6:...'
    Multiple nodes are separated by whitespace. `nodes_str` is already the
    accumulated value from every "nodes" line in the stanza (see the
    repeated-key handling in _parse_stanzas), so a multi-node cluster
    written as several lines and one written on a single line both land
    here as one whitespace-separated string.
    """
    result = []
    entries = nodes_str.split()

    for entry in entries:
        parts = entry.split(",fingerprint=")
        address = parts[0].strip()

        fingerprint = ""
        if len(parts) > 1:
            fingerprint = parts[1].strip()

        result.append(RemoteNode(address=address, fingerprint=fingerprint))

    return result


def load_remotes(
    cfg_path: Path = REMOTES_CFG,
    shadow_path: Path = REMOTES_SHADOW,
) -> dict[str, Remote]:
    """
    Load and merge remotes.cfg + remotes.shadow.
    Returns dict {remote_name: Remote}.
    """
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")

    remotes: dict[str, Remote] = {}

    cfg_text = cfg_path.read_text()
    cfg_stanzas = _parse_stanzas(cfg_text)

    for stanza in cfg_stanzas:
        if stanza["type"] != "pve":
            continue

        name = stanza["name"]
        fields = stanza["fields"]
        authid = fields.get("authid", "")
        nodes_str = fields.get("nodes", "")

        nodes = []
        if nodes_str:
            nodes = _parse_nodes(nodes_str)

        remotes[name] = Remote(name=name, authid=authid, nodes=nodes)

    if shadow_path.exists():
        shadow_text = shadow_path.read_text()
        shadow_stanzas = _parse_stanzas(shadow_text)

        for stanza in shadow_stanzas:
            if stanza["type"] != "pve":
                continue

            name = stanza["name"]
            token = stanza["fields"].get("token")

            # Skip placeholder tokens ("-") written by PDM when shadow is unavailable.
            if not token:
                continue
            if token == "-":
                continue
            if name not in remotes:
                continue

            remotes[name].token = token

    return remotes
