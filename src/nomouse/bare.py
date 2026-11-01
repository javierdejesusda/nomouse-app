"""Bare-Spark eval arm: one video call per frozen walk with the bare prompt.

This arm asks what Muse Spark find on its own, asked
only to "list the keyboard and focus failures"? It sees a video made from the
walk's frozen screenshots (one frame per key, one second each), with no focus
boxes, walker verdicts, labels or DOM ids. The answer is structured (element,
failure type, second, one sentence) so the scorer can match it to the labels
by type and visible name, under rules fixed before any run.
"""

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from nomouse.driver import RECORD_NAME
from nomouse.reviewer import AnswerError, ReviewError, frozen_digest, review_path
from nomouse.spark import (
    MODEL,
    REASONING_EFFORT,
    Transport,
    ask,
    urllib_transport,
)

BARE_PROMPT = "list the keyboard and focus failures"
SECONDS_PER_FRAME = 1
# Format only: says how to read the video's time and what each item holds.
BARE_MESSAGE = (
    BARE_PROMPT + "\n"
    "The video shows one frame per second. Answer in the JSON schema: one item"
    " per failure with the element's visible text or name, the failure type,"
    " the second of the video where it shows, and one sentence.\n"
)
FAILURE_TYPES = (
    "focus-order",
    "focus-not-moved",
    "focus-not-returned",
    "keyboard-trap",
    "not-focusable",
    "focus-not-visible",
    "focus-obscured",
    "no-bypass",
    "keyboard-operation",
    "other",
)
_ITEM_PROPERTIES: dict[str, Any] = {
    "element": {"type": "string"},
    "type": {"type": "string", "enum": list(FAILURE_TYPES)},
    "second": {"type": "integer"},
    "description": {"type": "string"},
}
BARE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "failures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _ITEM_PROPERTIES,
                "required": list(_ITEM_PROPERTIES),
                "additionalProperties": False,
            },
        }
    },
    "required": ["failures"],
    "additionalProperties": False,
}
VIDEO_NAME = "walk.mp4"

Encoder = Callable[[Sequence[Path]], bytes]
"""Turns screenshots, in order, into an mp4 showing each for one second."""


def bare_prompt_digest() -> str:
    """Returns the SHA-256 of the message and answer schema the arm sends.

    Returns:
        The hex SHA-256 of `[BARE_MESSAGE, BARE_SCHEMA]` as compact JSON with
        sorted keys, UTF-8 encoded.
    """
    frozen = json.dumps(
        [BARE_MESSAGE, BARE_SCHEMA], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(frozen.encode()).hexdigest()


def bare_path(run_dir: Path, run: str) -> Path:
    """Returns where the bare-Spark answer for a run label is written.

    Args:
        run_dir: The walk's run directory.
        run: The run label.

    Returns:
        `bare-<run>.json` in the run directory.

    Raises:
        ValueError: The label is not made of letters, digits, `_` and `-`.
    """
    label = review_path(run_dir, run).name.removeprefix("review-")
    return run_dir / f"bare-{label}"


def partial_path(run_dir: Path, run: str) -> Path:
    """Returns where an answer that does not fit the schema is kept.

    Args:
        run_dir: The walk's run directory.
        run: The run label.

    Returns:
        `bare-<run>.partial.json` in the run directory.
    """
    path = bare_path(run_dir, run)
    return path.with_name(f"{path.stem}.partial.json")


def encode_mp4(frames: Sequence[Path]) -> bytes:
    """Encodes screenshots as an H.264 mp4, one second per frame, with ffmpeg.

    Args:
        frames: PNG files of the same size, in display order.

    Returns:
        The mp4 file's bytes.

    Raises:
        subprocess.CalledProcessError: ffmpeg failed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for number, frame in enumerate(frames):
            (folder / f"frame-{number:05d}.png").symlink_to(frame.resolve())
        out = folder / "walk.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "error",
                "-framerate",
                str(1 / SECONDS_PER_FRAME),
                "-i",
                str(folder / "frame-%05d.png"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        return out.read_bytes()


def bare_review(
    run_dir: Path,
    *,
    run: str,
    transport: Transport = urllib_transport,
    encode: Encoder | None = None,
) -> dict[str, Any]:
    """Asks Muse Spark the bare prompt over the walk's video and writes it.

    The video is written once to `walk.mp4` and reused by later runs, so
    every run of the arm sends the same bytes.

    Args:
        run_dir: Run directory with the walk record, its screenshots and the
            frozen walker verdicts with their `.sha256`.
        run: Run label; the answer goes to `bare-<run>.json`.
        transport: Sends the HTTP request.
        encode: Makes the video; defaults to `encode_mp4`.

    Returns:
        The answer, as written: page, run label, verdicts hash, prompt
        hash, model, reasoning effort, the video's frame count and hash, the
        listed failures, usage and latency.

    Raises:
        ValueError: The run label is not a plain name.
        FileExistsError: The answer or its partial file already exists.
        ReviewError: The verdicts are missing or do not match their hash.
        AnswerError: The reply does not fit the schema; it is kept with its
            usage in `bare-<run>.partial.json` and no answer is written.
        SparkError: The call failed.
    """
    path = bare_path(run_dir, run)
    partial = partial_path(run_dir, run)
    for existing in (path, partial):
        if existing.exists():
            raise FileExistsError(f"{existing} already exists")
    digest = frozen_digest(run_dir)
    record_path = run_dir / RECORD_NAME
    if not record_path.is_file():
        raise ReviewError(f"no {RECORD_NAME} in {run_dir}")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    observations = [record["initial"], *record["steps"]]
    video_path = run_dir / VIDEO_NAME
    if not video_path.is_file():
        frames = [run_dir / o["screenshot"] for o in observations]
        video_path.write_bytes((encode or encode_mp4)(frames))
    video = video_path.read_bytes()
    head = {
        "page": record["url"],
        "run": run,
        "verdicts_sha256": digest,
        "prompt_sha256": bare_prompt_digest(),
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "input": {
            "kind": "video",
            "frames": len(observations),
            "seconds_per_frame": SECONDS_PER_FRAME,
            "video_sha256": hashlib.sha256(video).hexdigest(),
        },
    }
    reply = ask(
        BARE_MESSAGE,
        video=video,
        schema=("KeyboardFocusFailures", BARE_SCHEMA),
        transport=transport,
    )
    try:
        failures = _validated(reply.text)
    except AnswerError:
        kept = {"complete": False, **head, "text": reply.text, "usage": reply.usage}
        partial.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
        raise
    result = {
        **head,
        "failures": failures,
        "usage": reply.usage,
        "latency_s": reply.latency_s,
    }
    with path.open("x", encoding="utf-8") as out:
        out.write(json.dumps(result, indent=2) + "\n")
    return result


def _validated(text: str) -> list[dict[str, Any]]:
    """Checks the reply against `BARE_SCHEMA`; an element may be empty."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise AnswerError("the model's answer is not JSON") from error
    if not isinstance(data, dict) or set(data) != {"failures"}:
        raise AnswerError("the model's answer has no failures list")
    failures = data["failures"]
    if not isinstance(failures, list) or not all(map(_fits, failures)):
        raise AnswerError(f"the model's answer has an unexpected item: {failures!r}")
    return failures


def _fits(item: object) -> bool:
    if not isinstance(item, Mapping) or set(item) != set(_ITEM_PROPERTIES):
        return False
    second = item["second"]
    return (
        isinstance(item["element"], str)
        and isinstance(item["description"], str)
        and item["type"] in FAILURE_TYPES
        and isinstance(second, int)
        and not isinstance(second, bool)
    )
