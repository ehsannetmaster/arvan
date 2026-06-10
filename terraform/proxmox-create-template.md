# Proxmox: Create an Ubuntu 24.04 Cloud-Init Template

This guide walks through creating a reusable Proxmox VM template from the
official Ubuntu 24.04 (Noble) cloud image, ready for cloud-init based
provisioning.

## Prerequisites

- Proxmox VE host with shell access
- A storage target (this guide uses `storage1`)
- A network bridge (this guide uses `vmbr231`)

## Steps

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

## Next steps

Clone the template to create new VMs, then set cloud-init values such as
user, SSH keys, and IP configuration before first boot:

```bash
qm clone 9000 100 --name my-new-vm
qm set 100 --ciuser ubuntu --sshkeys ~/.ssh/id_rsa.pub
qm set 100 --ipconfig0 ip=dhcp
qm start 100
```
