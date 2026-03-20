from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qhv.cli import app
from qhv.host_checks import qemu_binary, qemu_supports_whpx
from qhv.state import StateStore

VM_TEST_ENV = "QHV_RUN_VM_TESTS"
VM_NAME = "vmtest"
SSH_PORT = 2299
BOOT_TIMEOUT_SECONDS = 300
SSH_USERNAME = "vmadmin"
README_VM_NAME = "demo"
README_SSH_PORT = 2222


pytestmark = pytest.mark.vm


def _require_real_vm_test_host() -> None:
    if os.environ.get(VM_TEST_ENV) != "1":
        pytest.skip(f"Set {VM_TEST_ENV}=1 to run real VM lifecycle tests.")
    if os.name != "nt":
        pytest.skip("Real VM lifecycle tests currently target Windows hosts only.")

    qemu_system = qemu_binary("qemu-system-x86_64")
    qemu_img = qemu_binary("qemu-img")
    if not qemu_system or not qemu_img:
        pytest.skip("QEMU binaries are not available on PATH.")
    if shutil.which("ssh") is None:
        pytest.skip("OpenSSH client is not available on PATH.")
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is not available on PATH.")
    if not qemu_supports_whpx(qemu_system):
        pytest.skip("This QEMU build does not report WHPX support.")


def _wait_for_ssh_banner(host: str, port: int, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5) as conn:
                conn.settimeout(5)
                banner = conn.recv(256)
                if banner.startswith(b"SSH-"):
                    return True
        except OSError:
            pass
        time.sleep(2)
    return False


