from __future__ import annotations

import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from qhv.models import ImageRef
from qhv.state import StateStore


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


@dataclass(slots=True)
class ImageProvider:
    name: str

    def resolve(self, state: StateStore, release: str, architecture: str) -> ImageRef:
        raise NotImplementedError

    def ensure_downloaded(self, image: ImageRef) -> ImageRef:
        if image.cache_path.exists():
            return image
        _download(image.url, image.cache_path)
        return image


class UbuntuImageProvider(ImageProvider):
    def __init__(self) -> None:
        super().__init__(name="ubuntu")

    def resolve(self, state: StateStore, release: str, architecture: str) -> ImageRef:
        arch = "amd64" if architecture in {"x86_64", "amd64"} else architecture
        filename = f"ubuntu-{release}-server-cloudimg-{arch}.img"
        url = (
            f"https://cloud-images.ubuntu.com/releases/server/{release}/release/"
            f"{filename}"
        )
        return ImageRef(
            provider=self.name,
            release=release,
            architecture=architecture,
            filename=filename,
            url=url,
            cache_path=state.images_dir / self.name / release / filename,
            disk_format="raw",
        )


class FedoraImageProvider(ImageProvider):
    def __init__(self) -> None:
        super().__init__(name="fedora")

    def resolve(self, state: StateStore, release: str, architecture: str) -> ImageRef:
        arch = "x86_64" if architecture in {"x86_64", "amd64"} else architecture
        variant = f"Fedora-Cloud-Base-Generic-{release}-1.6.{arch}.qcow2"
        url = (
            f"https://download.fedoraproject.org/pub/fedora/linux/releases/"
            f"{release}/Cloud/{arch}/images/{variant}"
        )
        return ImageRef(
            provider=self.name,
            release=release,
            architecture=architecture,
            filename=variant,
            url=url,
            cache_path=state.images_dir / self.name / release / variant,
            disk_format="qcow2",
        )


def provider_for(name: str) -> ImageProvider:
    providers: dict[str, ImageProvider] = {
        "ubuntu": UbuntuImageProvider(),
        "fedora": FedoraImageProvider(),
    }
    normalized = name.lower()
    if normalized not in providers:
        raise ValueError(f"Unsupported image provider: {name}")
    return providers[normalized]
