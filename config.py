"""
Parser for PDM remote config files and proxy-own configuration.

Reads:
  /etc/proxmox-datacenter-manager/remotes.cfg
  /etc/proxmox-datacenter-manager/remotes.shadow
  /etc/pdm-api-proxy/config.cfg
  /etc/pdm-api-proxy/tokens
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

@dataclass
class RemoteNode:
    address: str        # e.g. 192.168.0.11:8006 or hostname:8006
    fingerprint: str    # e.g. 7F:B6:... (informational, not used for TLS)


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

    def base_url_for_node(self, node: str) -> str:
        """
        Build base URL for a specific node resolved by DNS.
        Port is taken from the first address in remotes.cfg, defaulting to 8006.
        """
        port = self._port_from_nodes()
        return f"https://{node}:{port}/api2/json"

    def _port_from_nodes(self) -> int:
        """Extract port from first known node address, fallback to 8006."""
        if self.nodes:
            addr = self.nodes[0].address
            if ":" in addr:
                try:
                    return int(addr.rsplit(":", 1)[1])
                except ValueError:
                    pass
        return PVE_PORT


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
        if not line.strip() or line.strip().startswith("#"):
            continue

        header = re.match(r'^(\w+):\s+(.+)$', line)
        if header:
            if current is not None:
                stanzas.append(current)
            current = {"type": header.group(1), "name": header.group(2).strip(), "fields": {}}
            continue

        field_match = re.match(r'^\s+(\S+)\s+(.*)', line)
        if field_match and current is not None:
            current["fields"][field_match.group(1)] = field_match.group(2).strip()

    if current is not None:
        stanzas.append(current)

    return stanzas


def _parse_nodes(nodes_str: str) -> list[RemoteNode]:
    """
    Parse the nodes field value:
      '192.168.0.11:8006,fingerprint=7F:B6:...'
    Multiple nodes are separated by whitespace.
    """
    result = []
    for entry in nodes_str.split():
        parts = entry.split(",fingerprint=")
        address = parts[0].strip()
        fingerprint = parts[1].strip() if len(parts) > 1 else ""
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
    for stanza in _parse_stanzas(cfg_text):
        if stanza["type"] != "pve":
            continue
        name = stanza["name"]
        fields = stanza["fields"]
        authid = fields.get("authid", "")
        nodes_str = fields.get("nodes", "")
        nodes = _parse_nodes(nodes_str) if nodes_str else []
        remotes[name] = Remote(name=name, authid=authid, nodes=nodes)

    if shadow_path.exists():
        shadow_text = shadow_path.read_text()
        for stanza in _parse_stanzas(shadow_text):
            if stanza["type"] != "pve":
                continue
            name = stanza["name"]
            token = stanza["fields"].get("token")
            # Skip placeholder tokens ("-") written by PDM when shadow is unavailable
            if token and token != "-" and name in remotes:
                remotes[name].token = token

    return remotes
