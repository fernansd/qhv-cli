# AGENTS.md

## Purpose

This repository contains `qhv`, a Windows-focused CLI for provisioning Linux VMs with QEMU and WHPX. The current goal is fast localhost developer VMs, not a full VM platform.

## Product boundaries

- Primary host target: Windows on `x86_64`/`amd64`.
- Primary virtualization path: QEMU using WHPX acceleration.
- V1 networking: QEMU user-mode networking with localhost port forwards.
- V1 guest bootstrap: cloud-init seed files attached through QEMU's read-only `vvfat` support with a `cidata` label for NoCloud discovery.
- Current distro targets: Ubuntu Server cloud images first, Fedora Cloud as the second provider.
- Out of scope unless explicitly requested: native Hyper-V VM management, bridged switch orchestration, snapshots, GUI work, or multi-VM topology features.

## Repository layout

- `src/qhv/cli.py`: Typer CLI commands and user-facing flows.
- `src/qhv/models.py`: shared dataclasses used across modules.
- `src/qhv/state.py`: `.qhv/` state layout and VM record persistence.
- `src/qhv/host_checks.py`: Windows host/QEMU/WHPX validation.
- `src/qhv/images.py`: distro image providers and download logic.
- `src/qhv/bootstrap.py`: cloud-init content generation.
- `src/qhv/qemu.py`: QEMU command building, disk creation, process lifecycle, and SSH command generation.
- `tests/`: unit coverage for the core non-boot paths.

## Working rules for agents

- Preserve the separation between CLI orchestration and backend logic. Add behavior to `cli.py` only when it is truly presentation or command wiring.
- Keep image-source logic behind provider-style abstractions. Do not hardcode distro-specific URLs directly into CLI flows.
- Keep future networking work isolated. If you add bridged, NAT, or inter-VM networking later, extend the QEMU/networking model instead of rewriting command handlers around ad hoc flags.
- Treat `.qhv/` as the only local project state root unless there is a strong reason to change the layout.
- Prefer additive changes that keep `create`, `start`, `status`, `ssh`, `delete`, and `check` stable as the public CLI contract.
- If you change QEMU invocation semantics, verify both command generation and PID/lifecycle behavior on Windows. This code currently tracks the spawned QEMU process directly.
- When adding a new image provider, specify the backing disk format explicitly because overlay creation depends on it.

## Implementation notes

- Ubuntu cloud images are currently modeled as `qcow2`; Fedora cloud images are modeled as `qcow2`. The overlay disk creation path depends on `ImageRef.disk_format`.
- The cloud-init seed is a directory, not an ISO. It is attached through a read-only QEMU `vvfat` device labeled `cidata` so cloud-init discovers it as a NoCloud seed.
- The generated network config matches predictable `en*` guest NIC names. If networking regresses, inspect the rendered `network-config` before assuming a host-side port-forward issue.
- Default `create` sizing is intentionally conservative: `1` vCPU and `1024` MiB. This baseline is used because it is stable on the current Windows/WHPX host.
- `qhv create` is expected to wait for an SSH banner before reporting success. If you change startup behavior, preserve the distinction between "QEMU spawned" and "guest is reachable".
- Default SSH key discovery only checks `~/.ssh/id_ed25519.pub` and `~/.ssh/id_rsa.pub`. Other keys on disk are not considered unless explicitly passed.
- Host checks may return `unknown` feature states on machines where `dism.exe` output is unavailable or restricted. Keep failures actionable rather than assuming a single Windows configuration.
- Tests include both deterministic unit coverage and opt-in real VM coverage. The real VM suite includes a constrained lifecycle test and a README-style default-flow test.
- `pyproject.toml` sets `pytest` to use `.pytest_tmp/` because the default temp location may be inaccessible on this machine.
- The default README flow depends on host port `2222` being free. Real-VM tests that exercise the default flow should skip rather than fail when that port is already occupied by the host.

## Common commands

```powershell
uv sync --extra dev
uv run pytest
uv run qhv --help
uv run qhv check
$env:QHV_RUN_VM_TESTS='1'; uv run pytest -m vm -vv -rs
```

## Safe extension points

- Add new CLI flags by extending `VmSpec` first when the flag changes VM behavior.
- Add new distros by implementing another provider in `src/qhv/images.py` and reusing the existing bootstrap/state flow.
- Add new validation by returning another `HostCheckItem` from `collect_host_checks()`.
- Add future config-file support as a layer that resolves into `VmSpec`, rather than bypassing the current model types.

## Verification expectations

Before closing work, run at least:

```powershell
uv run pytest
uv run qhv --help
```

If the task changes host detection or real launch behavior, also run `uv run qhv check`. If QEMU is not installed on the machine, call that out explicitly instead of claiming end-to-end launch validation.

If the task changes launch, bootstrap, default sizing, port forwarding, or user-facing lifecycle behavior, also run the opt-in VM suite. Treat the README-style default flow as a first-class validation target, not just the constrained integration harness.
