import json
from pathlib import Path

from typer.testing import CliRunner

from qhv.cli import app
from qhv.models import ImageRef, VmRecord, VmSpec
from qhv.state import StateStore


def test_state_store_round_trip(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    spec = VmSpec(name="demo")
    image = ImageRef(
        provider="ubuntu",
        release="24.04",
        architecture="x86_64",
        filename="ubuntu.img",
        url="https://example.com/ubuntu.img",
        cache_path=tmp_path / "images" / "ubuntu.img",
        disk_format="qcow2",
    )
    record = VmRecord(
        name="demo",
        spec=spec,
        image=image,
        vm_dir=tmp_path / "vms" / "demo",
        disk_path=tmp_path / "vms" / "demo" / "demo.qcow2",
        seed_dir=tmp_path / "vms" / "demo" / "seed",
        log_path=tmp_path / "vms" / "demo" / "serial.log",
        pid=123,
        state="failed",
        last_error="boom",
        serial_mode="socket",
        serial_socket_port=40222,
    )
    store.save_vm(record)
    loaded = store.load_vm("demo")
    assert loaded.spec.name == "demo"
    assert loaded.spec.image_source == "cloud"
    assert loaded.spec.variant == "cloud"
    assert loaded.pid == 123
    assert loaded.image.disk_format == "qcow2"
    assert loaded.state == "failed"
    assert loaded.last_error == "boom"
    assert loaded.serial_mode == "socket"
    assert loaded.serial_socket_port == 40222


def test_load_vm_backfills_new_fields_for_older_records(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    payload = {
        "name": "demo",
        "spec": {
            "name": "demo",
            "distro": "ubuntu",
            "release": "24.04",
            "architecture": "x86_64",
            "cpus": 1,
            "memory_mb": 1024,
            "disk_size_gb": 40,
            "ssh_port": 2222,
            "forwarded_ports": [],
            "username": "vmadmin",
            "password": "vmadmin",
            "ssh_public_key": None,
            "hostname": None,
        },
        "image": {
            "provider": "ubuntu",
            "release": "24.04",
            "architecture": "x86_64",
            "filename": "ubuntu.img",
            "url": "https://example.com/ubuntu.img",
            "cache_path": str(tmp_path / "images" / "ubuntu.img"),
            "disk_format": "qcow2",
        },
        "vm_dir": str(tmp_path / "vms" / "demo"),
        "disk_path": str(tmp_path / "vms" / "demo" / "demo.qcow2"),
        "seed_dir": str(tmp_path / "vms" / "demo" / "seed"),
        "log_path": str(tmp_path / "vms" / "demo" / "serial.log"),
        "pid": 999,
    }
    store.vm_dir("demo").mkdir(parents=True, exist_ok=True)
    store.vm_record_path("demo").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr("qhv.state.is_pid_running", lambda pid: True)

    loaded = store.load_vm("demo")

    assert loaded.spec.image_source == "cloud"
    assert loaded.spec.variant == "cloud"
    assert loaded.state == "running"
    assert loaded.last_error is None
    assert loaded.serial_mode == "log-only"
    assert loaded.serial_socket_port is None


def test_state_store_round_trip_preserves_image_source_and_variant(tmp_path) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    spec = VmSpec(name="demo", image_source="incus", distro="ubuntu", release="noble", variant="cloud")
    image = ImageRef(
        provider="ubuntu",
        release="noble",
        architecture="x86_64",
        filename="disk.qcow2",
        url="https://example.com/disk.qcow2",
        cache_path=tmp_path / "images" / "incus" / "ubuntu" / "noble" / "amd64" / "cloud" / "disk.qcow2",
        disk_format="qcow2",
    )
    record = VmRecord(
        name="demo",
        spec=spec,
        image=image,
        vm_dir=tmp_path / "vms" / "demo",
        disk_path=tmp_path / "vms" / "demo" / "demo.qcow2",
        seed_dir=tmp_path / "vms" / "demo" / "seed",
        log_path=tmp_path / "vms" / "demo" / "serial.log",
        pid=None,
    )

    store.save_vm(record)
    loaded = store.load_vm("demo")

    assert loaded.spec.image_source == "incus"
    assert loaded.spec.variant == "cloud"


def test_status_accepts_named_vm_argument(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    spec = VmSpec(name="demo")
    image = ImageRef(
        provider="ubuntu",
        release="24.04",
        architecture="x86_64",
        filename="ubuntu.img",
        url="https://example.com/ubuntu.img",
        cache_path=tmp_path / "images" / "ubuntu.img",
        disk_format="qcow2",
    )
    record = VmRecord(
        name="demo",
        spec=spec,
        image=image,
        vm_dir=tmp_path / "vms" / "demo",
        disk_path=tmp_path / "vms" / "demo" / "demo.qcow2",
        seed_dir=tmp_path / "vms" / "demo" / "seed",
        log_path=tmp_path / "vms" / "demo" / "serial.log",
        pid=None,
    )
    store.save_vm(record)

    runner = CliRunner()
    result = runner.invoke(app, ["status", "demo", "--state-dir", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 0
    assert "demo: stopped" in result.output


def test_list_vms_ignores_directories_without_vm_record(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    (store.vms_dir / "orphaned").mkdir(parents=True)

    assert store.list_vms() == []


def test_status_ignores_orphaned_vm_directories(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    (store.vms_dir / "orphaned").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(app, ["status", "--state-dir", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 0
    assert result.output.strip() == "No VMs found."


def test_delete_reports_incomplete_cleanup(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    spec = VmSpec(name="demo")
    image = ImageRef(
        provider="ubuntu",
        release="24.04",
        architecture="x86_64",
        filename="ubuntu.img",
        url="https://example.com/ubuntu.img",
        cache_path=tmp_path / "images" / "ubuntu.img",
        disk_format="qcow2",
    )
    record = VmRecord(
        name="demo",
        spec=spec,
        image=image,
        vm_dir=tmp_path / "vms" / "demo",
        disk_path=tmp_path / "vms" / "demo" / "demo.qcow2",
        seed_dir=tmp_path / "vms" / "demo" / "seed",
        log_path=tmp_path / "vms" / "demo" / "serial.log",
        pid=None,
    )
    store.save_vm(record)

    monkeypatch.setattr("qhv.cli.terminate_pid", lambda pid: None)
    monkeypatch.setattr(StateStore, "delete_vm", lambda self, name: False)

    runner = CliRunner()
    result = runner.invoke(app, ["delete", "demo", "--state-dir", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 1
    assert "Failed to fully delete VM 'demo'." in result.output
