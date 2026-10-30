import base64
import hashlib
import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from nomouse import spark
from nomouse.bare import (
    BARE_PROMPT,
    BARE_SCHEMA,
    FAILURE_TYPES,
    bare_path,
    bare_prompt_digest,
    bare_review,
    encode_mp4,
)
from nomouse.cli import main
from nomouse.reviewer import AnswerError, ReviewError

VERDICTS = b'{"verdicts":[]}\n'
VIDEO = b"\x00\x00\x00\x18ftypisomfake"
LABELS = Path(__file__).resolve().parents[1] / "eval" / "w3c-labels.json"


def write_run(run_dir: Path, keys: Sequence[str], *, freeze: bool = True) -> Path:
    run_dir.mkdir(parents=True)
    steps = [
        {"index": i, "key": key, "screenshot": f"step-{i:03d}.png"}
        for i, key in enumerate(keys)
    ]
    record = {
        "url": "http://127.0.0.1/p.html",
        "initial": {"screenshot": "initial.png"},
        "steps": steps,
    }
    (run_dir / "walk.json").write_text(json.dumps(record), encoding="utf-8")
    for shot in ["initial.png", *(s["screenshot"] for s in steps)]:
        (run_dir / shot).write_bytes(shot.encode())
    if freeze:
        (run_dir / "verdicts.json").write_bytes(VERDICTS)
        digest = hashlib.sha256(VERDICTS).hexdigest()
        (run_dir / "verdicts.json.sha256").write_text(f"{digest}  verdicts.json\n")
    return run_dir


FAILURES = [
    {
        "element": "Menu",
        "type": "focus-not-moved",
        "second": 3,
        "description": "Focus stayed on Menu.",
    }
]


class BareTransport:
    def __init__(self, text: str = json.dumps({"failures": FAILURES})) -> None:
        self.text = text
        self.bodies: list[dict[str, Any]] = []

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        self.bodies.append(json.loads(body))
        reply = {
            "model": "muse-spark-1.3",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": self.text}],
                }
            ],
            "usage": {
                "input_tokens": 900,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 300,
                "output_tokens_details": {"reasoning_tokens": 200},
                "total_tokens": 1200,
            },
        }
        return 200, json.dumps(reply).encode()


class FakeEncoder:
    def __init__(self) -> None:
        self.frames: list[list[str]] = []

    def __call__(self, frames: Sequence[Path]) -> bytes:
        self.frames.append([frame.name for frame in frames])
        return VIDEO


def test_bare_review_sends_the_walk_as_one_video_with_the_bare_prompt(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab", "Enter", "Escape"])
    transport = BareTransport()
    encoder = FakeEncoder()

    bare_review(run_dir, run="eval1", transport=transport, encode=encoder)

    assert encoder.frames == [
        ["initial.png", "step-000.png", "step-001.png", "step-002.png"]
    ]
    (body,) = transport.bodies
    content = body["input"][0]["content"]
    assert content[0]["text"].startswith(BARE_PROMPT + "\n")
    assert content[1] == {
        "type": "input_video",
        "video_url": "data:video/mp4;base64," + base64.b64encode(VIDEO).decode(),
    }
    assert body["text"]["format"]["schema"] == BARE_SCHEMA
    assert BARE_PROMPT == "list the keyboard and focus failures"


def test_bare_review_writes_the_answer_with_hashes_and_usage(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab", "Enter"])

    result = bare_review(
        run_dir, run="eval1", transport=BareTransport(), encode=FakeEncoder()
    )

    written = json.loads((run_dir / "bare-eval1.json").read_text(encoding="utf-8"))
    assert written == result
    assert result["run"] == "eval1"
    assert result["verdicts_sha256"] == hashlib.sha256(VERDICTS).hexdigest()
    assert result["prompt_sha256"] == bare_prompt_digest()
    assert result["model"] == "muse-spark-1.3"
    assert result["input"] == {
        "kind": "video",
        "frames": 3,
        "seconds_per_frame": 1,
        "video_sha256": hashlib.sha256(VIDEO).hexdigest(),
    }
    assert result["failures"] == FAILURES
    assert result["usage"]["input_tokens"] == 900
    assert (run_dir / "walk.mp4").read_bytes() == VIDEO


def test_bare_review_reuses_the_walk_video_of_an_earlier_run(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab"])
    (run_dir / "walk.mp4").write_bytes(b"earlier")
    encoder = FakeEncoder()
    transport = BareTransport()

    result = bare_review(run_dir, run="eval2", transport=transport, encode=encoder)

    assert encoder.frames == []
    assert result["input"]["video_sha256"] == hashlib.sha256(b"earlier").hexdigest()


@pytest.mark.parametrize("tamper", ["missing", "changed"])
def test_bare_review_refuses_unfrozen_verdicts_before_any_call(
    tmp_path: Path, tamper: str
) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab"], freeze=tamper == "changed")
    if tamper == "changed":
        (run_dir / "verdicts.json").write_bytes(b"{}")
    transport = BareTransport()
    encoder = FakeEncoder()

    with pytest.raises(ReviewError, match="verdicts"):
        bare_review(run_dir, run="eval1", transport=transport, encode=encoder)

    assert transport.bodies == [] and encoder.frames == []
    assert not (run_dir / "bare-eval1.json").exists()


def test_bare_review_never_overwrites(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab"])
    bare_path(run_dir, "eval1").write_text("{}", encoding="utf-8")
    transport = BareTransport()

    with pytest.raises(FileExistsError):
        bare_review(run_dir, run="eval1", transport=transport, encode=FakeEncoder())

    assert transport.bodies == []


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"failures": [{**FAILURES[0], "type": "ugly"}]}),
        json.dumps({"failures": [{**FAILURES[0], "second": "3"}]}),
        json.dumps({"failures": [{"element": "x"}]}),
        json.dumps({"items": []}),
    ],
)
def test_bare_review_keeps_an_unfit_reply_and_writes_no_answer(
    tmp_path: Path, text: str
) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab"])

    with pytest.raises(AnswerError):
        bare_review(
            run_dir, run="eval1", transport=BareTransport(text), encode=FakeEncoder()
        )

    assert not (run_dir / "bare-eval1.json").exists()
    kept = json.loads((run_dir / "bare-eval1.partial.json").read_text(encoding="utf-8"))
    assert kept["text"] == text
    assert kept["usage"]["output_tokens"] == 300
    assert kept["complete"] is False


