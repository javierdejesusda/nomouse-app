import base64
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from nomouse import spark
from nomouse.cli import main
from nomouse.g1 import QUESTION, run_g1, summarize


def focus(name: str | None, y: float) -> dict[str, Any]:
    return {
        "backend_node_id": 7,
        "tag": "A" if name else "BODY",
        "dom_id": None,
        "role": "link" if name else None,
        "name": name,
        "box": {"x": 10.0, "y": y, "width": 80.0, "height": 20.0} if name else None,
        "cover": None,
        "document_has_focus": True,
    }


def make_run(run_dir: Path, steps: int) -> Path:
    run_dir.mkdir(parents=True)
    observation = {"url": "/p.html", "scroll_x": 0, "scroll_y": 0, "settled": True}
    initial = {**observation, "focus": focus(None, 0), "screenshot": "initial.png"}
    record_steps = [
        {
            **observation,
            "focus": focus(f"Link {i}", 30.0 * i),
            "screenshot": f"step-{i:03d}.png",
            "index": i,
            "key": "Tab",
            "appeared": [],
        }
        for i in range(steps)
    ]
    initial["appeared"] = []
    (run_dir / "walk.json").write_text(
        json.dumps({"initial": initial, "steps": record_steps}), encoding="utf-8"
    )
    for item in [initial, *record_steps]:
        (run_dir / item["screenshot"]).write_bytes(item["screenshot"].encode())
    return run_dir


class TokenTransport:
    """Answers like the API, billing 1,500 tokens per image plus the text."""

    def __init__(self, output_tokens: int = 300) -> None:
        self.output_tokens = output_tokens
        self.bodies: list[dict[str, Any]] = []

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        request = json.loads(body)
        self.bodies.append(request)
        content = request["input"][0]["content"]
        images = sum(part["type"] == "input_image" for part in content)
        usage = {
            "input_tokens": 1500 * images + 200,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": self.output_tokens,
            "output_tokens_details": {"reasoning_tokens": 10},
            "total_tokens": 1500 * images + 200 + self.output_tokens,
        }
        reply = {
            "model": "muse-spark-1.3",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "usage": usage,
        }
        return 200, json.dumps(reply).encode()


def test_run_g1_sends_a_sliding_window_of_screenshots_and_focus_boxes(
    tmp_path: Path,
) -> None:
    run_dir = make_run(tmp_path / "run", steps=7)
    transport = TokenTransport()

    replies = run_g1(
        run_dir, tmp_path / "g1.jsonl", calls=8, window=5, transport=transport
    )

    assert len(replies) == len(transport.bodies) == 8
    image_counts = [
        sum(p["type"] == "input_image" for p in b["input"][0]["content"])
        for b in transport.bodies
    ]
    assert image_counts == [1, 2, 3, 4, 5, 5, 5, 5]
    last = transport.bodies[-1]["input"][0]["content"]
    text = last[0]["text"]
    assert text.startswith(QUESTION)
    boxes = json.loads(text.removeprefix(QUESTION))
    assert [b["step"] for b in boxes] == [2, 3, 4, 5, 6]
    assert boxes[-1] == {
        "step": 6,
        "key": "Tab",
        "tag": "A",
        "role": "link",
        "name": "Link 6",
        "box": {"x": 10.0, "y": 180.0, "width": 80.0, "height": 20.0},
    }
    first = json.loads(
        transport.bodies[0]["input"][0]["content"][0]["text"].removeprefix(QUESTION)
    )
    assert first == [
        {
            "step": None,
            "key": None,
            "tag": "BODY",
            "role": None,
            "name": None,
            "box": None,
        }
    ]
    assert last[1]["image_url"].endswith(base64.b64encode(b"step-002.png").decode())
    assert len((tmp_path / "g1.jsonl").read_text().splitlines()) == 8


def test_run_g1_refuses_a_walk_shorter_than_the_calls(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path / "run", steps=3)
    transport = TokenTransport()

    with pytest.raises(ValueError, match="4 observations, 20 calls"):
        run_g1(run_dir, tmp_path / "g1.jsonl", transport=transport)

    assert transport.bodies == []


