"""Deterministic walkers that judge a walk record before any model sees it.

W0 applies the keyboard trap, unreachable target and focus-not-visible rules.
W1 adds a geometric order check, a cover check and a focus-management check.
The thresholds below are frozen before the eval; changing one changes every
verdict hash.
"""

import datetime
import hashlib
import json
import math
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops

from nomouse.driver import Observation, Step, WalkRecord
from nomouse.witness import Box, Focus

FOCUS_RING_MARGIN_PX = 8
"""Pixels around the focus box searched for a change: Chromium draws its
default focus ring outside the border box, and an author ring may sit a few
pixels further out (for example 2 px wide at a 6 px offset)."""

ORDER_THRESHOLD_PX = 24
"""How far a Tab transition may move up, or left on the same row, before W1
flags it: about one line of 16 px text at 1.5 line height."""

VERDICTS_NAME = "verdicts.json"
FROZEN_AT_NAME = f"{VERDICTS_NAME}.frozen_at"
"""Sidecar holding the UTC freeze time, kept out of the hashed verdicts."""

Rule = Literal[
    "trap", "unreachable", "focus-not-visible", "order", "cover", "focus-management"
]
WalkerName = Literal["W0", "W1"]

# (left, top, right, bottom) in whole screenshot pixels.
PixelBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class Element:
    """An element named without backend node ids, which vary between walks.

    Attributes:
        tag: Upper-case tag name.
        dom_id: The element's `id` attribute, if any.
        role: Accessibility role, when the walk recorded one.
        name: Accessible name, when the walk recorded one.
    """

    tag: str | None
    dom_id: str | None
    role: str | None
    name: str | None


@dataclass(frozen=True)
class Finding:
    """One rule that fired.

    Attributes:
        rule: The rule.
        step: Index of the step it fired on, or None for a page-wide rule.
        element: The element it is about.
        detail: One plain sentence fragment with the measured facts.
    """

    rule: Rule
    step: int | None
    element: Element
    detail: str


@dataclass(frozen=True)
class Verdicts:
    """Everything one walker concluded about one walk.

    Attributes:
        walker: Which walker.
        findings: Rules that fired, in rule order and then step order.
        checked: How many instances each rule evaluated, so a rule that
            found nothing can be told apart from one that never applied.
    """

    walker: WalkerName
    findings: list[Finding]
    checked: dict[Rule, int] = field(default_factory=dict)


def run_w0(record: WalkRecord, run_dir: Path) -> Verdicts:
    """Runs W0 on a walk.

    Args:
        record: The walk record.
        run_dir: The directory holding the walk's screenshots.

    Returns:
        W0's verdicts.
    """
    return _verdicts("W0", _w0_outcomes(record, run_dir))


def run_w1(record: WalkRecord, run_dir: Path) -> Verdicts:
    """Runs W1, which is W0 plus the order, cover and focus-management checks.

    Args:
        record: The walk record.
        run_dir: The directory holding the walk's screenshots.

    Returns:
        W1's verdicts.
    """
    return _verdicts("W1", _w1_outcomes(record, _w0_outcomes(record, run_dir)))


def freeze(record: WalkRecord, run_dir: Path) -> str:
    """Runs W0 and W1 and freezes their verdicts with a SHA-256 hash.

    Writes `verdicts.json` (canonical JSON: the page, the key sequence, the
    thresholds and both walkers' verdicts, with no backend node ids or run
    paths), `verdicts.json.sha256` in `sha256sum` format and
    `verdicts.json.frozen_at`, the UTC freeze time in ISO 8601. The time
    stays out of the hashed file so the same walk gives the same hash.
    Frozen verdicts are never rewritten.

    Args:
        record: The walk record.
        run_dir: The directory holding the walk's screenshots, where the
            verdicts are written.

    Returns:
        The hex SHA-256 of `verdicts.json`.

    Raises:
        FileExistsError: If the run directory already holds verdicts.
    """
    w0 = _w0_outcomes(record, run_dir)
    payload = {
        "page": record.url,
        "keys": record.keys,
        "thresholds": {
            "focus_ring_margin_px": FOCUS_RING_MARGIN_PX,
            "order_threshold_px": ORDER_THRESHOLD_PX,
        },
        "verdicts": [
            asdict(_verdicts("W0", w0)),
            asdict(_verdicts("W1", _w1_outcomes(record, w0))),
        ],
    }
    data = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    verdicts_path = run_dir / VERDICTS_NAME
    with verdicts_path.open("xb") as frozen:
        frozen.write(data)
    frozen_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        (run_dir / f"{VERDICTS_NAME}.sha256").write_text(
            f"{digest}  {VERDICTS_NAME}\n", encoding="utf-8"
        )
        (run_dir / FROZEN_AT_NAME).write_text(f"{frozen_at}\n", encoding="utf-8")
    except BaseException:
        verdicts_path.unlink()
        raise
    return digest


