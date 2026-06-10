terraform {
  required_version = ">= 1.5"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.66"
    }
  }
}

provider "proxmox" {
  endpoint  = var.proxmox_endpoint  # e.g. "https://192.168.1.10:8006/"
  api_token = var.proxmox_api_token # "automation@pve!mytoken=xxxxxxxx-xxxx-..."
  insecure  = true                  # set false if you have a valid TLS cert

  # Required for some operations (disk import, file uploads) — uses SSH.
  ssh {
    agent    = false
    username = "root"
    password = var.proxmox_ssh_password
  }
}
