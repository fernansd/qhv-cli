from __future__ import annotations

import os
import shlex
import signal
import socket
import subprocess
import time
from pathlib import Path

from qhv.host_checks import qemu_binary
from qhv.models import VmRecord, VmSpec


CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
STARTUP_TIMEOUT_SECONDS = 180
STARTUP_POLL_INTERVAL_SECONDS = 2.0
LOG_TAIL_BYTES = 4000


def parse_port_forward(value: str) -> tuple[int, int]:
    host, guest = value.split(":", 1)
    return int(host), int(guest)


class QemuRunner:
    def __init__(
        self,
        qemu_system: str | None = None,
        qemu_img: str | None = None,
        startup_timeout_seconds: float = STARTUP_TIMEOUT_SECONDS,
        startup_poll_interval_seconds: float = STARTUP_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.qemu_system = qemu_system or qemu_binary("qemu-system-x86_64") or "qemu-system-x86_64"
        self.qemu_img = qemu_img or qemu_binary("qemu-img") or "qemu-img"
        self.startup_timeout_seconds = startup_timeout_seconds
        self.startup_poll_interval_seconds = startup_poll_interval_seconds

    def _seed_drive_arg(self, seed_dir: Path) -> str:
        normalized = str(seed_dir.resolve()).replace("\\", "/")
        return (
            f"file.driver=vvfat,file.dir={normalized},file.label=cidata,"
            "file.floppy=on"
        )

    def build_command(self, record: VmRecord) -> list[str]:
        spec = record.spec
        netdev = ",".join(
            ["user,id=net0"]
            + [port.qemu_arg() for port in spec.all_forwarded_ports()]
        )
        return [
            self.qemu_system,
            "-accel",
            "whpx",
            "-machine",
            "q35",
            "-cpu",
            "qemu64",
            "-smp",
            str(spec.cpus),
            "-m",
            str(spec.memory_mb),
            "-name",
            spec.name,
            "-display",
            "none",
            "-serial",
            f"file:{record.log_path}",
            "-device",
            "virtio-net-pci,netdev=net0",
            "-netdev",
            netdev,
            "-drive",
            f"if=virtio,format=qcow2,file={record.disk_path}",
            "-drive",
            f"if=virtio,format=raw,readonly=on,{self._seed_drive_arg(record.seed_dir)}",
        ]

    def create_overlay_disk(self, base_image: Path, destination: Path, size_gb: int, base_format: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                self.qemu_img,
                "create",
                "-f",
                "qcow2",
                "-F",
                base_format,
                "-b",
                str(base_image),
                str(destination),
            ],
            check=True,
        )
        subprocess.run(
            [
                self.qemu_img,
                "resize",
                str(destination),
                f"{size_gb}G",
            ],
            check=True,
        )

    def _read_recent_logs(self, record: VmRecord, stderr_path: Path) -> tuple[str, str]:
        stderr_output = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        serial_output = record.log_path.read_text(encoding="utf-8", errors="replace") if record.log_path.exists() else ""
        return stderr_output[-LOG_TAIL_BYTES:], serial_output[-LOG_TAIL_BYTES:]

    def _startup_error(self, message: str, record: VmRecord, stderr_path: Path) -> RuntimeError:
        stderr_output, serial_output = self._read_recent_logs(record, stderr_path)
        return RuntimeError(
            f"{message}\n"
            f"stderr:\n{stderr_output}\n"
            f"serial:\n{serial_output}"
        )

    def _ssh_banner_available(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
                conn.settimeout(5)
                return conn.recv(256).startswith(b"SSH-")
        except OSError:
            return False

    def start(self, record: VmRecord) -> int | None:
        command = self.build_command(record)
        stderr_path = record.vm_dir / "qemu.stderr.log"
        stderr_handle = stderr_path.open("ab")
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
                creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW if os.name == "nt" else 0,
                stdin=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + self.startup_timeout_seconds
            while time.monotonic() < deadline:
                stderr_handle.flush()
                exit_code = process.poll()
                if exit_code is not None:
                    raise self._startup_error(
                        f"QEMU exited during startup with code {exit_code}.",
                        record,
                        stderr_path,
                    )
                if self._ssh_banner_available(record.spec.ssh_port):
                    return process.pid
                time.sleep(self.startup_poll_interval_seconds)
            terminate_pid(process.pid)
            raise self._startup_error(
                f"QEMU did not expose an SSH banner on localhost:{record.spec.ssh_port} within {int(self.startup_timeout_seconds)} seconds.",
                record,
                stderr_path,
            )
            return process.pid
        finally:
            stderr_handle.close()

    def ssh_command(self, spec: VmSpec) -> list[str]:
        return [
            "ssh",
            f"{spec.username}@127.0.0.1",
            "-p",
            str(spec.ssh_port),
        ]


def is_pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_pid(pid: int | None) -> None:
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        return
    os.kill(pid, signal.SIGTERM)


def format_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