Outcomes = dict[Rule, list[Finding | None]]
"""Per rule, one entry per instance checked: a Finding when it fired, else None."""


def _judged_steps(record: WalkRecord) -> list[Step]:
    """Returns the steps the per-landing rules judge.

    Leaves out the Tab that closes a full cycle, which lands on the first
    element again, and the Escape and Tab that probe a trap.
    """
    if record.repeat_step is not None:
        return record.steps[: record.repeat_step + 1]
    if record.end_reason == "cycle":
        return record.steps[:-1]
    return record.steps


def _w0_outcomes(record: WalkRecord, run_dir: Path) -> Outcomes:
    return {
        "trap": list(_trap(record)),
        "unreachable": list(_unreachable(record)),
        "focus-not-visible": list(_focus_not_visible(record, run_dir)),
    }


def _w1_outcomes(record: WalkRecord, w0: Outcomes) -> Outcomes:
    return w0 | {
        "order": list(_order(record)),
        "cover": list(_cover(record)),
        "focus-management": list(_focus_management(record)),
    }


def _verdicts(walker: WalkerName, outcomes: Outcomes) -> Verdicts:
    return Verdicts(
        walker=walker,
        findings=[
            result for results in outcomes.values() for result in results if result
        ],
        checked={rule: len(results) for rule, results in outcomes.items()},
    )


def _element(focus: Focus) -> Element:
    return Element(tag=focus.tag, dom_id=focus.dom_id, role=focus.role, name=focus.name)


def _trap(record: WalkRecord) -> Iterator[Finding | None]:
    """Yields a trap finding, or None for a trap that Escape leaves.

    Fires when Tab repeats a cycle while other targets exist outside it, and
    neither Escape nor the Tab after it moves focus out of the cycle.
    """
    repeat = record.repeat_step
    if repeat is None:
        return
    trapped = record.steps[repeat].focus
    tabs = [step for step in record.steps[: repeat + 1] if step.key == "Tab"]
    first = next(
        step.index
        for step in tabs
        if step.focus.backend_node_id == trapped.backend_node_id
    )
    cycle = {
        step.focus.backend_node_id
        for step in tabs
        if step.index >= first and not step.focus.is_document
    }
    probe = [step.key for step in record.steps[repeat + 1 :]]
    outside = [t for t in record.targets if cycle.isdisjoint(t.reached_by)]
    if probe != ["Escape", "Tab"] or not outside:
        return
    escape, tab = record.steps[repeat + 1 :]
    if not {escape.focus.backend_node_id, tab.focus.backend_node_id} <= cycle:
        yield None
        return
    yield Finding(
        rule="trap",
        step=repeat,
        element=_element(trapped),
        detail=f"Tab cycles through {len(cycle)} elements; Escape does not leave",
    )


