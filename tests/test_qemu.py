import os
import subprocess
from pathlib import Path

from qhv.models import ImageRef, VmRecord, VmSpec
from qhv.qemu import QemuRunner, parse_port_forward, ssh_known_hosts_sink, terminate_pid


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
        serial_mode="socket",
        serial_socket_port=40222,
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
    assert "-chardev" in command
    assert any("socket,id=serial0" in part for part in command)
    assert any("port=40222" in part for part in command)
    assert any("logfile=" in part for part in command)


def test_build_command_uses_read_only_vvfat_seed_drive(tmp_path) -> None:
    record = _record(tmp_path)
    command = QemuRunner(qemu_system="qemu-system-x86_64", qemu_img="qemu-img").build_command(record)
    seed_drives = [part for part in command if "readonly=on,file.driver=vvfat" in part]
    assert seed_drives
    assert all("file.label=cidata" in part for part in seed_drives)
    assert all("file.floppy=on" in part for part in seed_drives)


def test_build_command_supports_legacy_log_only_serial(tmp_path: Path) -> None:
    record = _record(tmp_path)
    record.serial_mode = "log-only"
    record.serial_socket_port = None

    command = QemuRunner(qemu_system="qemu-system-x86_64", qemu_img="qemu-img").build_command(record)

    assert any(part.startswith("file:") for part in command)


def test_ssh_command_disables_host_key_persistence() -> None:
    spec = VmSpec(name="demo", ssh_port=2222)

    command = QemuRunner(qemu_system="qemu-system-x86_64", qemu_img="qemu-img").ssh_command(spec)

    assert command[:9] == [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"UserKnownHostsFile={ssh_known_hosts_sink()}",
        "-o",
        f"GlobalKnownHostsFile={ssh_known_hosts_sink()}",
        "-o",
        "LogLevel=ERROR",
    ]
    assert command[-3:] == [f"{spec.username}@127.0.0.1", "-p", str(spec.ssh_port)]


def test_ssh_known_hosts_sink_matches_platform() -> None:
    expected = "NUL" if os.name == "nt" else "/dev/null"
    assert ssh_known_hosts_sink() == expected


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
