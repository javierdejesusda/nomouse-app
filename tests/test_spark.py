import base64
import http.server
import json
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import pytest

from nomouse.spark import (
    API_URL,
    MAX_IMAGES,
    MODEL,
    SparkError,
    ask,
    urllib_transport,
)

PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"

REPLY = {
    "id": "resp_1",
    "object": "response",
    "status": "completed",
    "model": "muse-spark-1.3",
    "output": [
        {"id": "rs_1", "type": "reasoning", "summary": [], "status": "completed"},
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": "Focus stayed ", "annotations": []},
                {"type": "output_text", "text": "on the button.", "annotations": []},
            ],
        },
    ],
    "usage": {
        "input_tokens": 1520,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 40,
        "output_tokens_details": {"reasoning_tokens": 22},
        "total_tokens": 1560,
    },
}


class FakeTransport:
    def __init__(self, status: int = 200, body: object = REPLY) -> None:
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.calls: list[tuple[str, dict[str, object], dict[str, str]]] = []

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        self.calls.append((url, json.loads(body), dict(headers)))
        return self.status, self.body


class Clock:
    def __init__(self, *times: float) -> None:
        self.times = list(times)

    def __call__(self) -> float:
        return self.times.pop(0)


def test_ask_posts_one_responses_request_with_minimal_reasoning() -> None:
    transport = FakeTransport()

    ask("What opened?", [PNG, PNG], transport=transport, env={})

    assert len(transport.calls) == 1
    url, body, headers = transport.calls[0]
    assert url == API_URL == "https://api.meta.ai/v1/responses"
    assert headers == {"Content-Type": "application/json"}
    assert body == {
        "model": MODEL,
        "reasoning": {"effort": "minimal"},
        "store": False,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "What opened?"},
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,"
                        + base64.b64encode(PNG).decode(),
                    },
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,"
                        + base64.b64encode(PNG).decode(),
                    },
                ],
            }
        ],
    }
    assert MODEL == "muse-spark-1.3"


def test_ask_asks_for_structured_output_when_given_a_schema() -> None:
    transport = FakeTransport()
    schema = {"type": "object", "properties": {}, "additionalProperties": False}

    ask("q", transport=transport, env={}, schema=("Answer", schema))

    assert transport.calls[0][1]["text"] == {
        "format": {
            "type": "json_schema",
            "name": "Answer",
            "schema": schema,
            "strict": True,
        }
    }


def test_ask_sends_an_mp4_video_inline_after_the_text() -> None:
    transport = FakeTransport()
    mp4 = b"\x00\x00\x00\x18ftypisomfake-video"

    ask("q", video=mp4, transport=transport, env={})

    assert transport.calls[0][1]["input"][0]["content"] == [
        {"type": "input_text", "text": "q"},
        {
            "type": "input_video",
            "video_url": "data:video/mp4;base64," + base64.b64encode(mp4).decode(),
        },
    ]


def test_ask_refuses_images_and_a_video_in_one_request() -> None:
    transport = FakeTransport()

    with pytest.raises(ValueError, match="images or a video"):
        ask("q", [PNG], video=b"mp4", transport=transport, env={})

    assert transport.calls == []


def test_ask_sends_a_bearer_header_when_the_key_is_in_the_environment() -> None:
    transport = FakeTransport()

    ask("q", transport=transport, env={"META_API_KEY": "fake-key"})

    assert transport.calls[0][2] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer fake-key",
    }


def test_ask_sends_no_authorization_header_for_an_empty_key() -> None:
    transport = FakeTransport()

    ask("q", transport=transport, env={"META_API_KEY": ""})

    assert "Authorization" not in transport.calls[0][2]


def test_ask_returns_the_output_text_usage_and_latency() -> None:
    reply = ask("q", transport=FakeTransport(), clock=Clock(10.0, 12.5))

    assert reply.text == "Focus stayed on the button."
    assert reply.usage == REPLY["usage"]
    assert reply.model == "muse-spark-1.3"
    assert reply.latency_s == 2.5


def test_ask_refuses_more_images_than_the_api_allows() -> None:
    transport = FakeTransport()

    with pytest.raises(ValueError, match="at most 50 images"):
        ask("q", [PNG] * (MAX_IMAGES + 1), transport=transport)

    assert transport.calls == []


