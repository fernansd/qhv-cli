from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ForwardPort:
    host: int
    guest: int
    protocol: str = "tcp"

    def qemu_arg(self) -> str:
        return f"hostfwd={self.protocol}::{self.host}-:{self.guest}"


@dataclass(slots=True)
class VmSpec:
    name: str
    distro: str = "ubuntu"
    release: str = "24.04"
    architecture: str = "x86_64"
    cpus: int = 1
    memory_mb: int = 1024
    disk_size_gb: int = 40
    ssh_port: int = 2222
    forwarded_ports: list[ForwardPort] = field(default_factory=list)
    username: str = "vmadmin"
    password: str | None = "vmadmin"
    ssh_public_key: str | None = None
    hostname: str | None = None

    def all_forwarded_ports(self) -> list[ForwardPort]:
        ports = [ForwardPort(host=self.ssh_port, guest=22)]
        ports.extend(self.forwarded_ports)
        return ports

    def normalized_hostname(self) -> str:
        return self.hostname or self.name


@dataclass(slots=True)
class ImageRef:
    provider: str
    release: str
    architecture: str
    filename: str
    url: str
    cache_path: Path
    disk_format: str

    def to_json(self) -> dict[str, str]:
        payload = asdict(self)
        payload["cache_path"] = str(self.cache_path)
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, str]) -> "ImageRef":
        return cls(
            provider=payload["provider"],
            release=payload["release"],
            architecture=payload["architecture"],
            filename=payload["filename"],
            url=payload["url"],
            cache_path=Path(payload["cache_path"]),
            disk_format=payload["disk_format"],
        )


@dataclass(slots=True)
class HostCheckItem:
    name: str
    ok: bool
    details: str
    remediation: str | None = None


@dataclass(slots=True)
class HostCheckResult:
    items: list[HostCheckItem]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.items)


@dataclass(slots=True)
class VmRecord:
    name: str
    spec: VmSpec
    image: ImageRef
    vm_dir: Path
    disk_path: Path
    seed_dir: Path
    log_path: Path
    pid: int | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "spec": {
                **asdict(self.spec),
                "forwarded_ports": [asdict(port) for port in self.spec.forwarded_ports],
            },
            "image": self.image.to_json(),
            "vm_dir": str(self.vm_dir),
            "disk_path": str(self.disk_path),
            "seed_dir": str(self.seed_dir),
            "log_path": str(self.log_path),
            "pid": self.pid,
        }

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> "VmRecord":
        spec_payload = dict(payload["spec"])
        ports = [ForwardPort(**port) for port in spec_payload.pop("forwarded_ports")]
        spec = VmSpec(**spec_payload, forwarded_ports=ports)
        return cls(
            name=str(payload["name"]),
            spec=spec,
            image=ImageRef.from_json(payload["image"]),
            vm_dir=Path(str(payload["vm_dir"])),
            disk_path=Path(str(payload["disk_path"])),
            seed_dir=Path(str(payload["seed_dir"])),
            log_path=Path(str(payload["log_path"])),
            pid=payload.get("pid"),
        )
