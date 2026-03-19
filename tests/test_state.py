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
    )
    store.save_vm(record)
    loaded = store.load_vm("demo")
    assert loaded.spec.name == "demo"
    assert loaded.pid == 123
    assert loaded.image.disk_format == "qcow2"


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
