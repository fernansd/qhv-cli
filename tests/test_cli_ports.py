from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from qhv.cli import app
from qhv.models import ImageRef, VmRecord, VmSpec
from qhv.state import StateStore


def _image_ref(tmp_path: Path) -> ImageRef:
    return ImageRef(
        provider="ubuntu",
        release="24.04",
        architecture="x86_64",
        filename="ubuntu.img",
        url="https://example.com/ubuntu.img",
        cache_path=tmp_path / "images" / "ubuntu.img",
        disk_format="qcow2",
    )


def _save_vm(store: StateStore, tmp_path: Path, name: str, ssh_port: int) -> VmRecord:
    record = VmRecord(
        name=name,
        spec=VmSpec(name=name, ssh_port=ssh_port),
        image=_image_ref(tmp_path),
        vm_dir=store.vm_dir(name),
        disk_path=store.vm_dir(name) / f"{name}.qcow2",
        seed_dir=store.vm_dir(name) / "seed",
        log_path=store.vm_dir(name) / "serial.log",
        pid=None,
        serial_mode="socket",
        serial_socket_port=40222 + ssh_port,
    )
    store.save_vm(record)
    return record


class _FakeProvider:
    default_release = "24.04"

    def __init__(self, image: ImageRef) -> None:
        self.image = image

    def resolve(self, store: StateStore, release: str, architecture: str, variant: str = "cloud") -> ImageRef:
        return self.image

    def ensure_downloaded(self, image: ImageRef) -> ImageRef:
        return image


class _FakeBootstrapper:
    def write_seed(self, seed_dir: Path, spec: VmSpec) -> None:
        seed_dir.mkdir(parents=True, exist_ok=True)


