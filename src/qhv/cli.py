from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

import typer

from qhv.bootstrap import GuestBootstrapper, discover_default_public_key
from qhv.host_checks import collect_host_checks
from qhv.images import provider_for
from qhv.models import ForwardPort, VmRecord, VmSpec
from qhv.qemu import QemuRunner, format_command, is_pid_running, parse_port_forward, terminate_pid
from qhv.state import StateStore

app = typer.Typer(help="Provision Linux VMs on Windows with QEMU and WHPX.")
TROUBLESHOOTING_DOC = Path("docs/troubleshooting.md")


def _state_store(state_dir: Path | None) -> StateStore:
    store = StateStore(root=state_dir)
    store.ensure_layout()
    return store


def _default_release(distro: str) -> str:
    return "24.04" if distro == "ubuntu" else "43"


def _build_spec(
    name: str,
    distro: str,
    release: str | None,
    cpus: int,
    memory_mb: int,
    disk_size_gb: int,
    ssh_port: int,
    forwards: list[str],
    username: str,
    password: str | None,
    ssh_public_key: str | None,
) -> VmSpec:
    extra_forwards = []
    for value in forwards:
        host, guest = parse_port_forward(value)
        extra_forwards.append(ForwardPort(host=host, guest=guest))
    return VmSpec(
        name=name,
        distro=distro,
        release=release or _default_release(distro),
        cpus=cpus,
        memory_mb=memory_mb,
        disk_size_gb=disk_size_gb,
        ssh_port=ssh_port,
        forwarded_ports=extra_forwards,
        username=username,
        password=password,
        ssh_public_key=ssh_public_key,
    )


def _start_vm(runner: QemuRunner, record: VmRecord) -> int | None:
    try:
        return runner.start(record)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


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
    release: Annotated[str | None, typer.Option(help="Distribution release.")] = None,
    cpus: Annotated[int, typer.Option(help="Virtual CPUs.")] = 1,
    memory_mb: Annotated[int, typer.Option(help="Memory in MiB.")] = 1024,
    disk_size_gb: Annotated[int, typer.Option(help="Virtual disk size in GiB.")] = 40,
    ssh_port: Annotated[int, typer.Option(help="Host port forwarded to guest SSH.")] = 2222,
    forward: Annotated[list[str], typer.Option(help="Additional host:guest port forward(s).")] = [],
    username: Annotated[str, typer.Option(help="Guest username.")] = "vmadmin",
    password: Annotated[str | None, typer.Option(help="Guest password when no SSH key is available.")] = "vmadmin",
    ssh_public_key: Annotated[str | None, typer.Option(help="Inline SSH public key to inject.")] = None,
    state_dir: Annotated[Path | None, typer.Option(help="Override state directory.")] = None,
) -> None:
    """Download an image, generate cloud-init assets, create a VM disk, and boot it."""
    store = _state_store(state_dir)
    if store.has_vm(name):
        raise typer.BadParameter(f"VM '{name}' already exists.")

    spec = _build_spec(
        name=name,
        distro=distro,
        release=release,
        cpus=cpus,
        memory_mb=memory_mb,
        disk_size_gb=disk_size_gb,
        ssh_port=ssh_port,
        forwards=forward,
        username=username,
        password=password,
        ssh_public_key=ssh_public_key or discover_default_public_key(),
    )
    provider = provider_for(spec.distro)
    image = provider.ensure_downloaded(provider.resolve(store, spec.release, spec.architecture))

    vm_dir = store.vm_dir(name)
    seed_dir = vm_dir / "seed"
    disk_path = vm_dir / f"{name}.qcow2"
    log_path = vm_dir / "serial.log"
    bootstrapper = GuestBootstrapper()
    bootstrapper.write_seed(seed_dir, spec)

    runner = QemuRunner()
    runner.create_overlay_disk(image.cache_path, disk_path, spec.disk_size_gb, image.disk_format)
    record = VmRecord(
        name=name,
        spec=spec,
        image=image,
        vm_dir=vm_dir,
        disk_path=disk_path,
        seed_dir=seed_dir,
        log_path=log_path,
    )
    record.pid = _start_vm(runner, record)
    store.save_vm(record)

    typer.echo(f"Created VM '{name}' using {image.provider} {image.release}.")
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
        typer.echo(f"VM '{name}' is already running (pid {record.pid}).")
        return
    runner = QemuRunner()
    record.pid = _start_vm(runner, record)
    store.save_vm(record)
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
        running = is_pid_running(record.pid)
        typer.echo(
            f"{record.name}: {'running' if running else 'stopped'} | "
            f"{record.spec.distro} {record.spec.release} | ssh localhost:{record.spec.ssh_port}"
        )
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
