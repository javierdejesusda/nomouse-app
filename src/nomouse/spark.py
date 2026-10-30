"""Minimal Meta Model API client for Muse Spark 1.3 over the Responses API."""

import base64
import datetime
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_URL = "https://api.meta.ai/v1/responses"
MODEL = "muse-spark-1.3"
# `none` returns HTTP 400 on Muse Spark; `minimal` is the cheapest level, and
# reasoning tokens bill as output.
REASONING_EFFORT = "minimal"
MAX_IMAGES = 50
KEY_VARIABLE = "META_API_KEY"

_TIMEOUT_S = 300

Transport = Callable[[str, bytes, Mapping[str, str]], tuple[int, bytes]]
"""Sends a POST: (url, body, headers) to (HTTP status, response body)."""


class SparkError(Exception):
    """The API answered with an error status.

    Attributes:
        status: HTTP status code.
        message: The API's error message, or the raw body when it is not the
            JSON error envelope.
    """

    def __init__(self, status: int, message: str) -> None:
        """Initializes the error.

        Args:
            status: HTTP status code.
            message: Error text from the response body.
        """
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


@dataclass(frozen=True)
class SparkReply:
    """One completed model call.

    Attributes:
        text: The assistant's output text.
        model: The model that answered, as the API reports it.
        usage: The response's `usage` object, unchanged.
        latency_s: Seconds from sending the request to reading the reply.
    """

    text: str
    model: str
    usage: dict[str, Any]
    latency_s: float


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_transport(
    url: str, body: bytes, headers: Mapping[str, str]
) -> tuple[int, bytes]:
    """Sends a POST with the standard library.

    Args:
        url: Request URL.
        body: Request body.
        headers: Request headers.

    Returns:
        The HTTP status and the response body, for error and redirect
        statuses too. Redirects are not followed, so the Authorization
        header never reaches another host.
    """
    request = urllib.request.Request(url, data=body, headers=dict(headers))
    try:
        with _OPENER.open(request, timeout=_TIMEOUT_S) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def ask(
    prompt: str,
    images: Sequence[bytes] = (),
    *,
    transport: Transport = urllib_transport,
    log_path: Path | None = None,
    env: Mapping[str, str] = os.environ,
    clock: Callable[[], float] = time.monotonic,
) -> SparkReply:
    """Sends one user message (text, then PNG images) to Muse Spark 1.3.

    Reasoning effort is pinned at `minimal`. The API key, with surrounding
    whitespace stripped, is sent as a Bearer token only when `META_API_KEY`
    is set and non-blank; otherwise no Authorization header is sent and the
    egress proxy adds it.

    Args:
        prompt: The text part of the message.
        images: PNG files, sent inline as base64 data URLs in this order.
        transport: Sends the HTTP request.
        log_path: When given, one JSON line (time, model, image count, usage,
            latency) is appended per successful call. Prompts, images and
            headers are never written.
        env: Environment that may hold the API key.
        clock: Monotonic clock in seconds, for the latency.

    Returns:
        The reply text, model, usage and latency.

    Raises:
        ValueError: More than 50 images were given, or the key holds spaces
            or control characters (the message never includes the key).
        SparkError: The API answered with a status other than 200, or with a
            body that is not a Responses API reply.
    """
    if len(images) > MAX_IMAGES:
        raise ValueError(f"the API takes at most {MAX_IMAGES} images per request")
    content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
    content += [
        {
            "type": "input_image",
            "image_url": "data:image/png;base64," + base64.b64encode(png).decode(),
        }
        for png in images
    ]
    body = {
        "model": MODEL,
        "reasoning": {"effort": REASONING_EFFORT},
        "input": [{"type": "message", "role": "user", "content": content}],
    }
    headers = {"Content-Type": "application/json"}
    if key := env.get(KEY_VARIABLE, "").strip():
        # http.client quotes an invalid header value in its exception.
        if " " in key or not key.isprintable():
            raise ValueError(f"{KEY_VARIABLE} holds spaces or control characters")
        headers["Authorization"] = f"Bearer {key}"
    started = clock()
    status, raw = transport(API_URL, json.dumps(body).encode(), headers)
    latency_s = clock() - started
    if status != 200:
        raise SparkError(status, _error_message(raw))
    try:
        data = json.loads(raw)
        reply = SparkReply(
            text=_output_text(data["output"]),
            model=str(data["model"]),
            usage=dict(data["usage"]),
            latency_s=latency_s,
        )
    except (ValueError, KeyError, TypeError) as error:
        raise SparkError(status, "malformed Responses API reply") from error
    if log_path is not None:
        _log(log_path, reply, len(images))
    return reply


def _output_text(output: list[dict[str, Any]]) -> str:
    return "".join(
        part["text"]
        for item in output
        if item["type"] == "message"
        for part in item["content"]
        if part["type"] == "output_text"
    )


def _error_message(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    try:
        return str(json.loads(text)["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return text


def _log(path: Path, reply: SparkReply, images: int) -> None:
    line = {
        "time": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "model": reply.model,
        "images": images,
        "usage": reply.usage,
        "latency_s": reply.latency_s,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(line) + "\n")
