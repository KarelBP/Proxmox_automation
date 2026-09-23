"""
Generic transparent proxy layer.

Forwards any request to the correct PVE node, replacing the Authorization
header with the PVE token stored in remotes.shadow.
"""

import httpx
from config import Remote


class PVEProxy:
    """
    Proxy client for a single PDM remote.
    Maintains one httpx.AsyncClient per node (keyed by node name),
    since each node has a different base URL.
    """

    def __init__(self, remote: Remote, tls_verify: bool | str):
        self.remote = remote
        self._tls_verify = tls_verify
        # Per-node clients — nodes are resolved via DNS
        self._clients: dict[str, httpx.AsyncClient] = {}

    async def _get_client(self, node: str) -> httpx.AsyncClient:
        # base_url_for_node resolves the base URL from THAT node's own
        # entry in remotes.cfg (address + port), not from the first node
        # configured for the remote — each node gets its own cached
        # client, keyed by node name, precisely because a cluster's nodes
        # can each listen on a different port.
        if node not in self._clients or self._clients[node].is_closed:
            self._clients[node] = httpx.AsyncClient(
                base_url=self.remote.base_url_for_node(node),
                headers={"Authorization": self.remote.pve_auth_header},
                verify=self._tls_verify,
                timeout=60.0,
            )
        return self._clients[node]

    async def forward(
        self,
        node: str,
        method: str,
        path: str,
        query_params: dict | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, bytes, str]:
        """
        Transparently forward a request to a PVE node.

        Returns (status_code, response_body_bytes, response_content_type).
        Body and Content-Type are passed through as-is without parsing.
        """
        client = await self._get_client(node)

        headers = {}
        if content_type:
            headers["Content-Type"] = content_type

        response = await client.request(
            method=method,
            url=path,
            params=query_params,
            content=body,
            headers=headers,
        )

        resp_ct = response.headers.get("content-type", "application/json")
        return response.status_code, response.content, resp_ct

    async def close(self):
        for client in self._clients.values():
            if not client.is_closed:
                await client.aclose()
        self._clients.clear()


class ProxyPool:
    """Pool of PVEProxy instances for all configured remotes."""

    def __init__(self):
        self._proxies: dict[str, PVEProxy] = {}

    def load(self, remotes: dict, tls_verify: bool | str = True):
        """Instantiate a PVEProxy for each remote that has a token."""
        for name, remote in remotes.items():
            if remote.token:
                self._proxies[name] = PVEProxy(remote, tls_verify)

    def get(self, remote_name: str) -> PVEProxy:
        if remote_name not in self._proxies:
            raise KeyError(f"Remote '{remote_name}' unknown or has no token")
        return self._proxies[remote_name]

    def list_remotes(self) -> list[str]:
        return list(self._proxies.keys())

    async def close_all(self):
        for proxy in self._proxies.values():
            await proxy.close()
