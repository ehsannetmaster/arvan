# Proxmox: Deploy VMs with Terraform

This guide documents the Terraform configuration that clones an existing
Proxmox cloud-init template into multiple VMs with static IP configuration,
and the steps to apply it.

It pairs with [proxmox-create-template.md](proxmox-create-template.md) (which
builds template `9000`) and [proxmox-create-token.md](proxmox-create-token.md)
(which creates the API user/token).

## Overview

- **Provider:** `bpg/proxmox`
- **What it does:** full-clones template `9000` into 3 VMs
- **Per VM:** 4 cores, 8 GB RAM, 30 GB disk, static IP via cloud-init

| VM name | VMID | IP address       |
|---------|------|------------------|
| kuber1  | 1030 | 100.64.231.101   |
| kuber2  | 1031 | 100.64.231.102   |
| kuber3  | 1032 | 100.64.231.103   |

Gateway `100.64.231.254`, DNS `8.8.8.8`, subnet `/24`.

## Directory layout

```
terraform/
├── providers.tf      # provider + connection config
├── variables.tf      # input variable declarations
├── main.tf           # the VM resource (clone + cloud-init)
├── terraform.tfvars  # actual values (secrets — not committed)
└── .gitignore        # excludes secrets and state
```

## Configuration files

### providers.tf

```hcl
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
  endpoint  = var.proxmox_endpoint
  api_token = var.proxmox_api_token
  insecure  = true

  ssh {
    agent    = false
    username = "root"
    password = var.proxmox_ssh_password
  }
}
```

### variables.tf

```hcl
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
  description = "SSH password for the Proxmox node (root)"
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
```

### main.tf

```hcl
resource "proxmox_virtual_environment_vm" "vm" {
  for_each = var.vms

  name      = each.key
  vm_id     = each.value.vmid
  node_name = var.target_node

  # Clone the existing cloud-init template (full clone).
  clone {
    vm_id = var.template_id
    full  = true
  }

  agent {
    enabled = false
  }

  cpu {
    cores = 4
    type  = "host"
  }

  memory {
    dedicated = 8192 # 8 GB
  }

  disk {
    datastore_id = var.storage
    interface    = "scsi0"
    size         = 30 # GB — must be >= template disk size
    iothread     = true
  }

  network_device {
    bridge = var.bridge
    model  = "virtio"
  }

  serial_device {} # matches the template's serial0 console

  # Cloud-init: static IP, gateway, DNS, user, SSH keys.
  initialization {
    datastore_id = var.storage

    ip_config {
      ipv4 {
        address = "${each.value.ip}/24"
        gateway = var.gateway
      }
    }

    dns {
      servers = [var.dns_server]
    }

    user_account {
      username = var.ci_user
      keys     = var.ssh_public_keys
    }
  }
}
```

### terraform.tfvars (values — keep private)

```hcl
proxmox_endpoint     = "https://192.168.255.101:8006/"
proxmox_api_token    = "automation@pve!terraform=<secret>"
proxmox_ssh_password = "<root-password>"

target_node = "vchost1"
template_id = 9000
storage     = "storage1"
bridge      = "vmbr231"

ci_user = "arvan"
ssh_public_keys = [
  "ssh-ed25519 AAAA... ehsan@win-mng",     # admin access from the management workstation
  "ssh-ed25519 AAAA... root@terraform",    # Ansible control node — see below
]
```

### SSH keys and their purpose

Each key in `ssh_public_keys` is injected into the `arvan` user's
`~/.ssh/authorized_keys` on every VM via cloud-init:

| Key (comment)    | Purpose |
|------------------|---------|
| `ehsan@win-mng`  | Administrative SSH access from the management workstation. |
| `root@terraform` | **Ansible control node.** This key lets Ansible (running on the `terraform` host) connect to the VMs over SSH without a password, so it can run the playbooks that **install and configure Kubernetes** across the three nodes. |

The `root@terraform` key must be present on the VMs before Ansible runs —
otherwise Ansible's SSH connection (and therefore the Kubernetes install) will
fail with a permission error.

## Prerequisites

- Terraform `>= 1.5` installed on the machine running the deploy
- Network access from that machine to the Proxmox API (`:8006`) and SSH (`:22`)
- Template `9000` exists on the target node
- `terraform.tfvars` filled in with real values (token, SSH password, node name)

## Steps to apply

```bash
# 1. Enter the config directory
cd terraform

# 2. Initialize — downloads the bpg/proxmox provider
terraform init

# 3. (Optional) validate syntax
terraform validate

# 4. Preview the changes
terraform plan

# 5. Deploy (type "yes" when prompted)
terraform apply
```

Expected plan: **3 to add, 0 to change, 0 to destroy**.

## Verify

On the Proxmox node:

```bash
qm list                 # VMIDs 1030, 1031, 1032 running
```

From a host on the network:

```bash
ping 100.64.231.101
ssh arvan@100.64.231.101
```

The Proxmox **serial console** shows `starting serial terminal on interface
serial0` — press **Enter** to get the login prompt. The VMs are SSH-key only,
so log in over SSH rather than at the console.

## Common operations

```bash
# Add/remove a VM: edit the `vms` map, then:
terraform apply

# Tear everything down:
terraform destroy

# Inspect state:
terraform state list
terraform show
```

## Notes

- `terraform.tfvars` and `*.tfstate` contain secrets — never commit them
  (covered by `.gitignore`).
- Changing a VM's **map key** (e.g. `kuber1`) destroys and recreates that VM;
  use `terraform state mv` to rename safely.
- Disk `size` must be **>=** the template's disk size.
```
