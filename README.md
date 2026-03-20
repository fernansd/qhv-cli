# qhv

`qhv` is a Windows-focused CLI for creating Linux server VMs with QEMU and WHPX acceleration. It is designed for localhost developer workflows: download a cloud image, generate cloud-init data, boot the VM, and connect over SSH through forwarded ports.

## Current scope

- Windows host checks for QEMU, WHPX, Hyper-V-related optional features, and virtualization support.
- Cloud image providers for Ubuntu Server and Fedora Cloud with a shared provider interface.
- Public Incus/Linux Containers image-server support for VM images through simplestreams metadata.
- User-mode networking with localhost port forwards.
- Commands for `check`, `create`, `start`, `status`, `ssh`, `console`, `prune`, and `delete`.
- Local state under `.qhv/` in the working directory.

## Requirements

- Python 3.11+
- `uv` for dependency management
- QEMU for Windows with `qemu-system-x86_64.exe` and `qemu-img.exe` on `PATH`
- Windows hypervisor support enabled
- OpenSSH client recommended for the `ssh` command

## Quick start

```powershell
uv sync
uv run qhv check
uv run qhv create demo
uv run qhv status demo
uv run qhv console demo --dump
uv run qhv ssh demo
```

Fedora 43 example:

```powershell
uv run qhv create fedora-demo --distro fedora --release 43
```

Public Incus image-server example:

```powershell
uv run qhv create noble-demo --image-source incus --distro ubuntu --release noble --variant cloud
```

The default image is the Ubuntu 24.04 cloud image (`--image-source cloud --distro ubuntu --release 24.04`). Public Incus image-server support requires an explicit `--release`; `--variant` defaults to `cloud`. The default guest user is `vmadmin`. If `~/.ssh/id_ed25519.pub` or `~/.ssh/id_rsa.pub` exists, it will be injected into the VM. Otherwise the tool enables password SSH with password `vmadmin`.

If you do not pass `--ssh-port`, `qhv create` picks the first free localhost SSH port starting at `2222`. The command also prints progress while it resolves the image, generates cloud-init assets, creates the overlay disk, and waits for the guest SSH banner. Auto-selected ports stay stable across known VMs, so stopped VMs still reserve their saved SSH port until you delete them.

Failed boots are retained as VM records with logs and the last startup error so you can inspect them with `qhv status`, `qhv console`, or `qhv show-cmd`, then remove them with `qhv delete` or `qhv prune`.

`qhv ssh` disables SSH host-key persistence for these localhost-forwarded VM sessions, so recreated VMs do not trip stale `known_hosts` entries.

## Troubleshooting

If `uv run qhv check` reports failures, start with [docs/troubleshooting.md](docs/troubleshooting.md). It covers the current host checks, QEMU installation on Windows, `PATH` setup, and enabling Windows hypervisor features required by WHPX.

## Testing

Fast verification:

```powershell
uv run pytest
```

Real VM lifecycle test:

```powershell
$env:QHV_RUN_VM_TESTS='1'
uv run pytest -m vm
```

The VM lifecycle test is opt-in because it downloads a cloud image, launches a real Linux guest, waits for the guest SSH banner through the forwarded port, and then destroys the VM. It expects a Windows host with QEMU available and WHPX-capable acceleration.

## Notes

- The primary acceleration backend is WHPX (`-accel whpx`).
- The cloud-init seed is generated as a small directory and attached to QEMU via the `fat:` driver. This keeps V1 self-contained on Windows without requiring external ISO tooling.
- V1 uses user-mode networking. The internal networking model is intentionally separated so bridged, NAT, or multi-VM topologies can be added later.
- `qhv console <name>` prefers a live serial attach for newly started VMs and falls back to following `.qhv/vms/<name>/serial.log` for older VMs.
- `qhv prune` removes stale VM directories and dead failed-start records without touching healthy stopped VMs.
- A listening forwarded port only proves QEMU networking is up; if SSH still resets, use `qhv console <name>` first, then inspect `.qhv/vms/<name>/qemu.stderr.log` for guest bootstrap details.
