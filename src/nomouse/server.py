"""Static file server for walk targets and the replay, with optional live API."""

import functools
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

_LOOPBACK_HOST = "127.0.0.1"
_RUNS_PATH = "/api/live/runs"
_RUN_PATH = re.compile(r"/api/live/runs/([A-Za-z0-9-]+)")
_MAX_BODY = 1024
_log = logging.getLogger(__name__)


class LiveApi(Protocol):
    """What the server needs from the live run service."""

    def status(self) -> dict[str, Any]:
        """Returns live mode's status."""

    def start(self, client: str, page_id: object) -> tuple[int, dict[str, Any]]:
        """Starts a run; returns the HTTP status and JSON body."""

    def run(self, run_id: str) -> dict[str, Any] | None:
        """Returns a run's state, or None."""


class _Server(ThreadingHTTPServer):
    live: LiveApi | None = None
    trust_proxy = False
    timeout_s = 30.0

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Logs a failed request by error type only, never the client address."""
        error = sys.exc_info()[1]
        _log.warning("request failed (%s)", type(error).__name__)


class _Handler(SimpleHTTPRequestHandler):
    """Serves files; with live mode, also the live run API under /api/live/."""

    server: _Server
    server_version = "nomouse"
    sys_version = ""

    def setup(self) -> None:
        """Closes a connection whose client stalls longer than the timeout."""
        super().setup()
        self.connection.settimeout(self.server.timeout_s)

    def list_directory(self, path: str | os.PathLike[str]) -> None:
        """Answers HTTP 404 for a directory with no index.html."""
        self.send_error(404, "File not found")

    def log_message(self, format: str, *args: Any) -> None:
        """Discards the per-request access log line."""

    def end_headers(self) -> None:
        """Adds headers that stop content sniffing, referrer leaks and framing.

        Same-origin frames stay allowed: walked pages may frame their own
        origin, and the guard aborts every other origin anyway.
        """
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        super().end_headers()

    def do_GET(self) -> None:
        """Answers the live status and run state, or serves a file."""
        path = urlsplit(self.path).path
        if not path.startswith("/api/"):
            super().do_GET()
            return
        live = self.server.live
        if live is None:
            self._not_running()
        elif path == "/api/live/status":
            self._json(200, live.status())
        elif (match := _RUN_PATH.fullmatch(path)) and (
            state := live.run(match.group(1))
        ) is not None:
            self._json(200, state)
        else:
            self._json(404, {"error": "not_found", "message": "No such run."})

    def do_POST(self) -> None:
        """Starts a live run from a same-origin JSON request."""
        live = self.server.live
        if live is None:
            self.send_error(501, "Unsupported method ('POST')")
            return
        if urlsplit(self.path).path != _RUNS_PATH:
            self._json(404, {"error": "not_found", "message": "Not found."})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "json_only", "message": "Send JSON."})
            return
        origin = self.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc != self.headers.get("Host"):
            self._json(403, {"error": "origin", "message": "Same origin only."})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._json(400, {"error": "bad_length", "message": "Bad length."})
            return
        if length > _MAX_BODY:
            self._json(413, {"error": "too_large", "message": "Request too large."})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            self._json(400, {"error": "bad_json", "message": "Send a JSON object."})
            return
        page = body.get("page") if isinstance(body, dict) else None
        self._json(*live.start(self._client(), page))

    def _client(self) -> str:
        """Returns the client address, from the trusted proxy when set."""
        forwarded = self.headers.get("X-Forwarded-For")
        if self.server.trust_proxy and forwarded:
            return forwarded.split(",")[-1].strip()
        return str(self.client_address[0])

    def _not_running(self) -> None:
        self._json(
            404,
            {
                "error": "live_not_running",
                "message": "Live mode is not running on this server. The replay"
                " of every frozen run works with no key.",
            },
        )

    def _json(self, status: int, data: dict[str, Any]) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def make_server(
    root: Path,
    port: int = 0,
    *,
    host: str = _LOOPBACK_HOST,
    live: LiveApi | None = None,
    trust_proxy: bool = False,
    timeout_s: float = 30.0,
) -> ThreadingHTTPServer:
    """Returns an HTTP server for a directory, bound to 127.0.0.1 by default.

    Files are answered to GET and HEAD; a directory without index.html is
    not listed (HTTP 404). With `live`, GET
    `/api/live/status` and `/api/live/runs/<id>` and a same-origin JSON POST
    to `/api/live/runs` reach the live run service, and any other POST
    gets HTTP 404. Without it, and for other methods, the answer is HTTP
    501. A failed request is logged by error type, without the client
    address. The caller runs `serve_forever` and closes the server.

    Args:
        root: Directory whose files are served.
        port: TCP port; 0 lets the operating system choose one.
        host: Interface to bind.
        live: The live run service, or None for files only.
        trust_proxy: Take the client address for the rate limit from the
            last `X-Forwarded-For` entry, as added by a reverse proxy in
            front of the server.
        timeout_s: Seconds a connection may stall (slow headers or body)
            before it is closed.

    Returns:
        The bound server, not yet serving.
    """
    handler = functools.partial(_Handler, directory=str(root))
    server = _Server((host, port), handler)
    server.live = live
    server.trust_proxy = trust_proxy
    server.timeout_s = timeout_s
    return server


@contextmanager
def serve_directory(root: Path) -> Iterator[str]:
    """Serves a directory over HTTP on 127.0.0.1 for the duration of a block.

    Only GET and HEAD are answered for files. The port is chosen by the
    operating system.

    Args:
        root: Directory whose files are served.

    Yields:
        The base URL of the server, ending in a slash.
    """
    server = make_server(root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{_LOOPBACK_HOST}:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
