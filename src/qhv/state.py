from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from qhv.models import VmRecord
from qhv.qemu import is_pid_running


class StateStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path.cwd() / ".qhv").resolve()
        self.images_dir = self.root / "images"
        self.vms_dir = self.root / "vms"

    def ensure_layout(self) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.vms_dir.mkdir(parents=True, exist_ok=True)

    def vm_dir(self, name: str) -> Path:
        return self.vms_dir / name

    def vm_record_path(self, name: str) -> Path:
        return self.vm_dir(name) / "vm.json"

    def has_vm_record(self, name: str) -> bool:
        return self.vm_record_path(name).exists()

    def save_vm(self, record: VmRecord) -> None:
        record.vm_dir.mkdir(parents=True, exist_ok=True)
        self.vm_record_path(record.name).write_text(
            json.dumps(record.to_json(), indent=2),
            encoding="utf-8",
        )

    def load_vm(self, name: str) -> VmRecord:
        payload = json.loads(self.vm_record_path(name).read_text(encoding="utf-8"))
        record = VmRecord.from_json(payload)
        if "state" not in payload:
            record.state = "running" if is_pid_running(record.pid) else "stopped"
        if "last_error" not in payload:
            record.last_error = None
        if "serial_mode" not in payload:
            record.serial_mode = "log-only"
        if "serial_socket_port" not in payload:
            record.serial_socket_port = None
        return record

    def has_vm(self, name: str) -> bool:
        return self.has_vm_record(name)

    def list_vms(self) -> list[str]:
        if not self.vms_dir.exists():
            return []
        return sorted(
            path.name
            for path in self.vms_dir.iterdir()
            if path.is_dir() and (path / "vm.json").exists()
        )

    def list_vm_dirs(self) -> list[Path]:
        if not self.vms_dir.exists():
            return []
        return sorted(path for path in self.vms_dir.iterdir() if path.is_dir())

    def delete_vm(self, name: str) -> bool:
        vm_dir = self.vm_dir(name)
        if not vm_dir.exists():
            return True
        for _ in range(20):
            try:
                shutil.rmtree(vm_dir)
            except FileNotFoundError:
                return True
            except OSError:
                if not vm_dir.exists():
                    return True
                time.sleep(0.25)
                continue
            return True
        return not vm_dir.exists()
