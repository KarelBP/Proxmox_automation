"""
PDM API Proxy

Transparent proxy for the PVE REST API using PDM remote credentials.

URL scheme:
  Incoming:  /api2/json/pve/remotes/{remote}/{path}
  Forwarded: https://{node}:{port}/api2/json/{path}

  Example:
    GET /api2/json/pve/remotes/mycluster/nodes/pve1/qemu/100/config
    ->  GET https://pve1:8006/api2/json/nodes/pve1/qemu/100/config

Proxy authentication:
  Authorization: PDMAPIToken=<uuid>   (token from /etc/pdm-api-proxy/tokens)

  Token format is a plain UUID4, matching the Proxmox API token convention.

PVE authentication:
  Handled internally by the proxy using the token from remotes.shadow.
  The caller never sees or supplies PVE credentials.

HTTPS:
  The proxy listens on HTTPS using the PDM TLS certificate by default
  (/etc/proxmox-datacenter-manager/proxy.pem).  Override via [tls_server]
  in /etc/pdm-api-proxy/config.cfg.
"""

import uuid
import logging
import re
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.responses import Response
from fastapi.security import APIKeyHeader

from config import ProxyConfig, Remote, load_remotes, load_tokens
from proxy import ProxyPool


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

proxy_cfg: ProxyConfig = ProxyConfig()
valid_tokens: set[str] = set()
pool = ProxyPool()

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


def verify_token(authorization: str | None = Security(api_key_header)) -> str:
    """
    Validate the Authorization header.
    Expected format: PDMAPIToken=<uuid>
    Plain UUID value (no prefix) is also accepted as a fallback.
    """
    token = None
    if authorization:
        if authorization.startswith("PDMAPIToken="):
            token = authorization.removeprefix("PDMAPIToken=")
        else:
            token = authorization

    if not token or token not in valid_tokens:
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return token


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global proxy_cfg, valid_tokens

    log.info("Starting PDM API Proxy...")

    proxy_cfg = ProxyConfig.load()
    log.info(f"TLS verify (upstream): {proxy_cfg.tls_verify}")

    if proxy_cfg.tls_server:
        proxy_cfg.tls_server.validate()
        log.info(f"TLS server cert: {proxy_cfg.tls_server.certfile}")

    valid_tokens = load_tokens()
    if not valid_tokens:
        # No token file found — generate a temporary UUID token and log it.
        # Format matches Proxmox API token convention.
        tmp = str(uuid.uuid4())
        valid_tokens.add(tmp)
        log.warning(f"No token file found — temporary dev token: PDMAPIToken={tmp}")
    else:
        log.info(f"Loaded {len(valid_tokens)} token(s)")

    # KNOWN LIMITATION: remotes.cfg/remotes.shadow are read exactly once,
    # here, and PVEProxy bakes each remote's token into the Authorization
    # header of a cached httpx client when that client is first created.
    # Nothing re-reads either file afterwards, so:
    #   - a token rotated in remotes.shadow is never picked up; every
    #     forwarded call 401s until this service is restarted, which makes
    #     a routine credential rotation look like a full-cluster outage
    #   - a remote newly enrolled in PDM stays a 404 until restart
    # Fixing this means re-reading both files periodically AND discarding
    # the cached client of any remote whose token changed — the header
    # cannot be swapped on a client that already exists.
    remotes = load_remotes()
    pool.load(remotes, tls_verify=proxy_cfg.tls_verify)
    log.info(f"Loaded remotes: {pool.list_remotes()}")

    yield

    log.info("Shutting down...")
    await pool.close_all()


app = FastAPI(
    title="PDM API Proxy",
    description=(
        "Transparent proxy for the PVE REST API.\n\n"
        "URL scheme: `/api2/json/pve/remotes/{remote}/{path}` → `https://{node}:PORT/api2/json/{path}`\n\n"
        "Authorization: `PDMAPIToken=<uuid>`"
    ),
    version="0.3.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Node resolution
# ---------------------------------------------------------------------------

def resolve_node(path: str, remote: Remote) -> str:
    """
    Work out which PVE node a request path targets.

    - "nodes/{node}/..." paths name the node explicitly.
    - Cluster-level paths (e.g. "cluster/nextid") carry no node, so fall
      back to the hostname of the first node configured for this remote.

    Raises ValueError when the remote has no nodes configured at all, so
    the caller can turn that into the right HTTP error.
    """
    node_match = re.match(r"^nodes/([^/]+)(/.*)?$", path)
    if node_match:
        return node_match.group(1)

    remote_nodes = remote.nodes
    if not remote_nodes:
        raise ValueError(f"Remote '{remote.name}' has no nodes configured")

    # Use RemoteNode.hostname rather than address.split(":")[0]: a bare
    # ":" split breaks on a bracketed IPv6 literal such as
    # "[fe80::1]:8006", which yields "[fe80" instead of "fe80::1".
    return remote_nodes[0].hostname


# ---------------------------------------------------------------------------
# Single wildcard endpoint
# ---------------------------------------------------------------------------

@app.api_route(
    "/api2/json/pve/remotes/{remote}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def generic_proxy(
    remote: str,
    path: str,
    request: Request,
    _: str = Security(verify_token),
):
    """
    Transparent forward to the PVE API.

    - Strips /pve/remotes/{remote} from the URL
    - Forwards the remaining path to the PVE node (resolved via DNS)
    - Query string forwarded as-is
    - Request body forwarded as-is
    - Swaps Authorization header for the PVE token from remotes.shadow
    - Returns PVE response as-is
    """
    try:
        pve_proxy = pool.get(remote)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Remote '{remote}' not found")

    # Extract node name from path — expected prefix: nodes/{node}/...
    # For cluster-level paths (e.g. cluster/nextid) there is no node in the path;
    # fall back to the hostname of the first node listed in remotes.cfg.
    try:
        node = resolve_node(path, pve_proxy.remote)
    except ValueError:
        raise HTTPException(status_code=502, detail=f"Remote '{remote}' has no nodes configured")

    query_params = dict(request.query_params)
    body = await request.body()
    content_type = request.headers.get("content-type")

    # base_url in the httpx client is already https://{node}:{port}/api2/json
    pve_path = f"/{path}"

    log.info(f"{request.method} remote={remote} node={node} path={pve_path} params={query_params or ''}")

    status, content, resp_ct = await pve_proxy.forward(
        node=node,
        method=request.method,
        path=pve_path,
        query_params=query_params or None,
        body=body or None,
        content_type=content_type,
    )

    return Response(
        content=content,
        status_code=status,
        media_type=resp_ct,
    )


# ---------------------------------------------------------------------------
# Health check — no auth required
# ---------------------------------------------------------------------------

@app.get("/healthz", include_in_schema=False)
async def health():
    return {"status": "ok", "remotes": pool.list_remotes()}


# ---------------------------------------------------------------------------
# Entrypoint — used when running directly: python main.py
# For production use the systemd service which calls uvicorn directly.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = ProxyConfig.load()
    tls = cfg.tls_server

    uvicorn.run(
        "main:app",
        host=cfg.listen_host,
        port=cfg.listen_port,
        ssl_certfile=str(tls.certfile) if tls else None,
        ssl_keyfile=str(tls.keyfile) if tls else None,
        log_level="info",
    )