def usage(input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def test_summarize_measures_flatness_with_and_without_the_warm_up_calls() -> None:
    usages = [usage(n, 100) for n in (1000, 2000, 3000, 4000)]
    usages += [usage(n, 300) for n in (5000, 5400, 4600, 5000)]

    summary = summarize(usages, window=5)

    assert summary.full_window.calls == 4
    assert summary.full_window.mean == 5000
    assert summary.full_window.max_deviation_pct == 8.0
    assert summary.all_calls.calls == 8
    assert summary.all_calls.mean == 3750
    assert round(summary.all_calls.max_deviation_pct, 2) == 73.33
    assert summary.mean_output == 200
    assert summary.passed


def test_summarize_fails_on_input_growth_or_heavy_output() -> None:
    growing = [usage(5000 + 600 * i, 100) for i in range(8)]
    heavy = [usage(5000, 2001) for _ in range(8)]

    assert not summarize(growing, window=5).passed
    assert not summarize(heavy, window=5).passed
    assert summarize(heavy, window=5).mean_output == 2001


def test_g1_command_runs_the_calls_logs_usage_and_reports_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = make_run(tmp_path / "run", steps=19)
    monkeypatch.setattr(spark, "urllib_transport", TokenTransport())

    assert main(["g1", str(run_dir)]) == 0

    out = capsys.readouterr().out
    log = run_dir / "g1-usage.jsonl"
    assert len(log.read_text().splitlines()) == 20
    assert "calls 5-20 (window full): mean input 7700" in out
    assert "max deviation 0.0%" in out
    assert "all 20 calls: mean input" in out
    assert "mean output 300" in out
    assert "G1 PASS" in out


def test_g1_command_reports_fail_with_exit_code_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = make_run(tmp_path / "run", steps=19)
    monkeypatch.setattr(spark, "urllib_transport", TokenTransport(output_tokens=3500))

    assert main(["g1", str(run_dir)]) == 1

    out = capsys.readouterr().out
    assert "G1 FAIL" in out
    assert "above 3,000" in out


def test_g1_command_refuses_to_append_to_an_existing_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = make_run(tmp_path / "run", steps=19)
    (run_dir / "g1-usage.jsonl").write_text("old\n")
    transport = TokenTransport()
    monkeypatch.setattr(spark, "urllib_transport", transport)

    with pytest.raises(SystemExit) as caught:
        main(["g1", str(run_dir)])

    assert caught.value.code == 2
    assert "already exists" in capsys.readouterr().err
    assert transport.bodies == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--window", "0"], "--window must be between 1 and --calls"),
        (["--window", "21"], "--window must be between 1 and --calls"),
        (["--calls", "61"], "61 calls asked"),
        (
            ["--window", "51", "--calls", "60"],
            "--window must be between 1 and --calls, at most 50",
        ),
    ],
)
def test_g1_command_refuses_bad_sizes_before_any_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    run_dir = make_run(tmp_path / "run", steps=59)
    transport = TokenTransport()
    monkeypatch.setattr(spark, "urllib_transport", transport)

    with pytest.raises(SystemExit) as caught:
        main(["g1", str(run_dir), *args])

    assert caught.value.code == 2
    assert message in capsys.readouterr().err
    assert transport.bodies == []


def test_g1_command_needs_a_finished_walk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["g1", str(tmp_path)])

    assert caught.value.code == 2
    assert "walk a page first" in capsys.readouterr().err
    assert not (tmp_path / "g1-usage.jsonl").exists()


class FailingTransport(TokenTransport):
    """Answers like the API until call `fail_at`, which gets HTTP 429."""

    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self.fail_at = fail_at

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        if len(self.bodies) + 1 == self.fail_at:
            self.bodies.append(json.loads(body))
            return 429, json.dumps({"error": {"message": "Rate limited"}}).encode()
        return super().__call__(url, body, headers)


def test_g1_command_reports_an_api_error_and_keeps_the_partial_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = make_run(tmp_path / "run", steps=19)
    monkeypatch.setattr(spark, "urllib_transport", FailingTransport(fail_at=4))

    assert main(["g1", str(run_dir)]) == 1

    captured = capsys.readouterr()
    log = run_dir / "g1-usage.jsonl"
    assert captured.out == ""
    assert captured.err == (
        f"nomouse g1: call failed: HTTP 429: Rate limited; 3 calls logged in {log}\n"
    )
    assert len(log.read_text().splitlines()) == 3