def _unreachable(record: WalkRecord) -> Iterator[Finding | None]:
    """Yields a finding per target never focused, or None per reached one.

    Judged only after a full Tab cycle in which every Escape returned focus
    to the button it was pressed on, so that tabbing went through the page in
    order. Landings on the document do not count.
    """
    if record.end_reason != "cycle":
        return
    steps = record.steps
    if any(
        step.key == "Escape"
        and step.focus.backend_node_id != steps[step.index - 2].focus.backend_node_id
        for step in steps
    ):
        return
    landed = {
        step.focus.backend_node_id for step in steps if not step.focus.is_document
    }
    for target in record.targets:
        if landed.isdisjoint(target.reached_by):
            yield Finding(
                rule="unreachable",
                step=None,
                element=Element(
                    tag=target.tag, dom_id=target.dom_id, role=None, name=None
                ),
                detail="never focused in a full Tab cycle",
            )
        else:
            yield None


def _focus_not_visible(record: WalkRecord, run_dir: Path) -> Iterator[Finding | None]:
    """Yields a finding per Tab landing that changed no pixel near its box.

    Compares the screenshot after the landing with the one before it, over
    the focus box grown by the ring margin and clipped to what both
    screenshots show of that page area. Pixels near the previously focused
    box are ignored, so its ring going away does not count as a change.
    Skips unsettled observations and landings that did not move focus.
    """
    viewport = Box(
        x=0, y=0, width=record.viewport["width"], height=record.viewport["height"]
    )
    previous: Observation = record.initial
    for step in _judged_steps(record):
        before, previous = previous, step
        focus = step.focus
        if (
            step.key != "Tab"
            or focus.is_document
            or focus.box is None
            or not (step.settled and before.settled)
            or focus.backend_node_id == before.focus.backend_node_id
        ):
            continue
        dx = round(step.scroll_x - before.scroll_x)
        dy = round(step.scroll_y - before.scroll_y)
        # Viewport of the earlier screenshot, in this screenshot's pixels.
        earlier = Box(x=-dx, y=-dy, width=viewport.width, height=viewport.height)
        region = _clip(_pixel_box(_grown(focus.box, FOCUS_RING_MARGIN_PX)), viewport)
        region = _clip(region, earlier) if region else None
        if region is None:
            continue
        ignored = None
        if before.focus.box is not None and not before.focus.is_document:
            old = _grown(before.focus.box, FOCUS_RING_MARGIN_PX)
            ignored = _pixel_box(
                Box(x=old.x - dx, y=old.y - dy, width=old.width, height=old.height)
            )
        if _unchanged(
            run_dir / before.screenshot,
            run_dir / step.screenshot,
            region,
            (dx, dy),
            ignored,
        ):
            yield Finding(
                rule="focus-not-visible",
                step=step.index,
                element=_element(focus),
                detail=(
                    f"no pixel changed within {FOCUS_RING_MARGIN_PX} px"
                    " of the focus box"
                ),
            )
        else:
            yield None


def _grown(box: Box, margin: float) -> Box:
    return Box(
        x=box.x - margin,
        y=box.y - margin,
        width=box.width + 2 * margin,
        height=box.height + 2 * margin,
    )


def _clip(box: PixelBox | None, bounds: Box) -> PixelBox | None:
    """Intersects a pixel box with whole-pixel bounds; None when empty."""
    if box is None:
        return None
    limit = _pixel_box(bounds)
    left, top = max(box[0], limit[0]), max(box[1], limit[1])
    right, bottom = min(box[2], limit[2]), min(box[3], limit[3])
    return (left, top, right, bottom) if left < right and top < bottom else None


def _unchanged(
    before_path: Path,
    after_path: Path,
    region: PixelBox,
    scroll: tuple[int, int],
    ignored: PixelBox | None,
) -> bool:
    """Whether a region of the later screenshot matches the earlier one.

    Args:
        before_path: The earlier screenshot.
        after_path: The later screenshot.
        region: The region, in the later screenshot's pixels.
        scroll: How far the page scrolled between the two, (dx, dy).
        ignored: A region, in the later screenshot's pixels, whose changes
            do not count.

    Returns:
        True when no counted pixel differs.
    """
    dx, dy = scroll
    left, top, right, bottom = region
    with Image.open(before_path) as before, Image.open(after_path) as after:
        before_crop = before.convert("RGB").crop(
            (left + dx, top + dy, right + dx, bottom + dy)
        )
        after_crop = after.convert("RGB").crop(region)
    difference = ImageChops.difference(before_crop, after_crop)
    if ignored is not None:
        difference.paste(
            (0, 0, 0),
            (
                ignored[0] - left,
                ignored[1] - top,
                ignored[2] - left,
                ignored[3] - top,
            ),
        )
    return difference.getbbox() is None


