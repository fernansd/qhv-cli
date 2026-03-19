from __future__ import annotations

from pathlib import Path

from qhv.models import VmSpec


def discover_default_public_key() -> str | None:
    ssh_dir = Path.home() / ".ssh"
    for candidate in ("id_ed25519.pub", "id_rsa.pub"):
        path = ssh_dir / candidate
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return None


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_user_data(spec: VmSpec) -> str:
    ssh_key = spec.ssh_public_key or discover_default_public_key()
    lines = [
        "#cloud-config",
        f"hostname: {spec.normalized_hostname()}",
        "users:",
        "  - default",
        f"  - name: {spec.username}",
        "    sudo: ALL=(ALL) NOPASSWD:ALL",
        "    groups: [adm, sudo, wheel]",
        "    shell: /bin/bash",
    ]
    if ssh_key:
        lines.extend(
            [
                "    ssh_authorized_keys:",
                f"      - {ssh_key}",
            ]
        )
    else:
        password = spec.password or "vmadmin"
        lines.extend(
            [
                "    lock_passwd: false",
            ]
        )
    lines.extend(
        [
            "package_update: true",
            "packages:",
            "  - qemu-guest-agent",
        ]
    )
    if ssh_key:
        lines.append("ssh_pwauth: false")
    else:
        lines.extend(
            [
                "chpasswd:",
                "  expire: false",
                "  users:",
                f"    - name: {spec.username}",
                f"      password: {_yaml_quote(password)}",
                "      type: text",
                "ssh_pwauth: true",
            ]
        )
    return "\n".join(lines) + "\n"


def render_meta_data(spec: VmSpec) -> str:
    return (
        f"instance-id: {spec.name}\n"
        f"local-hostname: {spec.normalized_hostname()}\n"
    )


def render_network_config() -> str:
    return "\n".join(
        [
            "version: 2",
            "ethernets:",
            "  default-nic:",
            "    match:",
            '      name: "en*"',
            "    dhcp4: true",
        ]
    ) + "\n"


class GuestBootstrapper:
    def write_seed(self, seed_dir: Path, spec: VmSpec) -> Path:
        seed_dir.mkdir(parents=True, exist_ok=True)
        (seed_dir / "user-data").write_text(render_user_data(spec), encoding="utf-8")
        (seed_dir / "meta-data").write_text(render_meta_data(spec), encoding="utf-8")
        (seed_dir / "network-config").write_text(render_network_config(), encoding="utf-8")
        return seed_dir
