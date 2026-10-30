"""Gate G1: per-call token usage of Muse Spark on one walk.

The calls mirror the planned per-step reviewer and navigator calls: each one
sends a sliding window of the walk's latest screenshots with their focus boxes
as JSON text and a short fixed question, so per-call input should stop growing
once the window is full.
"""

import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nomouse.driver import RECORD_NAME
from nomouse.spark import SparkReply, Transport, ask, urllib_transport

QUESTION = (
    "These are the latest screenshots of a keyboard-only walk of one page,"
    " oldest first, one per key press. The JSON below gives, for each"
    " screenshot in the same order, the key pressed and the element the"
    " browser reports as focused, with its box in CSS pixels. For the last key"
    " only: if something opened, did focus land inside it; if something"
    " closed, did focus return to its trigger; if it was a Tab, does the move"
    " follow the visual reading order? Answer in at most two sentences.\n"
)

DEFAULT_CALLS = 20
DEFAULT_WINDOW = 5
MAX_INPUT_DEVIATION_PCT = 10.0
MAX_MEAN_OUTPUT = 2000.0
# Above this mean output the design caps walks at 25 keys and runs builder
# pages once.
FALLBACK_OUTPUT = 3000.0


@dataclass(frozen=True)
class Flatness:
    """How far per-call input tokens stray from their mean.

    Attributes:
        calls: Number of calls measured.
        mean: Mean input tokens.
        low: Fewest input tokens in one call.
        high: Most input tokens in one call.
        max_deviation_pct: Largest distance of one call from the mean, as a
            percentage of the mean.
    """

    calls: int
    mean: float
    low: int
    high: int
    max_deviation_pct: float


@dataclass(frozen=True)
class Summary:
    """The G1 numbers for one series of calls.

    Attributes:
        full_window: Flatness over the calls whose window is full; this is the
            figure the pass criterion uses.
        all_calls: Flatness over every call, including the warm-up calls that
            carry fewer screenshots.
        mean_output: Mean output tokens per call, reasoning included.
        passed: Full-window input within 10% of its mean and mean output at
            most 2,000 tokens.
    """

    full_window: Flatness
    all_calls: Flatness
    mean_output: float
    passed: bool


def run_g1(
    run_dir: Path,
    log_path: Path,
    *,
    calls: int = DEFAULT_CALLS,
    window: int = DEFAULT_WINDOW,
    transport: Transport = urllib_transport,
) -> list[SparkReply]:
    """Makes the G1 calls over one walk, logging usage per call.

    Call `i` covers observations `i - window + 1` to `i` of the walk (the
    initial observation first, then one per key), so the first `window - 1`
    calls carry fewer screenshots.

    Args:
        run_dir: Run directory holding the walk record and its screenshots.
        log_path: JSON lines file that receives one usage line per call.
        calls: Number of calls; the walk needs at least this many
            observations.
        window: Screenshots per call once the window is full.
        transport: Sends the HTTP requests.

    Returns:
        The replies, in call order.

    Raises:
        ValueError: The walk has fewer observations than calls.
    """
    record = json.loads((run_dir / RECORD_NAME).read_text(encoding="utf-8"))
    observations: list[dict[str, Any]] = [record["initial"], *record["steps"]]
    if len(observations) < calls:
        raise ValueError(
            f"the walk has {len(observations)} observations, {calls} calls asked"
        )
    replies = []
    for end in range(calls):
        shown = observations[max(0, end - window + 1) : end + 1]
        boxes = [_focus_entry(observation) for observation in shown]
        images = [(run_dir / o["screenshot"]).read_bytes() for o in shown]
        replies.append(
            ask(
                QUESTION + json.dumps(boxes),
                images,
                transport=transport,
                log_path=log_path,
            )
        )
    return replies


def summarize(usages: Sequence[Mapping[str, Any]], window: int) -> Summary:
    """Computes the G1 numbers from per-call `usage` objects.

    Args:
        usages: The `usage` object of each call, in call order.
        window: Screenshots per call once the window is full; calls before
            the window filled are left out of the full-window figure.

    Returns:
        Flatness with and without the warm-up calls, mean output, and the
        verdict.
    """
    inputs = [int(u["input_tokens"]) for u in usages]
    full_window = _flatness(inputs[window - 1 :])
    mean_output = statistics.fmean(int(u["output_tokens"]) for u in usages)
    return Summary(
        full_window=full_window,
        all_calls=_flatness(inputs),
        mean_output=mean_output,
        passed=full_window.max_deviation_pct <= MAX_INPUT_DEVIATION_PCT
        and mean_output <= MAX_MEAN_OUTPUT,
    )


def _flatness(inputs: Sequence[int]) -> Flatness:
    mean = statistics.fmean(inputs)
    return Flatness(
        calls=len(inputs),
        mean=mean,
        low=min(inputs),
        high=max(inputs),
        max_deviation_pct=max(abs(n - mean) for n in inputs) / mean * 100,
    )


def _focus_entry(observation: Mapping[str, Any]) -> dict[str, Any]:
    focus = observation["focus"]
    return {
        "step": observation.get("index"),
        "key": observation.get("key"),
        "tag": focus["tag"],
        "role": focus["role"],
        "name": focus["name"],
        "box": focus["box"],
    }
