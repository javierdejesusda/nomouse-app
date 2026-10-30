"""Deterministic walkers that judge a walk record before any model sees it.

W0 applies the keyboard trap, unreachable target and focus-not-visible rules.
W1 adds a geometric order check, a cover check and a focus-management check.
The thresholds below are frozen before the eval; changing one changes every
verdict hash.
"""

import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops

from nomouse.driver import Observation, WalkRecord
from nomouse.witness import Box, Focus

FOCUS_RING_MARGIN_PX = 8
"""Pixels around the focus box searched for a change: Chromium draws its
default focus ring outside the border box, and an author ring may sit a few
pixels further out (for example 2 px wide at a 6 px offset)."""

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
    return _verdicts(
        "W0",
        {
            "trap": _trap(record),
            "unreachable": _unreachable(record),
            "focus-not-visible": _focus_not_visible(record, run_dir),
        },
    )


def _verdicts(
    walker: WalkerName, checks: dict[Rule, Iterator[Finding | None]]
) -> Verdicts:
    """Collects rule outcomes: a Finding when a rule fired, None when it passed."""
    findings: list[Finding] = []
    checked: dict[Rule, int] = {}
    for rule, outcomes in checks.items():
        results = list(outcomes)
        checked[rule] = len(results)
        findings.extend(result for result in results if result is not None)
    return Verdicts(walker=walker, findings=findings, checked=checked)


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
    steps = record.steps
    focus_moved = any(
        step.key == "Escape"
        and step.focus.backend_node_id != steps[step.index - 2].focus.backend_node_id
        for step in steps
    )
    if record.end_reason != "cycle" or focus_moved:
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
    for step in record.steps:
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
