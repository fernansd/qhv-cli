from qhv.images import FedoraImageProvider, IncusPublicImageProvider, UbuntuImageProvider, provider_for, resolver_for
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
    assert image.url.endswith("/releases/43/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-43-1.6.x86_64.qcow2")
    assert image.disk_format == "qcow2"


def test_fedora_42_provider_uses_release_specific_compose_id(tmp_path) -> None:
    store = StateStore(tmp_path)
    image = FedoraImageProvider().resolve(store, "42", "x86_64")
    assert image.filename == "Fedora-Cloud-Base-Generic-42-1.1.x86_64.qcow2"
    assert image.url.endswith("/releases/42/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-42-1.1.x86_64.qcow2")


def test_fedora_provider_rejects_unknown_release(tmp_path) -> None:
    store = StateStore(tmp_path)
    try:
        FedoraImageProvider().resolve(store, "41", "x86_64")
    except ValueError as exc:
        assert "Unsupported Fedora cloud release" in str(exc)
    else:
        raise AssertionError("Expected FedoraImageProvider to reject unsupported releases.")


def test_provider_for_rejects_unknown_provider() -> None:
    try:
        provider_for("debian")
    except ValueError as exc:
        assert "Unsupported image provider" in str(exc)
    else:
        raise AssertionError("Expected provider_for to reject unsupported provider.")


def test_resolver_for_supports_incus_public_images() -> None:
    provider = resolver_for("incus", "ubuntu")
    assert isinstance(provider, IncusPublicImageProvider)
    assert provider.name == "ubuntu"


