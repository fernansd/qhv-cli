from __future__ import annotations

import json
import shutil
from typing import Any
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from qhv.models import ImageRef
from qhv.state import StateStore


INCUS_PUBLIC_IMAGE_SERVER = "https://images.linuxcontainers.org/"
INCUS_SIMPLESTREAMS_INDEX_PATH = "streams/v1/index.json"
INCUS_VM_DISK_FTYPE = "disk-kvm.img"


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _read_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def _normalize_incus_architecture(architecture: str) -> str:
    if architecture in {"x86_64", "amd64"}:
        return "amd64"
    return architecture


def _latest_incus_vm_item(versions: object) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(versions, dict):
        return None
    latest_version: str | None = None
    latest_item: dict[str, Any] | None = None
    for version, version_payload in versions.items():
        if not isinstance(version, str) or not isinstance(version_payload, dict):
            continue
        items = version_payload.get("items")
        if not isinstance(items, dict):
            continue
        for item in items.values():
            if not isinstance(item, dict):
                continue
            if item.get("ftype") != INCUS_VM_DISK_FTYPE:
                continue
            if latest_version is None or version > latest_version:
                latest_version = version
                latest_item = item
            break
    if latest_version is None or latest_item is None:
        return None
    return latest_version, latest_item


def _incus_products_url(base_url: str) -> str:
    index_url = urllib.parse.urljoin(base_url, INCUS_SIMPLESTREAMS_INDEX_PATH)
    payload = _read_json(index_url)
    index = payload.get("index")
    if not isinstance(index, dict):
        raise ValueError("Incus image index is missing the 'index' section.")
    images = index.get("images")
    if not isinstance(images, dict):
        raise ValueError("Incus image index does not define an 'images' stream.")
    path = images.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("Incus image index does not provide an image metadata path.")
    return urllib.parse.urljoin(base_url, path)


def _resolve_incus_product(
    products: object,
    distro: str,
    release: str,
    architecture: str,
    variant: str,
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    if not isinstance(products, dict):
        raise ValueError("Incus image metadata is missing the 'products' section.")
    normalized_distro = distro.lower()
    normalized_variant = variant.lower()
    normalized_arch = _normalize_incus_architecture(architecture)
    matching_products: list[tuple[str, dict[str, Any]]] = []
    for product_key, product in products.items():
        if not isinstance(product_key, str) or not isinstance(product, dict):
            continue
        product_distro = str(product.get("os", "")).lower()
        product_release = str(product.get("release", ""))
        product_arch = str(product.get("arch", ""))
        product_variant = str(product.get("variant", "")).lower()
        if (
            product_distro == normalized_distro
            and product_release == release
            and product_arch == normalized_arch
            and product_variant == normalized_variant
        ):
            matching_products.append((product_key, product))

    if not matching_products:
        raise ValueError(
            "No public Incus VM image matches "
            f"distro '{distro}', release '{release}', architecture '{normalized_arch}', "
            f"and variant '{variant}'."
        )

    selected_product_key: str | None = None
    selected_product: dict[str, Any] | None = None
    selected_version: str | None = None
    selected_item: dict[str, Any] | None = None
    for product_key, product in matching_products:
        latest = _latest_incus_vm_item(product.get("versions"))
        if latest is None:
            continue
        version, item = latest
        if selected_version is None or version > selected_version:
            selected_product_key = product_key
            selected_product = product
            selected_version = version
            selected_item = item

    if selected_product_key is None or selected_product is None or selected_version is None or selected_item is None:
        details = (
            f"distro '{distro}', release '{release}', architecture '{normalized_arch}', "
            f"variant '{variant}'"
        )
        raise ValueError(
            f"Public Incus image metadata exists for {details}, but no VM qcow2 disk artifact is published."
        )
    return selected_product_key, selected_product, selected_version, selected_item


@dataclass(slots=True)
class ImageProvider:
    name: str
    default_release: str | None

    def resolve(
        self,
        state: StateStore,
        release: str,
        architecture: str,
        variant: str = "cloud",
    ) -> ImageRef:
        raise NotImplementedError

    def ensure_downloaded(self, image: ImageRef) -> ImageRef:
        if image.cache_path.exists():
            return image
        _download(image.url, image.cache_path)
        return image


class UbuntuImageProvider(ImageProvider):
    def __init__(self) -> None:
        super().__init__(name="ubuntu", default_release="24.04")

    def resolve(
        self,
        state: StateStore,
        release: str,
        architecture: str,
        variant: str = "cloud",
    ) -> ImageRef:
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
    _release_compose_ids = {
        "42": "1.1",
        "43": "1.6",
    }

    def __init__(self) -> None:
        super().__init__(name="fedora", default_release="43")

    def resolve(
        self,
        state: StateStore,
        release: str,
        architecture: str,
        variant: str = "cloud",
    ) -> ImageRef:
        arch = "x86_64" if architecture in {"x86_64", "amd64"} else architecture
        compose_id = self._release_compose_ids.get(release)
        if compose_id is None:
            supported = ", ".join(sorted(self._release_compose_ids))
            raise ValueError(
                f"Unsupported Fedora cloud release '{release}'. Supported releases: {supported}."
            )
        variant = f"Fedora-Cloud-Base-Generic-{release}-{compose_id}.{arch}.qcow2"
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


class IncusPublicImageProvider(ImageProvider):
    def __init__(self, distro: str, base_url: str = INCUS_PUBLIC_IMAGE_SERVER) -> None:
        super().__init__(name=distro, default_release=None)
        self.base_url = base_url.rstrip("/") + "/"

    def resolve(
        self,
        state: StateStore,
        release: str,
        architecture: str,
        variant: str = "cloud",
    ) -> ImageRef:
        products_url = _incus_products_url(self.base_url)
        metadata = _read_json(products_url)
        product_key, _product, version, disk_item = _resolve_incus_product(
            metadata.get("products"),
            distro=self.name,
            release=release,
            architecture=architecture,
            variant=variant,
        )
        relative_path = disk_item.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError(f"Incus VM image '{product_key}' has an invalid disk path.")
        disk_filename = Path(relative_path).name
        if Path(relative_path).suffix.lower() != ".qcow2":
            raise ValueError(
                f"Incus VM image '{product_key}' publishes '{disk_filename}', but qhv only supports qcow2 VM disks."
            )
        normalized_arch = _normalize_incus_architecture(architecture)
        return ImageRef(
            provider=self.name,
            release=release,
            architecture=architecture,
            filename=disk_filename,
            url=urllib.parse.urljoin(self.base_url, relative_path),
            cache_path=(
                state.images_dir
                / "incus"
                / self.name
                / release
                / normalized_arch
                / variant
                / version
                / disk_filename
            ),
            disk_format="qcow2",
        )


def resolver_for(image_source: str, distro: str) -> ImageProvider:
    normalized_source = image_source.lower()
    normalized_distro = distro.lower()
    cloud_providers: dict[str, ImageProvider] = {
        "ubuntu": UbuntuImageProvider(),
        "fedora": FedoraImageProvider(),
    }
    if normalized_source == "cloud":
        provider = cloud_providers.get(normalized_distro)
        if provider is None:
            raise ValueError(f"Unsupported image provider: {distro}")
        return provider
    if normalized_source == "incus":
        return IncusPublicImageProvider(normalized_distro)
    raise ValueError(f"Unsupported image source: {image_source}")


def provider_for(name: str) -> ImageProvider:
    return resolver_for("cloud", name)
