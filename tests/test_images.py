from qhv.images import FedoraImageProvider, UbuntuImageProvider, provider_for
from qhv.state import StateStore


def test_ubuntu_provider_uses_current_cloud_image(tmp_path) -> None:
    store = StateStore(tmp_path)
    image = UbuntuImageProvider().resolve(store, "24.04", "x86_64")
    assert image.url.endswith("/releases/server/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img")
    assert image.disk_format == "raw"


def test_fedora_provider_uses_cloud_qcow2(tmp_path) -> None:
    store = StateStore(tmp_path)
    image = FedoraImageProvider().resolve(store, "43", "x86_64")
    assert image.filename == "Fedora-Cloud-Base-Generic-43-1.6.x86_64.qcow2"
    assert image.disk_format == "qcow2"


def test_provider_for_rejects_unknown_provider() -> None:
    try:
        provider_for("debian")
    except ValueError as exc:
        assert "Unsupported image provider" in str(exc)
    else:
        raise AssertionError("Expected provider_for to reject unsupported provider.")
