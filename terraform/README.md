# Proxmox: Cloud-Init Template + Terraform VM Deployment

End-to-end provisioning of the Kubernetes nodes on Proxmox: first build a
reusable Ubuntu 24.04 cloud-init **template**, then clone it into the VMs with
**Terraform**. Creating the API user/token is covered separately in
[proxmox-create-token.md](../proxmox-create-token.md).

---

## Part 1 — Create an Ubuntu 24.04 cloud-init template

Create a reusable Proxmox VM template from the official Ubuntu 24.04 (Noble)
cloud image, ready for cloud-init based provisioning.

### Prerequisites

- Proxmox VE host with shell access
- A storage target (this guide uses `storage1`)
- A network bridge (this guide uses `vmbr231`)

### 1. Download the Ubuntu cloud image

```bash
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
```

### 2. Create the VM shell

Create VM `9000` with 2 GB RAM, 2 cores, and a virtio NIC on the bridge.

```bash
qm create 9000 --name ubuntu-2404-template --memory 2048 --cores 2 \
  --net0 virtio,bridge=vmbr231
```

### 3. Import the cloud image as a disk

```bash
qm importdisk 9000 noble-server-cloudimg-amd64.img storage1
```

### 4. Attach the disk to a SCSI controller

Use the `virtio-scsi-single` controller with iothread enabled.

```bash
qm set 9000 --scsihw virtio-scsi-single --scsi0 storage1:9000/vm-9000-disk-0.raw,iothread=1
```

### 5. Add a cloud-init drive

```bash
qm set 9000 --ide2 storage1:cloudinit
```

### 6. Configure boot order

Boot from the imported SCSI disk.

```bash
qm set 9000 --boot c --bootdisk scsi0
```

The `c` comes from old BIOS/DOS-style drive lettering that QEMU adopted for its
legacy boot syntax — it means **"boot from the hard disk."**

| Letter | Boot device |
|--------|----------------------|
| `a`    | floppy disk          |
| `c`    | hard disk (the "C: drive") |
| `d`    | CD-ROM               |
| `n`    | network (PXE)        |

So `--boot c` says *boot from the hard disk*, and `--bootdisk scsi0` specifies
*which* disk that is (the imported Ubuntu image). Letters can be combined to set
an order — e.g. `--boot dc` means "try CD-ROM first, then hard disk."

### 7. Enable the serial console

Required for the cloud image's serial output.

```bash
qm set 9000 --serial0 socket --vga serial0
```

### 8. Enable the QEMU guest agent

```bash
qm set 9000 --agent enabled=1
```

### 9. Convert the VM into a template

```bash
qm template 9000
```

### Manual clone (quick test)

To validate the template by hand, clone it and set cloud-init values before
first boot. For the real multi-VM deployment, use Terraform (Part 2) instead.

```bash
qm clone 9000 100 --name my-new-vm
qm set 100 --ciuser ubuntu --sshkeys ~/.ssh/id_rsa.pub
qm set 100 --ipconfig0 ip=dhcp
qm start 100
```

---

## Part 2 — Deploy VMs with Terraform

The Terraform configuration in this directory clones template `9000` (from
Part 1) into multiple VMs with static IP configuration. It needs an API
user/token — see [proxmox-create-token.md](../proxmox-create-token.md).

### Overview

- **Provider:** `bpg/proxmox`
- **What it does:** full-clones template `9000` into 3 VMs
- **Per VM:** 4 cores, 8 GB RAM, 30 GB disk, static IP via cloud-init

| VM name | VMID | IP address       |
|---------|------|------------------|
| kuber1  | 1030 | 100.64.231.101   |
| kuber2  | 1031 | 100.64.231.102   |
| kuber3  | 1032 | 100.64.231.103   |

Gateway `100.64.231.254`, DNS `8.8.8.8`, subnet `/24`.

### Directory layout

```
terraform/
├── providers.tf      # provider + connection config
├── variables.tf      # input variable declarations
├── main.tf           # the VM resource (clone + cloud-init)
├── terraform.tfvars  # actual values (secrets — not committed)
└── .gitignore        # excludes secrets and state
```

### Configuration files

#### providers.tf

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

#### variables.tf

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

#### main.tf

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

#### terraform.tfvars (values — keep private)

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

### Prerequisites

- Terraform `>= 1.5` installed on the machine running the deploy
- Network access from that machine to the Proxmox API (`:8006`) and SSH (`:22`)
- Template `9000` exists on the target node (Part 1)
- `terraform.tfvars` filled in with real values (token, SSH password, node name)

### Steps to apply

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

### Verify

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

### Common operations

```bash
# Add/remove a VM: edit the `vms` map, then:
terraform apply

# Tear everything down:
terraform destroy

# Inspect state:
terraform state list
terraform show
```

### Notes

- `terraform.tfvars` and `*.tfstate` contain secrets — never commit them
  (covered by `.gitignore`).
- Changing a VM's **map key** (e.g. `kuber1`) destroys and recreates that VM;
  use `terraform state mv` to rename safely.
- Disk `size` must be **>=** the template's disk size.
