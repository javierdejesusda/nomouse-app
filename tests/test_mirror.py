import io
import os
import zipfile
from pathlib import Path

import pytest

from nomouse.mirror import MIRROR_URL, WALK_PAGES, fetch_mirror

PAGE_NAMES = (
    "before/home.html",
    "before/news.html",
    "before/tickets.html",
    "before/survey.html",
    "before/annotated/news.html",
    "after/home.html",
    "after/news.html",
    "after/tickets.html",
    "after/survey.html",
    "after/reports/news.html",
    "img/w3c.png",
)


def make_zip(names: tuple[str, ...]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, f"content of {name}")
    return buffer.getvalue()


class FakeDownload:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self.payload


def test_mirror_url_is_the_official_w3c_zip() -> None:
    assert MIRROR_URL == "https://www.w3.org/WAI/demos/bad/bad.zip"


def test_fetch_mirror_unpacks_the_zip_into_the_cache(tmp_path: Path) -> None:
    download = FakeDownload(make_zip(PAGE_NAMES))

    root = fetch_mirror(tmp_path / "cache", download=download)

    assert download.urls == [MIRROR_URL]
    assert root.parent == tmp_path / "cache"
    assert (root / "before" / "survey.html").read_text() == (
        "content of before/survey.html"
    )
    assert (root / "img" / "w3c.png").exists()


def test_fetch_mirror_never_unpacks_news_pages(tmp_path: Path) -> None:
    root = fetch_mirror(tmp_path, download=FakeDownload(make_zip(PAGE_NAMES)))

    assert sorted(p.name for p in root.rglob("news.html")) == []
    assert (root / "before" / "home.html").exists()


def test_fetch_mirror_reuses_an_unpacked_cache(tmp_path: Path) -> None:
    download = FakeDownload(make_zip(PAGE_NAMES))

    first = fetch_mirror(tmp_path, download=download)
    second = fetch_mirror(tmp_path, download=download)

    assert first == second
    assert download.urls == [MIRROR_URL]


def test_fetch_mirror_rejects_members_outside_the_cache(tmp_path: Path) -> None:
    payload = make_zip(("before/home.html", "../escaped.html"))

    with pytest.raises(ValueError, match="escaped.html"):
        fetch_mirror(tmp_path / "cache", download=FakeDownload(payload))

    assert not (tmp_path / "escaped.html").exists()
    assert not any((tmp_path / "cache").glob("*/before/home.html"))


def test_walk_pages_are_home_tickets_and_survey_before_and_after() -> None:
    assert WALK_PAGES == (
        "before/home.html",
        "before/tickets.html",
        "before/survey.html",
        "after/home.html",
        "after/tickets.html",
        "after/survey.html",
    )
    assert not [page for page in WALK_PAGES if "news" in page]


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("NOMOUSE_NETWORK_TESTS") != "1",
    reason="downloads the W3C zip; set NOMOUSE_NETWORK_TESTS=1 to run",
)
def test_fetch_mirror_downloads_the_real_zip(tmp_path: Path) -> None:
    root = fetch_mirror(tmp_path)

    assert all((root / page).is_file() for page in WALK_PAGES)
    assert not list(root.rglob("news.html"))
