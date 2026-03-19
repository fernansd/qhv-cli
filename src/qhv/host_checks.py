from __future__ import annotations

import platform
import shutil
import subprocess

from qhv.models import HostCheckItem, HostCheckResult


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _normalize_feature_state(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"enabled", "habilitado", "activado"}:
        return "Enabled"
    if normalized in {"disabled", "deshabilitado", "desactivado"}:
        return "Disabled"
    return value.strip()


def parse_feature_state(output: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("State :") or stripped.startswith("Estado :"):
            return _normalize_feature_state(stripped.split(":", 1)[1])
    return None


def qemu_binary(name: str) -> str | None:
    candidates = [name]
    if not name.endswith(".exe"):
        candidates.append(f"{name}.exe")
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def get_optional_feature_state(feature_name: str) -> str | None:
    command = [
        "dism.exe",
        "/Online",
        "/Get-FeatureInfo",
        f"/FeatureName:{feature_name}",
    ]
    result = _run(command)
    if result.returncode != 0:
        return None
    return parse_feature_state(result.stdout)


def qemu_supports_whpx(qemu_system: str) -> bool:
    result = _run([qemu_system, "-accel", "help"])
    return result.returncode == 0 and "whpx" in result.stdout.lower()


def virtualization_capable() -> bool:
    return platform.machine().lower() in {"amd64", "x86_64"}


def _feature_details(state: str | None, supports_whpx: bool = False) -> str:
    detail = f"Feature state: {state or 'unknown'}"
    if state is None and supports_whpx:
        return f"{detail} (WHPX available via QEMU)"
    return detail


def collect_host_checks() -> HostCheckResult:
    qemu_system = qemu_binary("qemu-system-x86_64")
    qemu_img = qemu_binary("qemu-img")
    supports_whpx = qemu_supports_whpx(qemu_system) if qemu_system else False
    whpx_state = get_optional_feature_state("HypervisorPlatform")
    vmp_state = get_optional_feature_state("VirtualMachinePlatform")
    hyperv_state = get_optional_feature_state("Microsoft-Hyper-V-All")

    items = [
        HostCheckItem(
            name="Windows host",
            ok=platform.system() == "Windows",
            details=f"Detected {platform.system()}",
            remediation="Run this tool on Windows.",
        ),
        HostCheckItem(
            name="x86_64 host",
            ok=virtualization_capable(),
            details=f"Detected architecture {platform.machine()}",
            remediation="Use an x86_64/amd64 Windows host for WHPX-backed QEMU.",
        ),
        HostCheckItem(
            name="qemu-system-x86_64",
            ok=qemu_system is not None,
            details=qemu_system or "Not found on PATH",
            remediation="Install QEMU for Windows and add it to PATH.",
        ),
        HostCheckItem(
            name="qemu-img",
            ok=qemu_img is not None,
            details=qemu_img or "Not found on PATH",
            remediation="Install QEMU for Windows and add qemu-img to PATH.",
        ),
        HostCheckItem(
            name="Windows Hypervisor Platform",
            ok=whpx_state == "Enabled" or supports_whpx,
            details=_feature_details(whpx_state, supports_whpx=supports_whpx),
            remediation="Enable the 'Windows Hypervisor Platform' optional feature.",
        ),
        HostCheckItem(
            name="Virtual Machine Platform",
            ok=vmp_state in {"Enabled", None},
            details=_feature_details(vmp_state),
            remediation="Enable 'Virtual Machine Platform' if WHPX setup remains incomplete.",
        ),
        HostCheckItem(
            name="Hyper-V platform",
            ok=hyperv_state in {"Enabled", None},
            details=_feature_details(hyperv_state),
            remediation="Enable Hyper-V components if your host policy requires them.",
        ),
    ]
    if qemu_system:
        items.append(
            HostCheckItem(
                name="QEMU WHPX acceleration",
                ok=supports_whpx,
                details="WHPX listed by QEMU" if supports_whpx else "WHPX not reported by QEMU",
                remediation="Use a QEMU build that includes WHPX support.",
            )
        )
    return HostCheckResult(items=items)
