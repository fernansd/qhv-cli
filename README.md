# qhv

`qhv` is a Windows-focused CLI for creating Linux server VMs with QEMU and WHPX acceleration. It is designed for localhost developer workflows: download a cloud image, generate cloud-init data, boot the VM, and connect over SSH through forwarded ports.

## Current scope

- Windows host checks for QEMU, WHPX, Hyper-V-related optional features, and virtualization support.
- Cloud image providers for Ubuntu Server and Fedora Cloud with a shared provider interface.
- User-mode networking with localhost port forwards.
- Commands for `check`, `create`, `start`, `status`, `ssh`, and `delete`.
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
uv run qhv ssh demo
```

The default image is Ubuntu 24.04 cloud image. The default guest user is `vmadmin`. If `~/.ssh/id_ed25519.pub` or `~/.ssh/id_rsa.pub` exists, it will be injected into the VM. Otherwise the tool enables password SSH with password `vmadmin`.

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
- A listening forwarded port only proves QEMU networking is up; if SSH still resets, check `.qhv/vms/<name>/serial.log` and `.qhv/vms/<name>/qemu.stderr.log` for guest bootstrap details.
