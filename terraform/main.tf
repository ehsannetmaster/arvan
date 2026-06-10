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

  # Disk inherited from the template clone; resize here if desired.
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
