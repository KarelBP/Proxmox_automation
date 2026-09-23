# Proxmox automation for ServiceNow
### What if we could use Proxmox Datacenter Manager for automation?
This project is an experiment around that idea: using PDM as an automation endpoint for ServiceNow, with the PDM API proxy running on the Proxmox Datacenter Manager server to provide functionality available through the PVE API but not yet through the PDM API.

The XML files in this repository are update sets for ServiceNow. The other files are for the PDM API proxy running on the Proxmox Datacenter Manager server.

This code is intended for lab use. The Python scripts were purely vibe coded, partially fixed and tested using Claude.ai. The changes from the previous version are confined to the remotes.cfg / remotes.shadow parsing in config.py and to node resolution in main.py, which was pulled out of the request handler into a function of its own so it could be tested; beyond that, the other files only gained comments.

There was still no human line-by-line review. If you see anybody using it in production, call security.

## Files
Alias Proxmox automation.xml => Credential and connection alias used in the Flow Designer actions; needs to be populated with a connection and a credential.

Proxmox clone CT.xml => Flow Designer action for cloning of containers.

Proxmox clone VM.xml => Flow Designer action for cloning of virtual machines.

Proxmox configure CT.xml => Flow Designer action for configuration of containers; see Proxmox PVE API documentation for possible keys and values, e.g. to set memory to 4096 you'll need to set param_key to memory and param_value to 4096.

Proxmox configure VM.xml => Flow Designer action for configuration of virtual machines; see Proxmox PVE API documentation for possible keys and values, e.g. to add a new 20GB disk residing on local-lvm storage you'll need to set param_key to scsi1 and param_value to local-lvm:20.

Proxmox create CT.xml => Flow Designer action for deployment of a container with a minimal set of necessary parameters: name, id, which image, OS type, where to place it, where to connect the network adapter, SSH keys and start after creation (yes/no); the rest needs to be done with configuration.

Proxmox create VM.xml => Flow Designer action for deployment of a virtual machine with a minimal set of necessary parameters: name, id, OS type, CPU sizing, memory sizing, OS drive sizing and placement, where to connect the network adapter, which ISO image to boot from and start after creation (yes/no); the rest needs to be done with configuration.

Proxmox delete CT.xml => Flow Designer action for deletion of containers.

Proxmox delete VM.xml => Flow Designer action for deletion of virtual machines.

config.cfg.example => Basic configuration for PDM API proxy.

config.py => Parser for PDM remote config files and proxy-own configuration.

main.py => Transparent proxy for the PVE REST API using PDM remote credentials.

pdm-api-proxy.service => Service definition.

proxy.py => Generic transparent proxy layer. Forwards any request to the correct PVE node, replacing the Authorization
header with the PVE token stored in remotes.shadow.

tests/ => Regression tests for the remotes.cfg/remotes.shadow parsing and node resolution. Run `python3 -m pytest tests/` from this directory; needs pytest in addition to the runtime dependencies.

## Known limitations
The proxy reads remotes.cfg and remotes.shadow once at startup and caches an HTTP client with each remote's PVE token. A token rotated in remotes.shadow is therefore not picked up: every forwarded call returns 401 until the service is restarted. A remote newly enrolled in PDM remains unavailable and returns 404 for the same reason.

In short, restart the service after making changes to your PDM configuration.

## Articles
[AI to the Rescue](https://www.linkedin.com/pulse/ai-rescue-karel-bene%25C5%25A1-xuemf/) - Proxmox, ServiceNow automation and two new buzzwords.
