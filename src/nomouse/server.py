"""Static file server on the loopback interface for local walk targets."""

import functools
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_LOOPBACK_HOST = "127.0.0.1"


class _QuietHandler(SimpleHTTPRequestHandler):
    """Serves files without logging every request to stderr."""

    def log_message(self, format: str, *args: Any) -> None:
        """Discards the per-request access log line."""


def make_server(root: Path, port: int = 0) -> ThreadingHTTPServer:
    """Returns an HTTP server for a directory, bound to 127.0.0.1.

    Only GET and HEAD are answered; other methods get HTTP 501. The caller
    runs `serve_forever` and closes the server.

    Args:
        root: Directory whose files are served.
        port: TCP port; 0 lets the operating system choose one.

    Returns:
        The bound server, not yet serving.
    """
    handler = functools.partial(_QuietHandler, directory=str(root))
    return ThreadingHTTPServer((_LOOPBACK_HOST, port), handler)


@contextmanager
def serve_directory(root: Path) -> Iterator[str]:
    """Serves a directory over HTTP on 127.0.0.1 for the duration of a block.

    Only GET and HEAD are answered; other methods get HTTP 501. The port is
    chosen by the operating system.

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
