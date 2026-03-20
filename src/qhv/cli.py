from __future__ import annotations

from collections.abc import Callable
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from qhv.bootstrap import GuestBootstrapper, discover_default_public_key
from qhv.host_checks import collect_host_checks
from qhv.images import resolver_for
from qhv.models import ForwardPort, VmRecord, VmSpec
from qhv.qemu import (
    QemuRunner,
    format_command,
    is_pid_running,
    is_tcp_endpoint_reachable,
    is_tcp_port_available,
    parse_port_forward,
    terminate_pid,
)
from qhv.state import StateStore

app = typer.Typer(help="Provision Linux VMs on Windows with QEMU and WHPX.")
TROUBLESHOOTING_DOC = Path("docs/troubleshooting.md")
DEFAULT_SSH_PORT = 2222
DEFAULT_SERIAL_SOCKET_PORT = 40222
SERIAL_POLL_INTERVAL_SECONDS = 0.5


def _state_store(state_dir: Path | None) -> StateStore:
    store = StateStore(root=state_dir)
    store.ensure_layout()
    return store


def _default_release(image_source: str, distro: str) -> str:
    provider = resolver_for(image_source, distro)
    if provider.default_release is None:
        raise ValueError(f"Explicit --release is required when --image-source {image_source} is used.")
    return provider.default_release


def _image_description(spec: VmSpec) -> str:
    if spec.image_source == "cloud":
        return f"{spec.distro} {spec.release}"
    return f"{spec.image_source} {spec.distro} {spec.release} {spec.variant}"


def _created_image_description(spec: VmSpec) -> str:
    if spec.image_source == "cloud":
        return f"{spec.distro} {spec.release}"
    return f"{spec.image_source} {spec.distro} {spec.release} ({spec.variant})"


def _parse_forwards(forwards: list[str]) -> list[ForwardPort]:
    extra_forwards = []
    for value in forwards:
        host, guest = parse_port_forward(value)
        extra_forwards.append(ForwardPort(host=host, guest=guest))
    return extra_forwards


def _reserved_port_reasons(store: StateStore, exclude_name: str | None = None) -> dict[int, str]:
    reasons: dict[int, str] = {}
    for vm_name in store.list_vms():
        if vm_name == exclude_name:
            continue
        try:
            record = store.load_vm(vm_name)
        except FileNotFoundError:
            continue
        for port in record.spec.all_forwarded_ports():
            reasons.setdefault(port.host, f"reserved by VM '{record.name}'")
        if record.serial_socket_port is not None:
            reasons.setdefault(record.serial_socket_port, f"reserved by VM '{record.name}'")
    return reasons


def _find_available_port(
    start_port: int,
    reserved_reasons: dict[int, str],
    local_reasons: dict[int, str] | None = None,
    bind_host: str = "0.0.0.0",
) -> tuple[int, list[tuple[int, str]]]:
    skip_reasons: list[tuple[int, str]] = []
    local = local_reasons or {}
    for port in range(start_port, 65536):
        if port in local:
            skip_reasons.append((port, local[port]))
            continue
        if port in reserved_reasons:
            skip_reasons.append((port, reserved_reasons[port]))
            continue
        if not is_tcp_port_available(port, bind_host=bind_host):
            skip_reasons.append((port, "busy on host"))
            continue
        return port, skip_reasons
    raise RuntimeError(f"No free TCP ports are available from {start_port} onward.")


def _format_auto_selected_port_message(name: str, selected_port: int, skip_reasons: list[tuple[int, str]]) -> str:
    if not skip_reasons:
        return f"Auto-selected SSH port {selected_port} for VM '{name}'."
    details = ", ".join(f"{port} {reason}" for port, reason in skip_reasons)
    return f"Auto-selected SSH port {selected_port} for VM '{name}' ({details})."


def _duplicate_host_port_error(name: str, host_port: int, first_guest: int, second_guest: int) -> str:
    return (
        f"VM '{name}' maps host port {host_port} more than once "
        f"(guest ports {first_guest} and {second_guest})."
    )