def test_bare_review_accepts_an_empty_list_and_an_unnamed_element(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab"])
    unnamed = [{**FAILURES[0], "element": ""}]

    result = bare_review(
        run_dir,
        run="eval1",
        transport=BareTransport(json.dumps({"failures": unnamed})),
        encode=FakeEncoder(),
    )

    assert result["failures"] == unnamed


def test_failure_types_match_the_w3c_label_vocabulary() -> None:
    labels = json.loads(LABELS.read_text(encoding="utf-8"))

    assert list(FAILURE_TYPES) == list(labels["types"])
    item = BARE_SCHEMA["properties"]["failures"]["items"]
    assert item["properties"]["type"]["enum"] == list(FAILURE_TYPES)


def test_bare_prompt_digest_covers_the_prompt_and_schema() -> None:
    from nomouse import bare

    frozen = json.dumps(
        [bare.BARE_MESSAGE, BARE_SCHEMA], sort_keys=True, separators=(",", ":")
    )
    assert bare_prompt_digest() == hashlib.sha256(frozen.encode()).hexdigest()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_encode_mp4_shows_each_frame_for_one_second(tmp_path: Path) -> None:
    frames = []
    for i, colour in enumerate(["red", "green", "blue"]):
        frame = tmp_path / f"f{i}.png"
        Image.new("RGB", (64, 48), colour).save(frame)
        frames.append(frame)

    video = encode_mp4(frames)

    out = tmp_path / "v.mp4"
    out.write_bytes(video)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames,width,height:format=duration",
            "-of",
            "json",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(probe.stdout)
    assert info["streams"][0]["nb_read_frames"] == "3"
    assert (info["streams"][0]["width"], info["streams"][0]["height"]) == (64, 48)
    assert float(info["format"]["duration"]) == pytest.approx(3.0)


def test_cli_bare_writes_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab"])
    monkeypatch.setattr(spark, "urllib_transport", BareTransport())
    monkeypatch.setattr("nomouse.bare.encode_mp4", FakeEncoder())

    assert main(["bare", str(run_dir), "--run", "eval1"]) == 0

    out = capsys.readouterr().out
    assert "1 failures listed" in out
    assert "900 input and 300 output tokens" in out
    assert str(run_dir / "bare-eval1.json") in out


def test_cli_bare_reports_an_unfit_answer_as_a_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab"])
    monkeypatch.setattr(spark, "urllib_transport", BareTransport("nope"))
    monkeypatch.setattr("nomouse.bare.encode_mp4", FakeEncoder())

    assert main(["bare", str(run_dir), "--run", "eval1"]) == 1

    assert "bare-eval1.partial.json" in capsys.readouterr().err


def test_cli_bare_needs_a_run_label(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", ["Tab"])

    with pytest.raises(SystemExit) as exit_info:
        main(["bare", str(run_dir)])

    assert exit_info.value.code == 2