class _FakeCreateRunner:
    def create_overlay_disk(self, base_image: Path, destination: Path, size_gb: int, base_format: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()

    def start(self, record: VmRecord, progress=None) -> int:
        if progress is not None:
            progress(f"Starting QEMU for VM '{record.name}'...")
            progress(f"Waiting for SSH on localhost:{record.spec.ssh_port}...")
        return 4321


def test_create_auto_selects_next_ssh_port_and_reports_progress(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    _save_vm(store, tmp_path, "first", 2222)

    monkeypatch.setattr(
        "qhv.cli.is_tcp_port_available",
        lambda port, bind_host="0.0.0.0": False if port == 2223 else True,
    )
    monkeypatch.setattr("qhv.cli.resolver_for", lambda image_source, distro: _FakeProvider(_image_ref(tmp_path)))
    monkeypatch.setattr("qhv.cli.GuestBootstrapper", _FakeBootstrapper)
    monkeypatch.setattr("qhv.cli.QemuRunner", _FakeCreateRunner)

    runner = CliRunner()
    result = runner.invoke(app, ["create", "second", "--state-dir", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    created = store.load_vm("second")
    assert created.spec.ssh_port == 2224
    assert "Auto-selected SSH port 2224 for VM 'second' (2222 reserved by VM 'first', 2223 busy on host)." in result.output
    assert "Ensuring ubuntu 24.04 image is available..." in result.output
    assert "Generating cloud-init seed for VM 'second'..." in result.output
    assert "Creating overlay disk for VM 'second'..." in result.output
    assert "Waiting for SSH on localhost:2224..." in result.output
    assert created.state == "running"
    assert created.serial_mode == "socket"
    assert created.serial_socket_port is not None


def test_create_rejects_explicit_ssh_port_reserved_by_existing_vm(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    _save_vm(store, tmp_path, "first", 2222)

    monkeypatch.setattr("qhv.cli.resolver_for", lambda image_source, distro: _FakeProvider(_image_ref(tmp_path)))

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["create", "second", "--state-dir", str(tmp_path), "--ssh-port", "2222"],
    )

    assert result.exit_code == 2
    assert "reserved by VM 'first'" in result.output


def test_create_rejects_stale_vm_directory_without_record(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    store.vm_dir("stale").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(app, ["create", "stale", "--state-dir", str(tmp_path)])

    assert result.exit_code == 2
    assert "Run `qhv prune` or remove it manually" in result.output


def test_failed_create_persists_failed_vm_record(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()

    class _FailingRunner(_FakeCreateRunner):
        def start(self, record: VmRecord, progress=None) -> int:
            raise RuntimeError("boom")

    monkeypatch.setattr("qhv.cli.is_tcp_port_available", lambda port, bind_host="0.0.0.0": True)
    monkeypatch.setattr("qhv.cli.resolver_for", lambda image_source, distro: _FakeProvider(_image_ref(tmp_path)))
    monkeypatch.setattr("qhv.cli.GuestBootstrapper", _FakeBootstrapper)
    monkeypatch.setattr("qhv.cli.QemuRunner", _FailingRunner)

    runner = CliRunner()
    result = runner.invoke(app, ["create", "broken", "--state-dir", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 1
    record = store.load_vm("broken")
    assert record.state == "failed"
    assert record.last_error == "boom"
    assert record.serial_mode == "socket"
    assert record.serial_socket_port is not None


def test_create_requires_explicit_release_for_incus_source(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["create", "demo", "--image-source", "incus", "--state-dir", str(tmp_path)])

    assert result.exit_code == 2
    assert "Explicit --release is required when --image-source incus is" in result.output


def test_create_with_incus_source_persists_source_and_variant(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    captured: dict[str, str] = {}

    class _IncusProvider(_FakeProvider):
        default_release = None

        def resolve(self, store: StateStore, release: str, architecture: str, variant: str = "cloud") -> ImageRef:
            captured["release"] = release
            captured["architecture"] = architecture
            captured["variant"] = variant
            return self.image

    monkeypatch.setattr("qhv.cli.is_tcp_port_available", lambda port, bind_host="0.0.0.0": True)
    monkeypatch.setattr(
        "qhv.cli.resolver_for",
        lambda image_source, distro: _IncusProvider(
            ImageRef(
                provider=distro,
                release="noble",
                architecture="x86_64",
                filename="disk.qcow2",
                url="https://example.com/disk.qcow2",
                cache_path=tmp_path / "images" / "incus" / distro / "noble" / "amd64" / "cloud" / "disk.qcow2",
                disk_format="qcow2",
            )
        ),
    )
    monkeypatch.setattr("qhv.cli.GuestBootstrapper", _FakeBootstrapper)
    monkeypatch.setattr("qhv.cli.QemuRunner", _FakeCreateRunner)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "create",
            "incus-demo",
            "--image-source",
            "incus",
            "--distro",
            "ubuntu",
            "--release",
            "noble",
            "--variant",
            "cloud",
            "--state-dir",
            str(tmp_path),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    created = store.load_vm("incus-demo")
    assert created.spec.image_source == "incus"
    assert created.spec.variant == "cloud"
    assert captured == {
        "release": "noble",
        "architecture": "x86_64",
        "variant": "cloud",
    }
    assert "Ensuring incus ubuntu noble cloud image is available..." in result.output
    assert "Created VM 'incus-demo' using incus ubuntu noble (cloud)." in result.output


def test_start_reports_progress_messages(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    _save_vm(store, tmp_path, "demo", 2222)

    class _FakeStartRunner:
        def start(self, record: VmRecord, progress=None) -> int:
            assert progress is not None
            progress(f"Starting QEMU for VM '{record.name}'...")
            progress(f"Waiting for SSH on localhost:{record.spec.ssh_port}...")
            return 999

    monkeypatch.setattr("qhv.cli.is_pid_running", lambda pid: False)
    monkeypatch.setattr("qhv.cli.is_tcp_port_available", lambda port, bind_host="0.0.0.0": True)
    monkeypatch.setattr("qhv.cli.QemuRunner", _FakeStartRunner)

    runner = CliRunner()
    result = runner.invoke(app, ["start", "demo", "--state-dir", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "Starting VM 'demo'..." in result.output
    assert "Starting QEMU for VM 'demo'..." in result.output
    assert "Waiting for SSH on localhost:2222..." in result.output
    assert "Started VM 'demo' (pid 999)." in result.output
    assert store.load_vm("demo").pid == 999
    assert store.load_vm("demo").state == "running"


def test_start_fails_before_launch_when_host_port_is_busy(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    _save_vm(store, tmp_path, "demo", 2222)

    class _UnexpectedRunner:
        def __init__(self) -> None:
            raise AssertionError("QemuRunner should not be constructed when host ports are unavailable.")

    monkeypatch.setattr("qhv.cli.is_pid_running", lambda pid: False)
    monkeypatch.setattr("qhv.cli.is_tcp_port_available", lambda port, bind_host="0.0.0.0": False)
    monkeypatch.setattr("qhv.cli.QemuRunner", _UnexpectedRunner)

    runner = CliRunner()
    result = runner.invoke(app, ["start", "demo", "--state-dir", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 1
    assert "Cannot start VM 'demo': host port 2222 for guest port 22 is already in use." in result.output


def test_console_dump_prints_existing_serial_log(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    record = _save_vm(store, tmp_path, "demo", 2222)
    record.serial_mode = "log-only"
    record.serial_socket_port = None
    record.log_path.parent.mkdir(parents=True, exist_ok=True)
    record.log_path.write_text("boot ok", encoding="utf-8")
    store.save_vm(record)

    runner = CliRunner()
    result = runner.invoke(app, ["console", "demo", "--state-dir", str(tmp_path), "--dump"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "boot ok" in result.output


def test_prune_removes_failed_records_and_orphan_dirs(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    store.ensure_layout()
    orphan = store.vms_dir / "orphaned"
    orphan.mkdir(parents=True)
    record = _save_vm(store, tmp_path, "failed-vm", 2222)
    record.state = "failed"
    record.pid = None
    store.save_vm(record)

    monkeypatch.setattr("qhv.cli.is_pid_running", lambda pid: False)
    monkeypatch.setattr("qhv.state.is_pid_running", lambda pid: False)

    runner = CliRunner()
    result = runner.invoke(app, ["prune", "--state-dir", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "Removed stale VM directory 'orphaned'." in result.output
    assert "Removed stale VM 'failed-vm'." in result.output
    assert not orphan.exists()
    assert not store.has_vm("failed-vm")
