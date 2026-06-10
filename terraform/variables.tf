variable "proxmox_endpoint" {
  type        = string
  description = "Proxmox API URL, e.g. https://<host>:8006/"
}

variable "proxmox_api_token" {
  type        = string
  description = "API token in the form user@realm!tokenid=secret"
  sensitive   = true
}

variable "proxmox_ssh_password" {
  type        = string
  description = "SSH password for the Proxmox node (root) — used for disk import / file uploads"
  sensitive   = true
}

variable "target_node" {
  type        = string
  description = "Name of the Proxmox node to deploy on"
}

variable "template_id" {
  type        = number
  description = "VMID of the cloud-init template to clone"
}

variable "storage" {
  type        = string
  description = "Storage target for VM disks"
  default     = "storage1"
}

variable "bridge" {
  type        = string
  description = "Network bridge"
  default     = "vmbr231"
}

variable "ci_user" {
  type        = string
  description = "Cloud-init default username"
  default     = "arvan"
}

variable "ssh_public_keys" {
  type        = list(string)
  description = "SSH public keys to inject"
  default     = []
}

# Per-VM definition: name, VMID, and static IP.
variable "vms" {
  type = map(object({
    vmid = number
    ip   = string
  }))
  default = {
    kuber1 = { vmid = 1030, ip = "100.64.231.101" }
    kuber2 = { vmid = 1031, ip = "100.64.231.102" }
    kuber3 = { vmid = 1032, ip = "100.64.231.103" }
  }
}

variable "gateway" {
  type    = string
  default = "100.64.231.254"
}

variable "dns_server" {
  type    = string
  default = "8.8.8.8"
}
