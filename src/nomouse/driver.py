"""Deterministic keyboard driver that walks one page and records focus."""

import collections
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from playwright.sync_api import Browser, BrowserContext, Page, ViewportSize

from nomouse.guard import BlockedRequest, RequestGuard
from nomouse.witness import Focus, FocusWitness

VIEWPORT: ViewportSize = {"width": 1024, "height": 768}
DEFAULT_MAX_KEYS = 300
RECORD_NAME = "walk.json"

# Chromium reports <summary> as DisclosureTriangle; it is activated with Enter
# like any other button, so the driver treats it as one.
BUTTON_ROLES = frozenset({"button", "DisclosureTriangle"})

EndReason = Literal["cycle", "repeat", "max_keys"]

# Two full cycles of a keyboard trap land on its first element three times.
_REPEAT_VISITS = 3

_TWO_FRAMES = (
    "() => new Promise(resolve =>"
    " requestAnimationFrame(() => requestAnimationFrame(resolve)))"
)


@dataclass(frozen=True, kw_only=True)
class Observation:
    """Page state captured after a key press, or before the first one.

    Attributes:
        url: The page URL.
        scroll_x: Horizontal scroll offset in CSS pixels.
        scroll_y: Vertical scroll offset in CSS pixels.
        focus: The focused element.
        screenshot: File name of the viewport screenshot in the run directory.
    """

    url: str
    scroll_x: float
    scroll_y: float
    focus: Focus
    screenshot: str


@dataclass(frozen=True, kw_only=True)
class Step(Observation):
    """Page state after one key press.

    Attributes:
        index: Position of the key in the walk, from 0.
        key: The key pressed.
    """

    index: int
    key: str


@dataclass(frozen=True, kw_only=True)
class WalkRecord:
    """Everything a walk observed, one step per key pressed.

    Attributes:
        url: The walked page.
        viewport: Viewport size in CSS pixels.
        end_reason: Why the walk stopped: a full Tab cycle, a Tab target
            reached a third time, or the key cap.
        keys: The key sequence, in order.
        initial: Page state before the first key.
        steps: Page state after each key.
        blocked_requests: Requests the guard aborted during the walk.
    """

    url: str
    viewport: ViewportSize
    end_reason: EndReason
    keys: list[str]
    initial: Observation
    steps: list[Step]
    blocked_requests: list[BlockedRequest]

    def to_dict(self) -> dict[str, Any]:
        """Returns the record as JSON-serializable data."""
        return asdict(self)


class _TabCycle:
    """Decides when the sequence of Tab landings has covered the page.

    A cycle is complete once Tab has moved focus out of the page (past the
    last element) and then lands on a node it reached before. Landings on the
    document while it keeps focus, such as a link that blurs itself on focus,
    are neither a wrap nor a visit.
    """

    def __init__(self) -> None:
        self._seen: set[int | None] = set()
        self._visits: collections.Counter[int | None] = collections.Counter()
        self._left_page = False

    def land(self, focus: Focus) -> EndReason | None:
        """Records where a Tab press landed.

        Args:
            focus: Focus after the Tab press.

        Returns:
            The reason to end the walk, or None to keep going.
        """
        node = focus.backend_node_id
        if self._left_page and node in self._seen:
            return "cycle"
        self._seen.add(node)
        if not focus.document_has_focus:
            self._left_page = True
        if focus.is_document:
            return None
        self._visits[node] += 1
        return "repeat" if self._visits[node] >= _REPEAT_VISITS else None


def new_walk_context(browser: Browser) -> BrowserContext:
    """Creates a browser context suited to a walk.

    Service workers are blocked so that every request goes through the guard.

    Args:
        browser: A Chromium browser.

    Returns:
        A new context with a 1024 px wide viewport at device scale factor 1.
    """
    return browser.new_context(
        viewport=VIEWPORT, device_scale_factor=1, service_workers="block"
    )


def walk(
    context: BrowserContext,
    url: str,
    run_dir: Path,
    *,
    max_keys: int = DEFAULT_MAX_KEYS,
) -> WalkRecord:
    """Walks a page with the keyboard and records focus after every key.

    The driver presses Tab through the page and, the first time Tab lands on
    a button, presses Enter and then Escape. Links are never activated. After
    every key it records the focused element through CDP and takes a viewport
    screenshot. The walk ends after one full Tab cycle (Tab moved focus out of
    the page and then landed on a node it had reached before), when one Tab
    target is reached a third time (a keyboard trap), or at `max_keys`.

    The guard is installed on the context and pinned to the page, so the walk
    cannot leave the page, its origin, or send a non-GET request. The page is
    left open in the context.

    Args:
        context: A context from `new_walk_context`, used for this walk only.
        url: The page to walk.
        run_dir: Directory for the record and screenshots; created if missing.
        max_keys: Upper bound on the number of keys pressed.

    Returns:
        The walk record, also written to `run_dir / RECORD_NAME`.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    guard = RequestGuard(url)
    guard.install(context)
    page = context.new_page()
    page.set_viewport_size(VIEWPORT)
    page.goto(url)
    guard.pin(page.url)
    witness = FocusWitness(context.new_cdp_session(page))

    initial = _observe(page, witness, run_dir, "initial.png")
    steps: list[Step] = []

    def press(key: str) -> Step:
        page.keyboard.press(key)
        page.evaluate(_TWO_FRAMES)
        index = len(steps)
        state = _observe(page, witness, run_dir, f"step-{index:03d}.png")
        step = Step(index=index, key=key, **vars(state))
        steps.append(step)
        return step

    cycle = _TabCycle()
    pressed_buttons: set[int | None] = set()
    end_reason: EndReason = "max_keys"
    while len(steps) < max_keys:
        focus = press("Tab").focus
        reason = cycle.land(focus)
        if reason is not None:
            end_reason = reason
            break
        if focus.role in BUTTON_ROLES and focus.backend_node_id not in pressed_buttons:
            pressed_buttons.add(focus.backend_node_id)
            for key in ("Enter", "Escape"):
                if len(steps) < max_keys:
                    press(key)

    record = WalkRecord(
        url=url,
        viewport=VIEWPORT,
        end_reason=end_reason,
        keys=[step.key for step in steps],
        initial=initial,
        steps=steps,
        blocked_requests=list(guard.blocked),
    )
    (run_dir / RECORD_NAME).write_text(
        json.dumps(record.to_dict(), indent=2), encoding="utf-8"
    )
    return record


def _observe(
    page: Page, witness: FocusWitness, run_dir: Path, screenshot: str
) -> Observation:
    page.screenshot(
        path=run_dir / screenshot, animations="disabled", caret="hide", scale="css"
    )
    scroll_x, scroll_y = witness.scroll()
    return Observation(
        url=page.url,
        scroll_x=scroll_x,
        scroll_y=scroll_y,
        focus=witness.focus(),
        screenshot=screenshot,
    )
