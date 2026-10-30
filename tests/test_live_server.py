import http.client
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nomouse.server import make_server


class FakeLive:
    def __init__(self) -> None:
        self.starts: list[tuple[str, object]] = []

    def status(self) -> dict[str, Any]:
        return {"live": True, "pages": [{"id": "before/home.html"}]}

    def start(self, client: str, page_id: object) -> tuple[int, dict[str, Any]]:
        self.starts.append((client, page_id))
        return 202, {"run": "live-1"}

    def run(self, run_id: str) -> dict[str, Any] | None:
        return {"run": run_id, "state": "done"} if run_id == "live-1" else None


Served = tuple[str, FakeLive]


def serve(
    site: Path, live: FakeLive | None, trust_proxy: bool = False
) -> Iterator[str]:
    server = make_server(site, 0, live=live, trust_proxy=trust_proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<!doctype html><title>Replay</title>")
    return tmp_path


@pytest.fixture
def served(site: Path) -> Iterator[Served]:
    live = FakeLive()
    for base in serve(site, live):
        yield base, live


def request(
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


JSON = {"Content-Type": "application/json"}


def test_the_status_and_run_endpoints_answer_json(served: Served) -> None:
    base, _ = served

    status, headers, body = request(base + "/api/live/status")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert json.loads(body)["live"] is True
    assert json.loads(request(base + "/api/live/runs/live-1")[2])["state"] == "done"
    assert request(base + "/api/live/runs/live-2")[0] == 404


def test_a_post_starts_a_run_for_the_client_address(served: Served) -> None:
    base, live = served

    status, _, body = request(
        base + "/api/live/runs", json.dumps({"page": "before/home.html"}).encode(), JSON
    )

    assert status == 202
    assert json.loads(body) == {"run": "live-1"}
    assert live.starts == [("127.0.0.1", "before/home.html")]


@pytest.mark.parametrize(
    ("path", "body", "headers", "expected"),
    [
        ("/api/live/runs", b'{"page": "x"}', {"Content-Type": "text/plain"}, 415),
        (
            "/api/live/runs",
            b'{"page": "x"}',
            {**JSON, "Origin": "https://evil.example"},
            403,
        ),
        ("/api/live/runs", b"x" * 2000, JSON, 413),
        ("/api/live/runs", b"not json", JSON, 400),
        ("/index.html", b"{}", JSON, 404),
    ],
)
def test_other_posts_are_refused_before_any_run_starts(
    served: Served, path: str, body: bytes, headers: dict[str, str], expected: int
) -> None:
    base, live = served

    status, _, _ = request(base + path, body, headers)

    assert status == expected
    assert live.starts == []


def test_a_same_origin_post_is_accepted(served: Served) -> None:
    base, live = served

    status, _, _ = request(
        base + "/api/live/runs", b'{"page": "x"}', {**JSON, "Origin": base}
    )

    assert status == 202


def test_static_files_are_still_served_and_other_methods_refused(
    served: Served,
) -> None:
    base, _ = served

    assert request(base + "/index.html")[0] == 200
    assert request(base + "/index.html", method="DELETE")[0] == 501


def test_without_live_mode_the_api_says_so(site: Path) -> None:
    for base in serve(site, None):
        status, _, body = request(base + "/api/live/status")
        assert status == 404
        assert "not running" in json.loads(body)["message"]
        assert request(base + "/api/live/runs", b"{}", JSON)[0] == 501


def test_behind_a_trusted_proxy_the_client_is_the_last_forwarded_address(
    site: Path,
) -> None:
    live = FakeLive()
    for base in serve(site, live, trust_proxy=True):
        request(
            base + "/api/live/runs",
            b'{"page": "x"}',
            {**JSON, "X-Forwarded-For": "10.0.0.1, 203.0.113.9"},
        )
    for base in serve(site, live):
        request(
            base + "/api/live/runs",
            b'{"page": "x"}',
            {**JSON, "X-Forwarded-For": "203.0.113.9"},
        )

    assert [client for client, _ in live.starts] == ["203.0.113.9", "127.0.0.1"]


@pytest.mark.parametrize("length", ["-1", "abc"])
def test_a_bad_content_length_is_refused(served: Served, length: str) -> None:
    base, live = served
    connection = http.client.HTTPConnection(base.removeprefix("http://"))
    connection.putrequest("POST", "/api/live/runs")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", length)
    connection.endheaders()

    assert connection.getresponse().status == 400
    assert live.starts == []
    connection.close()
