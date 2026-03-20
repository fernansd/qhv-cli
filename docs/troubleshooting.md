# Troubleshooting `qhv check`

This guide maps each `qhv check` failure to the fix you should try on Windows.

## `qemu-system-x86_64: Not found on PATH`

`qhv` could not find the main QEMU system binary.

### Fix

1. Install QEMU for Windows.
2. Make sure the QEMU install directory is on your `PATH`.
3. Open a new PowerShell session and verify:

```powershell
qemu-system-x86_64.exe --version
```

If that command fails, `qhv` will fail too.

## `qemu-img: Not found on PATH`

`qhv` uses `qemu-img` to create the writable VM overlay disk.

### Fix

1. Ensure the same QEMU installation also includes `qemu-img.exe`.
2. Verify it is on your `PATH`:

```powershell
qemu-img.exe --version
```

## `Windows Hypervisor Platform: Feature state: unknown` or `FAIL`

WHPX depends on Windows hypervisor support. If the feature is disabled, unavailable, or `dism.exe` cannot report its state, QEMU acceleration may not work.

### Fix

Run PowerShell as Administrator and enable the required Windows optional features:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform -All
Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All
```

If your Windows edition supports Hyper-V and your setup expects it, also enable:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All
```

Reboot after enabling these features, then rerun:

```powershell
uv run qhv check
```

## `QEMU WHPX acceleration: WHPX not reported by QEMU`

Your QEMU build is installed, but it does not appear to expose WHPX acceleration.

### Fix

1. Verify QEMU reports accelerators directly:

```powershell
qemu-system-x86_64.exe -accel help
```

2. Confirm `whpx` appears in the output.
3. If it does not, install a Windows QEMU build that includes WHPX support.

## `Virtual Machine Platform` or `Hyper-V platform` reported as `unknown`

`qhv` currently reads optional feature state through `dism.exe`. On some machines that may return no usable state because of permissions, OS policy, or host configuration.

### Fix

Manually inspect the feature state:

```powershell
Get-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform
Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All
```

If these show `Disabled`, enable them and reboot.

## Suggested validation flow

After applying fixes, validate in this order:

```powershell
qemu-system-x86_64.exe --version
qemu-img.exe --version
uv run qhv check
uv run qhv create demo
```

If `ssh` resets or disconnects immediately but `127.0.0.1:<port>` still accepts TCP connections, treat that as a guest bootstrap problem, not proof that SSH is configured correctly. Inspect the VM serial log and QEMU stderr log under `.qhv/vms/<name>/`.

Use the built-in serial troubleshooting command first:

```powershell
uv run qhv console demo
uv run qhv console demo --dump
```

`qhv console` prefers a live serial attach for newly started VMs and falls back to the persisted `serial.log` for older VMs.

`qhv ssh` also disables SSH host-key persistence for localhost-forwarded VM sessions, so recreated VMs do not fail on stale `known_hosts` entries.

## Notes

- `qhv check` is a host readiness check. It does not guarantee that firmware settings such as CPU virtualization are enabled, but failures here should be resolved first.
- If your environment has group policy or corporate hardening, feature detection may show `unknown` even when the feature is enabled. In that case, verify with the PowerShell commands above.
- Stale directories under `.qhv/vms/` without a `vm.json` record are invalid leftovers and should be safe to remove once no VM process is still using them.
- `uv run qhv prune` removes stale directories and dead failed-start VM records while leaving healthy stopped VMs intact.
