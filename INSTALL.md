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