def test_ask_raises_with_the_status_and_api_message_on_an_error() -> None:
    body = {
        "error": {
            "message": "Invalid API key",
            "type": "authentication_error",
            "param": None,
            "code": "invalid_api_key",
        }
    }

    with pytest.raises(SparkError) as caught:
        ask(
            "q",
            transport=FakeTransport(401, body),
            env={"META_API_KEY": "fake-key"},
        )

    assert caught.value.status == 401
    assert caught.value.message == "Invalid API key"
    assert str(caught.value) == "HTTP 401: Invalid API key"
    assert "fake-key" not in repr(caught.value.args)


def test_ask_reports_a_raw_body_when_the_error_is_not_json() -> None:
    with pytest.raises(SparkError) as caught:
        ask("q", transport=FakeTransport(404, b""))

    assert caught.value.status == 404
    assert caught.value.message == ""


def test_ask_appends_one_json_line_per_call_without_prompt_images_or_headers(
    tmp_path: Path,
) -> None:
    log = tmp_path / "logs" / "spark.jsonl"

    ask(
        "secret prompt",
        [PNG],
        transport=FakeTransport(),
        log_path=log,
        env={"META_API_KEY": "fake-key"},
    )
    ask("q", transport=FakeTransport(), log_path=log, clock=Clock(1.0, 1.25))

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert set(first) == {"time", "model", "images", "usage", "latency_s"}
    assert first["model"] == "muse-spark-1.3"
    assert first["images"] == 1
    assert first["usage"] == REPLY["usage"]
    assert first["time"].endswith("Z")
    assert second["images"] == 0
    assert second["latency_s"] == 0.25
    text = log.read_text(encoding="utf-8")
    assert "secret prompt" not in text
    assert base64.b64encode(PNG).decode() not in text
    assert "Authorization" not in text
    assert "fake-key" not in text


def test_ask_writes_no_log_line_for_a_failed_call(tmp_path: Path) -> None:
    log = tmp_path / "spark.jsonl"

    with pytest.raises(SparkError):
        ask(
            "q",
            transport=FakeTransport(500, {"error": {"message": "boom"}}),
            log_path=log,
        )

    assert not log.exists()


@pytest.mark.parametrize("key", ["fake-key\n", "fake-key\r\n", " fake-key "])
def test_ask_strips_whitespace_around_the_key(key: str) -> None:
    transport = FakeTransport()

    ask("q", transport=transport, env={"META_API_KEY": key})

    assert transport.calls[0][2]["Authorization"] == "Bearer fake-key"


@pytest.mark.parametrize("key", ["fake\r\nkey", "fake key", "fake\x00key"])
def test_ask_refuses_a_malformed_key_without_echoing_it(key: str) -> None:
    transport = FakeTransport()

    with pytest.raises(ValueError, match="META_API_KEY") as caught:
        ask("q", transport=transport, env={"META_API_KEY": key})

    assert "fake" not in str(caught.value)
    assert transport.calls == []


@pytest.mark.parametrize(
    "body",
    [
        b"<html>proxy error</html>",
        b'{"error": "x"}',
        b'{"model": "m", "output": null, "usage": {}}',
        b"[]",
    ],
)
def test_ask_raises_spark_error_on_a_malformed_reply(
    body: bytes, tmp_path: Path
) -> None:
    log = tmp_path / "spark.jsonl"

    with pytest.raises(SparkError) as caught:
        ask("q", transport=FakeTransport(200, body), log_path=log)

    assert caught.value.status == 200
    assert caught.value.message == "malformed Responses API reply"
    assert not log.exists()


@contextmanager
def serve(handler: type[http.server.BaseHTTPRequestHandler]) -> Iterator[str]:
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_urllib_transport_returns_a_redirect_without_following_it() -> None:
    received: list[str | None] = []

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    with serve(Other) as other:

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(302)
                self.send_header("Location", other.replace("127.0.0.1", "localhost"))
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        with serve(Redirect) as api:
            status, _ = urllib_transport(
                api + "/v1/responses", b"{}", {"Authorization": "Bearer fake-key"}
            )

    assert status == 302
    assert received == []