def _resolve_create_ssh_port(
    store: StateStore,
    name: str,
    requested_ssh_port: int | None,
    forwarded_ports: list[ForwardPort],
) -> tuple[int, list[tuple[int, str]]]:
    reserved_reasons = _reserved_port_reasons(store)
    local_reasons = {port.host: "reserved by another forward on this VM" for port in forwarded_ports}
    if requested_ssh_port is None:
        try:
            return _find_available_port(
                DEFAULT_SSH_PORT,
                reserved_reasons=reserved_reasons,
                local_reasons=local_reasons,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc

    if requested_ssh_port in local_reasons:
        raise typer.BadParameter(
            f"VM '{name}' already uses host port {requested_ssh_port} in another forward rule."
        )
    reason = reserved_reasons.get(requested_ssh_port)
    if reason is not None:
        raise typer.BadParameter(
            f"Host port {requested_ssh_port} is already {reason}. Choose a different --ssh-port."
        )
    if not is_tcp_port_available(requested_ssh_port):
        raise typer.BadParameter(
            f"Host port {requested_ssh_port} is already in use on this host. Choose a different --ssh-port."
        )
    return requested_ssh_port, []


def _validate_create_forwarded_ports(store: StateStore, spec: VmSpec) -> None:
    reserved_reasons = _reserved_port_reasons(store)
    seen_ports = {spec.ssh_port: 22}
    for port in spec.forwarded_ports:
        previous_guest = seen_ports.get(port.host)
        if previous_guest is not None:
            raise typer.BadParameter(_duplicate_host_port_error(spec.name, port.host, previous_guest, port.guest))
        reason = reserved_reasons.get(port.host)
        if reason is not None:
            raise typer.BadParameter(
                f"Host port {port.host} is already {reason}. Choose a different --forward value."
            )
        if not is_tcp_port_available(port.host):
            raise typer.BadParameter(
                f"Host port {port.host} is already in use on this host. Choose a different --forward value."
            )
        seen_ports[port.host] = port.guest


def _ensure_runtime_host_ports_available(record: VmRecord) -> None:
    seen_ports: dict[int, int] = {}
    for port in record.spec.all_forwarded_ports():
        previous_guest = seen_ports.get(port.host)
        if previous_guest is not None:
            raise RuntimeError(_duplicate_host_port_error(record.name, port.host, previous_guest, port.guest))
        if not is_tcp_port_available(port.host):
            raise RuntimeError(
                f"Cannot start VM '{record.name}': host port {port.host} for guest port {port.guest} is already in use."
            )
        seen_ports[port.host] = port.guest


def _build_spec(
    name: str,
    image_source: str,
    distro: str,
    release: str | None,
    variant: str,
    cpus: int,
    memory_mb: int,
    disk_size_gb: int,
    ssh_port: int,
    forwards: list[str],
    username: str,
    password: str | None,
    ssh_public_key: str | None,
) -> VmSpec:
    return VmSpec(
        name=name,
        image_source=image_source.lower(),
        distro=distro,
        release=release or _default_release(image_source, distro),
        variant=variant.lower(),
        cpus=cpus,
        memory_mb=memory_mb,
        disk_size_gb=disk_size_gb,
        ssh_port=ssh_port,
        forwarded_ports=_parse_forwards(forwards),
        username=username,
        password=password,
        ssh_public_key=ssh_public_key,
    )


def _ensure_vm_name_available(store: StateStore, name: str) -> None:
    if store.has_vm(name):
        raise typer.BadParameter(f"VM '{name}' already exists.")
    vm_dir = store.vm_dir(name)
    if vm_dir.exists():
        raise typer.BadParameter(
            f"VM '{name}' has a stale directory at {vm_dir}. Run `qhv prune` or remove it manually before recreating it."
        )


def _assign_serial_transport(store: StateStore, record: VmRecord) -> None:
    if record.serial_mode == "socket" and record.serial_socket_port is not None:
        reserved = _reserved_port_reasons(store, exclude_name=record.name)
        if (
            record.serial_socket_port not in reserved
            and record.serial_socket_port not in {port.host for port in record.spec.all_forwarded_ports()}
            and is_tcp_port_available(record.serial_socket_port, bind_host="127.0.0.1")
        ):
            return

    reserved = _reserved_port_reasons(store, exclude_name=record.name)
    local_reasons = {port.host: "reserved by another forward on this VM" for port in record.spec.all_forwarded_ports()}
    try:
        serial_port, _ = _find_available_port(
            DEFAULT_SERIAL_SOCKET_PORT,
            reserved_reasons=reserved,
            local_reasons=local_reasons,
            bind_host="127.0.0.1",
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    record.serial_mode = "socket"
    record.serial_socket_port = serial_port


def _record_runtime_state(record: VmRecord) -> str:
    if is_pid_running(record.pid):
        return "running"
    if record.state == "failed":
        return "failed"
    if record.state == "starting":
        return "starting"
    return "stopped"


def _launch_vm(
    store: StateStore,
    runner: QemuRunner,
    record: VmRecord,
    progress: Callable[[str], None] | None = None,
) -> int | None:
    try:
        pid = runner.start(record, progress=progress)
    except RuntimeError as exc:
        record.pid = None
        record.state = "failed"
        record.last_error = str(exc)
        store.save_vm(record)
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    record.pid = pid
    record.state = "running"
    record.last_error = None
    store.save_vm(record)
    return pid


def _serial_socket_available(record: VmRecord) -> bool:
    if record.serial_mode != "socket" or record.serial_socket_port is None:
        return False
    return is_tcp_endpoint_reachable("127.0.0.1", record.serial_socket_port)


def _emit_stream_text(text: str) -> None:
    if not text:
        return
    sys.stdout.write(text)
    sys.stdout.flush()


def _dump_serial_log(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    _emit_stream_text(path.read_text(encoding="utf-8", errors="replace"))


def _follow_serial_log(path: Path) -> None:
    while not path.exists():
        time.sleep(SERIAL_POLL_INTERVAL_SECONDS)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)
        while True:
            chunk = handle.read()
            if chunk:
                _emit_stream_text(chunk)
            else:
                time.sleep(SERIAL_POLL_INTERVAL_SECONDS)


def _stream_serial_socket(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                return
            _emit_stream_text(chunk.decode("utf-8", errors="replace"))


@app.command()
def check() -> None:
    """Validate the local Windows host for WHPX-backed QEMU."""
    result = collect_host_checks()
    for item in result.items:
        status = "OK" if item.ok else "FAIL"
        typer.echo(f"[{status}] {item.name}: {item.details}")
        if not item.ok and item.remediation:
            typer.echo(f"  Remedy: {item.remediation}")
    if not result.ok:
        typer.echo(f"See {TROUBLESHOOTING_DOC} for setup and remediation steps.")
    raise typer.Exit(code=0 if result.ok else 1)


@app.command()
def create(
    name: str,
    distro: Annotated[str, typer.Option(help="Image provider to use.")] = "ubuntu",
    image_source: Annotated[str, typer.Option(help="Image source to use: cloud or incus.")] = "cloud",
    release: Annotated[str | None, typer.Option(help="Distribution release.")] = None,
    variant: Annotated[str, typer.Option(help="Image variant. Incus-backed images default to 'cloud'.")] = "cloud",
    cpus: Annotated[int, typer.Option(help="Virtual CPUs.")] = 1,
    memory_mb: Annotated[int, typer.Option(help="Memory in MiB.")] = 1024,
    disk_size_gb: Annotated[int, typer.Option(help="Virtual disk size in GiB.")] = 40,
    ssh_port: Annotated[
        int | None,
        typer.Option(help="Host port forwarded to guest SSH. Defaults to the first free port starting at 2222."),
    ] = None,
    forward: Annotated[list[str], typer.Option(help="Additional host:guest port forward(s).")] = [],
    username: Annotated[str, typer.Option(help="Guest username.")] = "vmadmin",
    password: Annotated[str | None, typer.Option(help="Guest password when no SSH key is available.")] = "vmadmin",
    ssh_public_key: Annotated[str | None, typer.Option(help="Inline SSH public key to inject.")] = None,
    state_dir: Annotated[Path | None, typer.Option(help="Override state directory.")] = None,
) -> None:
    """Download an image, generate cloud-init assets, create a VM disk, and boot it."""
    store = _state_store(state_dir)
    _ensure_vm_name_available(store, name)
    normalized_image_source = image_source.lower()
    if normalized_image_source == "incus" and release is None:
        raise typer.BadParameter("Explicit --release is required when --image-source incus is used.")

    forwarded_ports = _parse_forwards(forward)
    resolved_ssh_port, skip_reasons = _resolve_create_ssh_port(
        store,
        name,
        ssh_port,
        forwarded_ports,
    )
    spec = _build_spec(
        name=name,
        image_source=normalized_image_source,
        distro=distro,
        release=release,
        variant=variant,
        cpus=cpus,
        memory_mb=memory_mb,
        disk_size_gb=disk_size_gb,
        ssh_port=resolved_ssh_port,
        forwards=forward,
        username=username,
        password=password,
        ssh_public_key=ssh_public_key or discover_default_public_key(),
    )
    _validate_create_forwarded_ports(store, spec)
    if ssh_port is None:
        typer.echo(_format_auto_selected_port_message(name, spec.ssh_port, skip_reasons))
    typer.echo(f"Ensuring {_image_description(spec)} image is available...")
    try:
        provider = resolver_for(spec.image_source, spec.distro)
        image = provider.ensure_downloaded(
            provider.resolve(store, spec.release, spec.architecture, variant=spec.variant)
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    vm_dir = store.vm_dir(name)
    seed_dir = vm_dir / "seed"
    disk_path = vm_dir / f"{name}.qcow2"
    log_path = vm_dir / "serial.log"
    bootstrapper = GuestBootstrapper()
    typer.echo(f"Generating cloud-init seed for VM '{name}'...")
    bootstrapper.write_seed(seed_dir, spec)

    runner = QemuRunner()
    typer.echo(f"Creating overlay disk for VM '{name}'...")
    runner.create_overlay_disk(image.cache_path, disk_path, spec.disk_size_gb, image.disk_format)
    record = VmRecord(
        name=name,
        spec=spec,
        image=image,
        vm_dir=vm_dir,
        disk_path=disk_path,
        seed_dir=seed_dir,
        log_path=log_path,
        state="starting",
        serial_mode="socket",
    )
    _assign_serial_transport(store, record)
    store.save_vm(record)
    _launch_vm(store, runner, record, progress=typer.echo)

    typer.echo(f"Created VM '{name}' using {_created_image_description(spec)}.")
    typer.echo(f"SSH: ssh {spec.username}@127.0.0.1 -p {spec.ssh_port}")
    if spec.ssh_public_key is None:
        typer.echo(f"Password SSH enabled for user '{spec.username}' with password '{spec.password}'.")


@app.command()
def start(
    name: str,
    state_dir: Annotated[Path | None, typer.Option(help="Override state directory.")] = None,
) -> None:
    """Start an existing VM."""
    store = _state_store(state_dir)
    record = store.load_vm(name)
    if is_pid_running(record.pid):
        if record.state != "running":
            record.state = "running"
            record.last_error = None
            store.save_vm(record)
        typer.echo(f"VM '{name}' is already running (pid {record.pid}).")
        return
    try:
        _ensure_runtime_host_ports_available(record)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    _assign_serial_transport(store, record)
    record.pid = None
    record.state = "starting"
    record.last_error = None
    store.save_vm(record)
    runner = QemuRunner()
    typer.echo(f"Starting VM '{name}'...")
    _launch_vm(store, runner, record, progress=typer.echo)
    typer.echo(f"Started VM '{name}' (pid {record.pid}).")


@app.command()
def status(
    name: Annotated[str | None, typer.Argument(help="Optional VM name to inspect.")] = None,
    state_dir: Annotated[Path | None, typer.Option(help="Override state directory.")] = None,
) -> None:
    """Show VM status."""
    store = _state_store(state_dir)
    names = [name] if name else store.list_vms()
    if not names:
        typer.echo("No VMs found.")
        return
    displayed = False
    for vm_name in names:
        try:
            record = store.load_vm(vm_name)
        except FileNotFoundError:
            continue
        state = _record_runtime_state(record)
        image_label = f"{record.spec.distro} {record.spec.release}"
        if record.spec.image_source != "cloud" or record.spec.variant != "cloud":
            image_label = f"{image_label} [{record.spec.image_source}/{record.spec.variant}]"
        line = f"{record.name}: {state} | {image_label} | ssh localhost:{record.spec.ssh_port}"
        if state == "failed" and record.last_error:
            line = f"{line} | error: {record.last_error.splitlines()[0]}"
        typer.echo(line)
        displayed = True
    if not displayed:
        typer.echo("No VMs found.")


@app.command()
def ssh(
    name: str,
    state_dir: Annotated[Path | None, typer.Option(help="Override state directory.")] = None,
    print_only: Annotated[bool, typer.Option("--print", help="Print the SSH command instead of executing it.")] = False,
) -> None:
    """Connect to a VM over forwarded SSH."""
    store = _state_store(state_dir)
    record = store.load_vm(name)
    runner = QemuRunner()
    command = runner.ssh_command(record.spec)
    if print_only:
        typer.echo(format_command(command))
        return
    completed = subprocess.run(command, check=False)
    raise typer.Exit(code=completed.returncode)


@app.command()
def console(
    name: str,
    state_dir: Annotated[Path | None, typer.Option(help="Override state directory.")] = None,
    tail_only: Annotated[bool, typer.Option(help="Follow serial.log even when live serial attach is available.")] = False,
    dump: Annotated[bool, typer.Option(help="Print the current serial output and exit.")] = False,
) -> None:
    """Inspect a VM serial console for troubleshooting."""
    store = _state_store(state_dir)
    record = store.load_vm(name)
    try:
        if dump:
            _dump_serial_log(record.log_path)
            return
        if not tail_only and record.serial_mode == "socket" and record.serial_socket_port is not None and is_pid_running(record.pid):
            try:
                _stream_serial_socket(record.serial_socket_port)
                return
            except OSError:
                if record.log_path.exists():
                    typer.echo(
                        f"Falling back to {record.log_path} because live serial attach to localhost:{record.serial_socket_port} failed.",
                        err=True,
                    )
                else:
                    raise
        _follow_serial_log(record.log_path)
    except FileNotFoundError:
        typer.echo(f"No serial log exists for VM '{name}' yet.", err=True)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        raise typer.Exit(code=0)
    except OSError as exc:
        typer.echo(f"Unable to open the serial console for VM '{name}': {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def delete(
    name: str,
    state_dir: Annotated[Path | None, typer.Option(help="Override state directory.")] = None,
) -> None:
    """Terminate and remove a VM and its local state."""
    store = _state_store(state_dir)
    record = store.load_vm(name)
    terminate_pid(record.pid)
    deleted = store.delete_vm(name)
    if not deleted:
        typer.echo(
            f"Failed to fully delete VM '{name}'. Remove {record.vm_dir} after any open handles are released.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Deleted VM '{name}'.")


@app.command()
def prune(
    state_dir: Annotated[Path | None, typer.Option(help="Override state directory.")] = None,
) -> None:
    """Remove stale VM directories and failed VM records."""
    store = _state_store(state_dir)
    removed = 0
    kept_active = 0

    for vm_dir in store.list_vm_dirs():
        if not (vm_dir / "vm.json").exists():
            if store.delete_vm(vm_dir.name):
                typer.echo(f"Removed stale VM directory '{vm_dir.name}'.")
                removed += 1

    for name in store.list_vms():
        try:
            record = store.load_vm(name)
        except FileNotFoundError:
            continue
        if is_pid_running(record.pid):
            kept_active += 1
            typer.echo(f"Kept active VM '{record.name}' (pid {record.pid}).")
            continue
        removable = False
        if record.state == "failed":
            removable = True
        elif record.state in {"starting", "running"} and not _serial_socket_available(record):
            removable = True
        if not removable:
            continue
        if store.delete_vm(record.name):
            typer.echo(f"Removed stale VM '{record.name}'.")
            removed += 1

    typer.echo(f"Pruned {removed} stale VM artifact(s); kept {kept_active} active VM(s).")


@app.command("show-cmd")
def show_cmd(
    name: str,
    state_dir: Annotated[Path | None, typer.Option(help="Override state directory.")] = None,
) -> None:
    """Print the QEMU command that will be used for an existing VM."""
    store = _state_store(state_dir)
    record = store.load_vm(name)
    runner = QemuRunner()
    typer.echo(format_command(runner.build_command(record)))
