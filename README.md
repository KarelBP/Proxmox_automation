# Proxmox_automation
Proxmox automation for ServiceNow. The XML files in this repository are update sets for ServiceNow. The other files are for PDM API proxy running on Proxmox Datacenter Manager server.

This is an experimental code intended for lab use. The Python scripts were purely vibe coded using Claude.ai during a breakfast; there hasn't been any review done, they "just worked". If you see anybody using it in production, call security.

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

pdm-api-proxy-service => Service definition.

proxy.py => Generic transparent proxy layer. Forwards any request to the correct PVE node, replacing the Authorization
header with the PVE token stored in remotes.shadow.

## Articles
[AI to the Rescue](https://www.linkedin.com/pulse/ai-rescue-karel-bene%25C5%25A1-xuemf/) - Proxmox, ServiceNow automation and two new buzzwords.
