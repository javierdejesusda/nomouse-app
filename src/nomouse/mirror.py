"""Local mirror of the W3C Before and After Demonstration (BAD).

The demo is downloaded from its official zip at setup time and unpacked into a
cache directory that is never committed. The News pages are excluded
everywhere: they are not unpacked and they are not in the walk allowlist.
"""

import io
import shutil
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

MIRROR_URL = "https://www.w3.org/WAI/demos/bad/bad.zip"

WALK_PAGES: tuple[str, ...] = (
    "before/home.html",
    "before/tickets.html",
    "before/survey.html",
    "after/home.html",
    "after/tickets.html",
    "after/survey.html",
)

_MIRROR_DIRNAME = "bad"
_DOWNLOAD_TIMEOUT_SECONDS = 60


def download_bytes(url: str) -> bytes:
    """Downloads a URL over HTTPS and returns its body.

    Args:
        url: The URL to download.

    Returns:
        The response body.
    """
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
        body: bytes = response.read()
    return body


def mirror_root(cache_dir: Path) -> Path:
    """Returns where the mirror lives inside a cache directory.

    Args:
        cache_dir: The cache directory passed to `fetch_mirror`.

    Returns:
        The mirror root; it exists only after a successful fetch.
    """
    return cache_dir / _MIRROR_DIRNAME


def fetch_mirror(
    cache_dir: Path, *, download: Callable[[str], bytes] = download_bytes
) -> Path:
    """Downloads and unpacks the W3C demo into the cache unless it is there.

    The archive is unpacked into a staging directory first and renamed into
    place only when every member was written, so an interrupted fetch never
    looks like a complete mirror.

    Args:
        cache_dir: Directory that holds the mirror; created when missing.
        download: Function that returns the bytes behind a URL.

    Returns:
        The mirror root, the directory that contains `before/` and `after/`.

    Raises:
        ValueError: If an archive member would be written outside the mirror.
    """
    root = mirror_root(cache_dir)
    if root.is_dir():
        return root
    staging = cache_dir / f"{_MIRROR_DIRNAME}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        _unpack(download(MIRROR_URL), staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    staging.rename(root)
    return root


def _unpack(payload: bytes, destination: Path) -> None:
    base = destination.resolve()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            target = (base / member.filename).resolve()
            if not target.is_relative_to(base):
                raise ValueError(
                    f"archive member outside the mirror: {member.filename}"
                )
            if member.is_dir() or _is_news_page(member.filename):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))


def _is_news_page(name: str) -> bool:
    return PurePosixPath(name).name == "news.html"
