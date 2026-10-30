import http.client
import json
import logging
import socket
import threading
import time
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
        self.tasks: list[object] = []

    def status(self) -> dict[str, Any]:
        return {"live": True, "pages": [{"id": "before/home.html"}]}

    def start(
        self, client: str, page_id: object, task: object = None
    ) -> tuple[int, dict[str, Any]]:
        self.starts.append((client, page_id))
        self.tasks.append(task)
        return 202, {"run": "live-1"}

    def run(self, run_id: str) -> dict[str, Any] | None:
        return {"run": run_id, "state": "done"} if run_id == "live-1" else None


Served = tuple[str, FakeLive]


def serve(
    site: Path,
    live: FakeLive | None,
    trust_proxy: bool = False,
    timeout_s: float = 30.0,
) -> Iterator[str]:
    server = make_server(
        site, 0, live=live, trust_proxy=trust_proxy, timeout_s=timeout_s
    )
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
    assert live.tasks == [None]


def test_a_post_passes_the_typed_task_on_as_sent(served: Served) -> None:
    base, live = served
    body = {"page": "builder/dialog-focus-returns.html", "task": ["open", 1]}

    status, _, _ = request(base + "/api/live/runs", json.dumps(body).encode(), JSON)

    assert status == 202
    assert live.tasks == [["open", 1]]


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


@pytest.mark.parametrize(
    "sent",
    [
        b"GET / HTTP/1.1\r\nHost: x\r\n",
        b"POST /api/live/runs HTTP/1.1\r\nHost: x\r\n"
        b"Content-Type: application/json\r\nContent-Length: 1000\r\n\r\n{",
    ],
)
def test_a_stalled_client_is_disconnected_after_the_timeout(
    site: Path, sent: bytes
) -> None:
    live = FakeLive()
    for base in serve(site, live, timeout_s=0.5):
        host, port = base.removeprefix("http://").split(":")
        with socket.create_connection((host, int(port)), timeout=5) as client:
            client.sendall(sent)
            started = time.monotonic()
            while client.recv(4096):
                pass
            assert time.monotonic() - started < 3
    assert live.starts == []


class BrokenLive(FakeLive):
    def status(self) -> dict[str, Any]:
        raise RuntimeError("boom")


def test_a_failed_request_is_logged_without_the_client_address(
    site: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    for base in serve(site, BrokenLive()):
        with pytest.raises((urllib.error.URLError, http.client.HTTPException)):
            urllib.request.urlopen(base + "/api/live/status")

    logged = capsys.readouterr().err + caplog.text
    assert "127.0.0.1" not in logged
    assert "RuntimeError" in caplog.text


def test_directories_are_not_listed(site: Path, served: Served) -> None:
    base, _ = served
    (site / "data").mkdir()
    (site / "data" / "a.json").write_text("{}")

    assert request(base + "/")[0] == 200
    status, _, body = request(base + "/data/")
    assert status == 404
    assert b"a.json" not in body


def test_pages_may_not_be_framed_by_other_sites_and_the_server_is_unnamed(
    served: Served,
) -> None:
    base, _ = served

    _, headers, _ = request(base + "/index.html")

    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "Python" not in headers["Server"]