def _port_is_available(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return False
    except OSError:
        return True


def _generate_ssh_keypair(key_path: Path) -> str:
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(key_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return key_path.with_suffix(".pub").read_text(encoding="utf-8").strip()


def _run_ssh_command(private_key_path: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "ssh",
            "-i",
            str(private_key_path),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(SSH_PORT),
            f"{SSH_USERNAME}@127.0.0.1",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_ssh_command_on_port(private_key_path: Path, port: int, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "ssh",
            "-i",
            str(private_key_path),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(port),
            f"{SSH_USERNAME}@127.0.0.1",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_create_and_destroy_lightweight_vm() -> None:
    _require_real_vm_test_host()

    runner = CliRunner()
    temp_root = Path.cwd() / "integration-runtime"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_dir = temp_root / f"qhv-vm-test-{os.getpid()}-{int(time.time())}"
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        state_dir = temp_dir / ".qhv-test"
        store = StateStore(state_dir)
        private_key_path = temp_dir / "id_ed25519"
        public_key = _generate_ssh_keypair(private_key_path)
        create_args = [
            "create",
            VM_NAME,
            "--state-dir",
            str(state_dir),
            "--cpus",
            "1",
            "--memory-mb",
            "1024",
            "--disk-size-gb",
            "8",
            "--ssh-port",
            str(SSH_PORT),
            "--username",
            SSH_USERNAME,
            "--ssh-public-key",
            public_key,
        ]

        try:
            result = runner.invoke(app, create_args, catch_exceptions=False)
            assert result.exit_code == 0, result.output
            assert store.has_vm(VM_NAME)

            status = runner.invoke(app, ["status", VM_NAME, "--state-dir", str(state_dir)], catch_exceptions=False)
            assert status.exit_code == 0, status.output
            assert "running" in status.output

            console_dump = runner.invoke(app, ["console", VM_NAME, "--state-dir", str(state_dir), "--dump"], catch_exceptions=False)
            assert console_dump.exit_code == 0, console_dump.output

            if not _wait_for_ssh_banner("127.0.0.1", SSH_PORT, BOOT_TIMEOUT_SECONDS):
                record = store.load_vm(VM_NAME)
                serial_log = record.log_path.read_text(encoding="utf-8", errors="replace") if record.log_path.exists() else ""
                pytest.fail(
                    "Timed out waiting for the guest SSH banner.\n"
                    f"Serial log:\n{serial_log[-4000:]}"
                )

            cloud_init_status = _run_ssh_command(private_key_path, "cloud-init status --wait")
            assert cloud_init_status.returncode == 0, cloud_init_status.stderr

            identity = _run_ssh_command(
                private_key_path,
                "test -f /var/lib/cloud/instance/boot-finished && id -un && uname -s",
            )
            assert identity.returncode == 0, identity.stderr
            assert SSH_USERNAME in identity.stdout
            assert "Linux" in identity.stdout
        finally:
            if store.has_vm(VM_NAME):
                delete_result = runner.invoke(
                    app,
                    ["delete", VM_NAME, "--state-dir", str(state_dir)],
                    catch_exceptions=False,
                )
                assert delete_result.exit_code == 0, delete_result.output
            assert not store.has_vm(VM_NAME)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_readme_user_flow_with_default_state_and_key_discovery() -> None:
    _require_real_vm_test_host()
    if not _port_is_available("127.0.0.1", README_SSH_PORT):
        pytest.skip(f"Default README SSH port {README_SSH_PORT} is already in use on this host.")

    runner = CliRunner()
    temp_root = Path.cwd() / "integration-runtime"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_dir = temp_root / f"qhv-readme-flow-{os.getpid()}-{int(time.time())}"
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = temp_dir / "workspace"
    home_dir = temp_dir / "home"
    ssh_dir = home_dir / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    key_path = ssh_dir / "id_ed25519"
    public_key = _generate_ssh_keypair(key_path)
    assert public_key.startswith("ssh-ed25519 ")

    original_cwd = Path.cwd()
    original_env = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    vm_created = False
    os.environ["HOME"] = str(home_dir)
    os.environ["USERPROFILE"] = str(home_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace_dir)
    try:
        result = runner.invoke(app, ["create", README_VM_NAME], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        vm_created = True
        assert (workspace_dir / ".qhv" / "vms" / README_VM_NAME / "vm.json").exists()

        status = runner.invoke(app, ["status", README_VM_NAME], catch_exceptions=False)
        assert status.exit_code == 0, status.output
        assert f"{README_VM_NAME}: running" in status.output

        show_cmd = runner.invoke(app, ["show-cmd", README_VM_NAME], catch_exceptions=False)
        assert show_cmd.exit_code == 0, show_cmd.output
        assert f"-name {README_VM_NAME}" in show_cmd.output

        console_dump = runner.invoke(app, ["console", README_VM_NAME, "--dump"], catch_exceptions=False)
        assert console_dump.exit_code == 0, console_dump.output

        if not _wait_for_ssh_banner("127.0.0.1", README_SSH_PORT, BOOT_TIMEOUT_SECONDS):
            serial_log = (workspace_dir / ".qhv" / "vms" / README_VM_NAME / "serial.log").read_text(
                encoding="utf-8",
                errors="replace",
            )
            raise AssertionError(
                "Timed out waiting for the README flow guest SSH banner.\n"
                f"Serial log:\n{serial_log[-4000:]}"
            )

        cloud_init_status = _run_ssh_command_on_port(key_path, README_SSH_PORT, "cloud-init status --wait")
        assert cloud_init_status.returncode == 0, cloud_init_status.stderr

        identity = _run_ssh_command_on_port(
            key_path,
            README_SSH_PORT,
            "test -f /var/lib/cloud/instance/boot-finished && id -un && uname -s",
        )
        assert identity.returncode == 0, identity.stderr
        assert SSH_USERNAME in identity.stdout
        assert "Linux" in identity.stdout

        delete_result = runner.invoke(app, ["delete", README_VM_NAME], catch_exceptions=False)
        assert delete_result.exit_code == 0, delete_result.output

        post_delete_status = runner.invoke(app, ["status"], catch_exceptions=False)
        assert post_delete_status.exit_code == 0, post_delete_status.output
        assert post_delete_status.output.strip() == "No VMs found."
        vm_created = False
    finally:
        if vm_created:
            runner.invoke(app, ["delete", README_VM_NAME], catch_exceptions=False)
        os.chdir(original_cwd)
        for name, value in original_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(temp_dir, ignore_errors=True)