def test_incus_provider_selects_latest_matching_vm_qcow2(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    provider = IncusPublicImageProvider("ubuntu")
    responses = {
        "https://images.linuxcontainers.org/streams/v1/index.json": {
            "index": {
                "images": {
                    "path": "streams/v1/images.json",
                }
            }
        },
        "https://images.linuxcontainers.org/streams/v1/images.json": {
            "products": {
                "ubuntu:noble:amd64:cloud": {
                    "os": "Ubuntu",
                    "release": "noble",
                    "arch": "amd64",
                    "variant": "cloud",
                    "versions": {
                        "20260318_07:42": {
                            "items": {
                                "root.tar.xz": {
                                    "ftype": "root.tar.xz",
                                    "path": "images/ubuntu/noble/amd64/cloud/20260318_07:42/rootfs.tar.xz",
                                },
                                "disk.qcow2": {
                                    "ftype": "disk-kvm.img",
                                    "path": "images/ubuntu/noble/amd64/cloud/20260318_07:42/disk.qcow2",
                                },
                            }
                        },
                        "20260319_07:42": {
                            "items": {
                                "disk.qcow2": {
                                    "ftype": "disk-kvm.img",
                                    "path": "images/ubuntu/noble/amd64/cloud/20260319_07:42/disk.qcow2",
                                },
                            }
                        },
                    },
                },
                "ubuntu:noble:amd64:default": {
                    "os": "Ubuntu",
                    "release": "noble",
                    "arch": "amd64",
                    "variant": "default",
                    "versions": {
                        "20260319_07:42": {
                            "items": {
                                "disk.qcow2": {
                                    "ftype": "disk-kvm.img",
                                    "path": "images/ubuntu/noble/amd64/default/20260319_07:42/disk.qcow2",
                                },
                            }
                        },
                    },
                },
                "ubuntu:noble:arm64:cloud": {
                    "os": "Ubuntu",
                    "release": "noble",
                    "arch": "arm64",
                    "variant": "cloud",
                    "versions": {
                        "20260319_07:42": {
                            "items": {
                                "disk.qcow2": {
                                    "ftype": "disk-kvm.img",
                                    "path": "images/ubuntu/noble/arm64/cloud/20260319_07:42/disk.qcow2",
                                },
                            }
                        },
                    },
                },
            }
        },
    }

    monkeypatch.setattr("qhv.images._read_json", lambda url: responses[url])

    image = provider.resolve(store, "noble", "x86_64", variant="cloud")

    assert image.url == "https://images.linuxcontainers.org/images/ubuntu/noble/amd64/cloud/20260319_07:42/disk.qcow2"
    assert image.cache_path == (
        tmp_path / "images" / "incus" / "ubuntu" / "noble" / "amd64" / "cloud" / "20260319_07:42" / "disk.qcow2"
    )
    assert image.disk_format == "qcow2"


def test_incus_provider_ignores_container_only_products(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    provider = IncusPublicImageProvider("ubuntu")
    responses = {
        "https://images.linuxcontainers.org/streams/v1/index.json": {
            "index": {
                "images": {
                    "path": "streams/v1/images.json",
                }
            }
        },
        "https://images.linuxcontainers.org/streams/v1/images.json": {
            "products": {
                "ubuntu:noble:amd64:cloud": {
                    "os": "Ubuntu",
                    "release": "noble",
                    "arch": "amd64",
                    "variant": "cloud",
                    "versions": {
                        "20260319_07:42": {
                            "items": {
                                "root.tar.xz": {
                                    "ftype": "root.tar.xz",
                                    "path": "images/ubuntu/noble/amd64/cloud/20260319_07:42/rootfs.tar.xz",
                                },
                            }
                        },
                    },
                },
            }
        },
    }

    monkeypatch.setattr("qhv.images._read_json", lambda url: responses[url])

    try:
        provider.resolve(store, "noble", "x86_64", variant="cloud")
    except ValueError as exc:
        assert "no VM qcow2 disk artifact is published" in str(exc)
    else:
        raise AssertionError("Expected IncusPublicImageProvider to reject container-only products.")


def test_incus_provider_rejects_missing_release_variant_or_architecture(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    provider = IncusPublicImageProvider("ubuntu")
    responses = {
        "https://images.linuxcontainers.org/streams/v1/index.json": {
            "index": {
                "images": {
                    "path": "streams/v1/images.json",
                }
            }
        },
        "https://images.linuxcontainers.org/streams/v1/images.json": {
            "products": {
                "ubuntu:jammy:amd64:cloud": {
                    "os": "Ubuntu",
                    "release": "jammy",
                    "arch": "amd64",
                    "variant": "cloud",
                    "versions": {
                        "20260319_07:42": {
                            "items": {
                                "disk.qcow2": {
                                    "ftype": "disk-kvm.img",
                                    "path": "images/ubuntu/jammy/amd64/cloud/20260319_07:42/disk.qcow2",
                                },
                            }
                        },
                    },
                },
            }
        },
    }

    monkeypatch.setattr("qhv.images._read_json", lambda url: responses[url])

    try:
        provider.resolve(store, "noble", "x86_64", variant="cloud")
    except ValueError as exc:
        assert "No public Incus VM image matches" in str(exc)
    else:
        raise AssertionError("Expected IncusPublicImageProvider to reject unmatched release, variant, or architecture.")


def test_incus_provider_rejects_non_qcow2_vm_artifacts(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    provider = IncusPublicImageProvider("ubuntu")
    responses = {
        "https://images.linuxcontainers.org/streams/v1/index.json": {
            "index": {
                "images": {
                    "path": "streams/v1/images.json",
                }
            }
        },
        "https://images.linuxcontainers.org/streams/v1/images.json": {
            "products": {
                "ubuntu:noble:amd64:cloud": {
                    "os": "Ubuntu",
                    "release": "noble",
                    "arch": "amd64",
                    "variant": "cloud",
                    "versions": {
                        "20260319_07:42": {
                            "items": {
                                "disk.raw": {
                                    "ftype": "disk-kvm.img",
                                    "path": "images/ubuntu/noble/amd64/cloud/20260319_07:42/disk.raw",
                                },
                            }
                        },
                    },
                },
            }
        },
    }

    monkeypatch.setattr("qhv.images._read_json", lambda url: responses[url])

    try:
        provider.resolve(store, "noble", "x86_64", variant="cloud")
    except ValueError as exc:
        assert "only supports qcow2 VM disks" in str(exc)
    else:
        raise AssertionError("Expected IncusPublicImageProvider to reject non-qcow2 VM artifacts.")
