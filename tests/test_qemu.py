import subprocess
from pathlib import Path

from qhv.models import ImageRef, VmRecord, VmSpec
from qhv.qemu import QemuRunner, parse_port_forward, terminate_pid


def _record(tmp_path: Path) -> VmRecord:
    spec = VmSpec(name="demo", ssh_port=2222)
    image = ImageRef(
        provider="ubuntu",
        release="24.04",
        architecture="x86_64",
        filename="ubuntu.img",
        url="https://example.com/ubuntu.img",
        cache_path=tmp_path / "ubuntu.img",
        disk_format="raw",
    )
    return VmRecord(
        name="demo",
        spec=spec,
        image=image,
        vm_dir=tmp_path / "demo",
        disk_path=tmp_path / "demo" / "demo.qcow2",
        seed_dir=tmp_path / "demo" / "seed",
        log_path=tmp_path / "demo" / "serial.log",
    )


def test_parse_port_forward() -> None:
    assert parse_port_forward("8080:80") == (8080, 80)


def test_build_command_contains_whpx_qemu64_and_ssh_forward(tmp_path) -> None:
    record = _record(tmp_path)
    command = QemuRunner(qemu_system="qemu-system-x86_64", qemu_img="qemu-img").build_command(record)
    assert "-accel" in command
    assert "whpx" in command
    assert "qemu64" in command
    assert any("hostfwd=tcp::2222-:22" in part for part in command)


def test_build_command_uses_read_only_vvfat_seed_drive(tmp_path) -> None:
    record = _record(tmp_path)
    command = QemuRunner(qemu_system="qemu-system-x86_64", qemu_img="qemu-img").build_command(record)
    seed_drives = [part for part in command if "readonly=on,file.driver=vvfat" in part]
    assert seed_drives
    assert all("file.label=cidata" in part for part in seed_drives)
    assert all("file.floppy=on" in part for part in seed_drives)


def test_start_fails_if_qemu_exits_during_startup(tmp_path, monkeypatch) -> None:
    record = _record(tmp_path)
    record.vm_dir.mkdir(parents=True, exist_ok=True)

    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 1234
            self._polls = iter([None, 1])

        def poll(self):
            return next(self._polls)

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(QemuRunner, "_ssh_banner_available", lambda self, port: False)
    monkeypatch.setattr("qhv.qemu.time.sleep", lambda seconds: None)

    runner = QemuRunner(
        qemu_system="qemu-system-x86_64",
        qemu_img="qemu-img",
        startup_timeout_seconds=5,
        startup_poll_interval_seconds=0,
    )

    try:
        runner.start(record)
    except RuntimeError as exc:
        assert "QEMU exited during startup with code 1." in str(exc)
        assert "stderr:" in str(exc)
        assert "serial:" in str(exc)
    else:
        raise AssertionError("Expected startup failure when QEMU exits during readiness polling.")


def test_terminate_pid_suppresses_taskkill_output_on_windows(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, stdout="ERROR", stderr="ERROR")

    monkeypatch.setattr(subprocess, "run", fake_run)

    terminate_pid(4321)

    assert calls
    command, kwargs = calls[0]
    assert command == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