def _pixel_box(box: Box) -> PixelBox:
    """Rounds a box outward to whole pixels as (left, top, right, bottom)."""
    return (
        math.floor(box.x),
        math.floor(box.y),
        math.ceil(box.x + box.width),
        math.ceil(box.y + box.height),
    )


def _order(record: WalkRecord) -> Iterator[Finding | None]:
    """Yields a finding per Tab transition that jumps up or left.

    A transition goes from the element focused before a Tab press to the
    element it lands on, in page coordinates (box plus scroll offset; an
    element with fixed or sticky position is therefore placed where it was
    drawn at that scroll). It jumps up when the landing's top edge is more
    than the threshold above the previous one, and left when the two tops are
    within the threshold (the same row) and the landing's left edge is more
    than the threshold to the left. Presses from or to the document, such as
    the wrap past the last element, are not judged.
    """
    previous: Observation = record.initial
    for step in _judged_steps(record):
        before, previous = previous, step
        source, focus = before.focus, step.focus
        if (
            step.key != "Tab"
            or source.is_document
            or focus.is_document
            or source.box is None
            or focus.box is None
            or source.backend_node_id == focus.backend_node_id
        ):
            continue
        up = (source.box.y + before.scroll_y) - (focus.box.y + step.scroll_y)
        left = (source.box.x + before.scroll_x) - (focus.box.x + step.scroll_x)
        if up > ORDER_THRESHOLD_PX:
            detail = f"jumps up {round(up)} px from {_label(source)}"
        elif abs(up) <= ORDER_THRESHOLD_PX and left > ORDER_THRESHOLD_PX:
            detail = (
                f"jumps left {round(left)} px on the same row from {_label(source)}"
            )
        else:
            yield None
            continue
        yield Finding("order", step.index, _element(focus), detail)


def _cover(record: WalkRecord) -> Iterator[Finding | None]:
    """Yields a finding per Tab landing whose sampled box is all covered."""
    for step in _judged_steps(record):
        cover = step.focus.cover
        if step.key != "Tab" or cover is None or cover.sampled == 0:
            continue
        if cover.covered == cover.sampled:
            yield Finding(
                rule="cover",
                step=step.index,
                element=_element(step.focus),
                detail=(
                    f"all {cover.sampled} sampled points are covered by other content"
                ),
            )
        else:
            yield None


def _focus_management(record: WalkRecord) -> Iterator[Finding | None]:
    """Yields findings on where focus goes when Enter opens something.

    After Enter, the elements that appeared form the opened set, and focus
    must be on one of them. After the Escape that follows, focus must be
    back on the trigger, the element Enter was pressed on. An Enter that
    opened nothing is not judged, nor is its Escape.
    """
    steps = _judged_steps(record)
    for step in steps:
        if step.key != "Enter" or not step.appeared or step.index == 0:
            continue
        trigger = steps[step.index - 1].focus
        if step.focus.backend_node_id in step.appeared:
            yield None
        else:
            yield Finding(
                rule="focus-management",
                step=step.index,
                element=_element(trigger),
                detail=(
                    f"after Enter, focus is outside the {len(step.appeared)}"
                    " elements that appeared"
                ),
            )
        escape = steps[step.index + 1] if step.index + 1 < len(steps) else None
        if escape is None or escape.key != "Escape":
            continue
        if escape.focus.backend_node_id == trigger.backend_node_id:
            yield None
        else:
            yield Finding(
                rule="focus-management",
                step=escape.index,
                element=_element(trigger),
                detail=(
                    "after Escape, focus did not return to the trigger"
                    f" (now {_label(escape.focus)})"
                ),
            )


def _label(focus: Focus) -> str:
    return f"{focus.tag}#{focus.dom_id}" if focus.dom_id else str(focus.tag)
