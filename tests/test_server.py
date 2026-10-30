import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nomouse.server import serve_directory


def test_serve_directory_serves_files_on_loopback(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text("<p>hello</p>")

    with (
        serve_directory(tmp_path) as base_url,
        urllib.request.urlopen(base_url + "page.html") as response,
    ):
        body = response.read()

    assert base_url.startswith("http://127.0.0.1:")
    assert base_url.endswith("/")
    assert body == b"<p>hello</p>"


def test_serve_directory_refuses_non_get_requests(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text("<p>hello</p>")

    with serve_directory(tmp_path) as base_url:
        request = urllib.request.Request(base_url + "page.html", method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)

    assert caught.value.code == 501


def test_serve_directory_stops_when_the_block_exits(tmp_path: Path) -> None:
    with serve_directory(tmp_path) as base_url:
        pass

    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(base_url, timeout=2)
