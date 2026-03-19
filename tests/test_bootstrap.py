from qhv.bootstrap import GuestBootstrapper, render_network_config, render_user_data
from qhv.models import VmSpec


def test_render_user_data_uses_ssh_key() -> None:
    spec = VmSpec(name="demo", ssh_public_key="ssh-ed25519 AAAA test@example")
    user_data = render_user_data(spec)
    assert "  - name: vmadmin\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    groups: [adm, sudo, wheel]\n    shell: /bin/bash\n    ssh_authorized_keys:" in user_data
    assert "packages:\n  - qemu-guest-agent\nssh_pwauth: false\n" in user_data
    assert "ssh_pwauth: false" in user_data
    assert "lock_passwd: false" not in user_data


def test_render_user_data_uses_password_fallback() -> None:
    spec = VmSpec(name="demo", password="vmadmin", ssh_public_key=None)
    user_data = render_user_data(spec)
    assert "  - name: vmadmin\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    groups: [adm, sudo, wheel]\n    shell: /bin/bash\n    lock_passwd: false" in user_data
    assert 'password: "vmadmin"' in user_data
    assert "ssh_pwauth: true" in user_data
    assert "ssh_authorized_keys" not in user_data


def test_bootstrapper_writes_seed_files(tmp_path) -> None:
    spec = VmSpec(name="demo")
    bootstrapper = GuestBootstrapper()
    seed_dir = bootstrapper.write_seed(tmp_path / "seed", spec)
    assert (seed_dir / "user-data").exists()
    assert (seed_dir / "meta-data").exists()
    assert (seed_dir / "network-config").exists()


def test_render_network_config_matches_default_virtio_nic_names() -> None:
    network_config = render_network_config()
    assert 'name: "en*"' in network_config
    assert "dhcp4: true" in network_config
