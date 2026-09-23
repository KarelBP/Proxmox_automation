# PDM API Proxy — Install Guide

## Prerequisites

Run all commands as `root` on the PDM host.
The source files (`config.py`, `proxy.py`, `main.py`, `config.cfg.example`,
`pdm-api-proxy.service`) must be present in the current working directory.

---

## Directory layout

```
/opt/pdm-api-proxy/      application source files
/etc/pdm-api-proxy/      configuration and tokens
```

---

## Installation

### 1. Install dependencies

```bash
apt install -y python3-fastapi python3-httpx uvicorn
```

### 2. Create the service account

The proxy runs as an unprivileged system user.
Membership in `www-data` grants read access to the PDM remote credentials
(`remotes.cfg`, `remotes.shadow`) and the PDM TLS certificate under
`/etc/proxmox-datacenter-manager/`.

```bash
useradd --system --no-create-home --shell /usr/sbin/nologin pdm-api-proxy
usermod -aG www-data pdm-api-proxy
```

### 3. Create directories

```bash
mkdir -p /opt/pdm-api-proxy
mkdir -p /etc/pdm-api-proxy
```

### 4. Copy source files

```bash
cp config.py proxy.py main.py /opt/pdm-api-proxy/
chown -R pdm-api-proxy:pdm-api-proxy /opt/pdm-api-proxy/
```

### 5. Copy and edit configuration

```bash
cp config.cfg.example /etc/pdm-api-proxy/config.cfg
```

Edit `/etc/pdm-api-proxy/config.cfg` if you need to change the listen
address/port or TLS settings. The defaults work on a standard PDM installation.

### 6. Create the token file

Tokens are plain UUID4 values (one per line). The `#` character starts a comment.

```bash
uuidgen > /etc/pdm-api-proxy/tokens
chmod 600 /etc/pdm-api-proxy/tokens
chown -R pdm-api-proxy:pdm-api-proxy /etc/pdm-api-proxy/
```

Record the generated token — you will need it for the `Authorization` header:

```bash
cat /etc/pdm-api-proxy/tokens
```

### 7. Install and enable the systemd service

```bash
cp pdm-api-proxy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pdm-api-proxy
```

### 8. Verify

```bash
systemctl status pdm-api-proxy
curl -k https://localhost:9006/healthz
```

Expected response:

```json
{"status": "ok", "remotes": ["<remote-name>", "..."]}
```

---

## Upgrading an existing installation

Only `config.py`, `proxy.py` and `main.py` change between versions. The
configuration, the token file and the systemd unit are untouched, so there is
no need to reinstall dependencies or run `systemctl daemon-reload`.

### 1. Back up the current source files

```bash
BACKUP=/root/pdm-api-proxy-backup-$(date +%F)
mkdir -p "$BACKUP"
cp -a /opt/pdm-api-proxy/*.py "$BACKUP"/
```

### 2. Replace the source files and restart

The new files must be in the current working directory, as in step 4 above.

```bash
cp config.py proxy.py main.py /opt/pdm-api-proxy/
chown pdm-api-proxy:pdm-api-proxy /opt/pdm-api-proxy/*.py
systemctl restart pdm-api-proxy
systemctl status pdm-api-proxy --no-pager
```

### 3. Verify

`healthz` confirms only that the service started and which remotes it loaded:

```bash
curl -k https://localhost:9006/healthz
```

To confirm that remotes.cfg parsed as expected — every node present, each on
its own port — print what the proxy actually resolved:

```bash
cd /opt/pdm-api-proxy
python3 - <<'EOF'
import config
for name, remote in config.load_remotes().items():
    print(f"{name}: token={'yes' if remote.token else 'NO'}")
    for node in remote.nodes:
        print(f"   {node.address:28} -> {remote.base_url_for_node(node.hostname)}")
EOF
```

Every node of a multi-node cluster must be listed. A node missing here means
its `nodes` line in remotes.cfg was not parsed, and requests for that node
will fall back to the first configured node's port.

Finally confirm the whole path — proxy token, credential swap, PVE response:

```bash
TOKEN=$(head -1 /etc/pdm-api-proxy/tokens)
curl -sk -H "Authorization: PDMAPIToken=$TOKEN" \
  "https://localhost:9006/api2/json/pve/remotes/<remote>/nodes/<node>/status"
```

### 4. Roll back

```bash
cp /root/pdm-api-proxy-backup-<date>/*.py /opt/pdm-api-proxy/
chown pdm-api-proxy:pdm-api-proxy /opt/pdm-api-proxy/*.py
systemctl restart pdm-api-proxy
```

---

## Changes that require a restart

Both credential sources are read once, at startup, and the PVE token is baked
into a cached HTTP client when that client is first used. Nothing re-reads
either file afterwards:

| Changed file | Effect until the proxy is restarted |
|---|---|
| `/etc/proxmox-datacenter-manager/remotes.shadow` | A rotated PVE token is never picked up — every forwarded call answers 401. A remote newly enrolled in PDM answers 404. |
| `/etc/pdm-api-proxy/tokens` | An added or removed proxy token has no effect. |

```bash
systemctl restart pdm-api-proxy
```

A routine credential rotation will otherwise look like a full-cluster outage.

---

## Token format

Authorization header format for all proxy requests:

```
Authorization: PDMAPIToken=xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
```

---

## HTTPS / TLS

The proxy uses the PDM TLS certificate by default:

```
/etc/proxmox-datacenter-manager/auth/api.pem   (certificate)
/etc/proxmox-datacenter-manager/auth/api.key   (private key)
```

This is the same certificate used by the PDM web UI. Clients that already
trust the PDM certificate require no additional configuration.

To use a different certificate, edit `[tls_server]` in `config.cfg` and update
the `--ssl-certfile` / `--ssl-keyfile` paths in the systemd unit file.

---

## File ownership summary

| Path | Owner | Group | Notes |
|---|---|---|---|
| `/opt/pdm-api-proxy/` | `pdm-api-proxy` | `pdm-api-proxy` | Application source |
| `/etc/pdm-api-proxy/` | `pdm-api-proxy` | `pdm-api-proxy` | Config and tokens |
| `/etc/proxmox-datacenter-manager/remotes.cfg` | `www-data` | `www-data` | Readable via group membership |
| `/etc/proxmox-datacenter-manager/remotes.shadow` | `www-data` | `www-data` | Readable via group membership |
| `/etc/proxmox-datacenter-manager/auth/` | `root` | `www-data` | Readable via group membership |
