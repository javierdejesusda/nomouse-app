"""Live runs on allowlisted pages, under a lifetime credit cap.

A live run walks one page from a fixed list (the W3C demo pages without
News, and the builder pages), freezes the walker verdicts, asks the form (i)
reviewer and writes packets, then adds the run to the replay site. With a
typed task, the keyboard navigator chooses the keys instead of the
deterministic driver, under the same page list, key cap and guard. One run
goes at a time, each client may start only a few runs per window, and when
the credit cap is used up, no key is configured or the API cannot be
reached, live mode is off and the replay carries the page.
"""

import collections
import datetime
import logging
import threading
import time
import urllib.error
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from nomouse import navigator, packets, reviewer
from nomouse.credits import CreditLedger
from nomouse.driver import new_walk_context, walk
from nomouse.export import BUILDER_LABEL, export_run
from nomouse.mirror import WALK_PAGES
from nomouse.server import serve_directory
from nomouse.spark import MODEL, REASONING_EFFORT, SparkError, Transport
from nomouse.walkers import freeze

LIVE_MAX_KEYS = 40
REVIEW_RUN = "live"
RUN_RESERVE_USD = 0.25
"""The most one 40-key run is expected to cost; a run starts only while the
spend plus this reserve stays within the cap. The T10 eval reviews cost
under USD 0.03 a page."""
TASK_RESERVE_USD = 0.75
"""The most one run with a typed task is expected to cost: up to 41
navigator calls with up to five screenshots each, then the review."""
API_PAUSE_S = 600.0
"""How long live mode stays off after a run could not reach the API."""
MAX_STORED_RUNS = 200
"""Live runs kept on disk before live mode turns off. A run of a page
with no button makes no model call, so the credit cap alone does not bound
the disk; a 40-key run takes up to about 25 MB with its replay copy."""

_REPLAY_NOTE = "The replay of every frozen run still works with no key."
_log = logging.getLogger(__name__)

Progress = Callable[[str], None]
Pipeline = Callable[["LivePage", Path, Transport, Progress, str | None], None]
"""Runs one live run: (page, run directory, metered transport, progress,
typed task or None)."""


@dataclass(frozen=True)
class LivePage:
    """One page a live run may walk.

    Attributes:
        id: `before/home.html` style for a W3C page, `builder/<file>.html`
            for a builder page; the only values a client may send.
        title: The name shown in the page list.
        set: `w3c` or `builder`.
        replay_id: The id of the page's frozen run in the replay.
    """

    id: str
    title: str
    set: str
    replay_id: str


def live_pages(builder_root: Path) -> tuple[LivePage, ...]:
    """Returns the fixed list of pages a live run may walk.

    Args:
        builder_root: The directory of the builder pages.

    Returns:
        The six W3C pages (News is never listed), then every builder page.
    """
    w3c = [
        LivePage(
            id=path,
            title=f"W3C {path.split('/')[0].title()}: {Path(path).stem.title()}",
            set="w3c",
            replay_id="w3c-" + path.removesuffix(".html").replace("/", "-"),
        )
        for path in WALK_PAGES
    ]
    builder = [
        LivePage(
            id=f"builder/{path.name}",
            title=f"{path.stem.replace('-', ' ').capitalize()} ({BUILDER_LABEL})",
            set="builder",
            replay_id=path.stem,
        )
        for path in sorted(builder_root.glob("*.html"))
    ]
    return (*w3c, *builder)


def walk_and_review(
    page: LivePage,
    run_dir: Path,
    transport: Transport,
    progress: Progress,
    task: str | None,
    *,
    roots: Mapping[str, Path],
) -> None:
    """Runs one live run: walk or navigate, freeze, form (i) review, packets.

    The page is served from its root on 127.0.0.1 and walked with at most
    40 keys under the walk's request guard (no other origin, no non-GET
    request, no navigation off the page): by the deterministic driver, or,
    with a task, by the keyboard navigator. The verdicts are frozen before
    any review call.

    Args:
        page: A listed page.
        run_dir: New directory for the run.
        transport: Sends the model calls.
        progress: Told each stage as it starts.
        task: A task cleaned by `navigator.clean_task`, or None.
        roots: The directory serving each page set (`w3c`, `builder`).

    Raises:
        SparkError: A navigator or review call failed.
        reviewer.AnswerError: A review answer did not fit its question.
    """
    progress("walking" if task is None else "navigating")
    path = page.id.removeprefix("builder/")
    with serve_directory(roots[page.set]) as base_url, sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = new_walk_context(browser)
            if task is None:
                record = walk(context, base_url + path, run_dir, max_keys=LIVE_MAX_KEYS)
            else:
                record = navigator.navigate(
                    context,
                    base_url + path,
                    run_dir,
                    task,
                    transport=transport,
                    max_keys=LIVE_MAX_KEYS,
                )
        finally:
            browser.close()
    freeze(record, run_dir)
    progress("reviewing")
    reviewer.review(run_dir, run=REVIEW_RUN, transport=transport)
    progress("writing packets")
    packets.write_packets(run_dir, run=REVIEW_RUN, transport=transport)


class RateLimiter:
    """Allows each client a fixed number of run starts per sliding window."""

    def __init__(
        self,
        max_runs: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initializes the limiter.

        Args:
            max_runs: Starts allowed per client within one window.
            window_s: The window in seconds.
            clock: Monotonic clock in seconds.
        """
        self.max_runs = max_runs
        self.window_s = window_s
        self._clock = clock
        self._starts: dict[str, collections.deque[float]] = {}

    def allow(self, client: str) -> bool:
        """Records a start for a client if the client is under its limit.

        Args:
            client: The client's address.

        Returns:
            Whether the start is allowed.
        """
        now = self._clock()
        for key in list(self._starts):
            starts = self._starts[key]
            while starts and starts[0] <= now - self.window_s:
                starts.popleft()
            if not starts:
                del self._starts[key]
        starts = self._starts.setdefault(client, collections.deque())
        if len(starts) >= self.max_runs:
            return False
        starts.append(now)
        return True


class LiveService:
    """Starts live runs one at a time and reports their state."""

    def __init__(
        self,
        *,
        site: Path,
        state: Path,
        pages: Sequence[LivePage],
        pipeline: Pipeline,
        ledger: CreditLedger,
        limiter: RateLimiter,
        transport: Transport,
        key_available: bool,
        clock: Callable[[], float] = time.monotonic,
        max_stored_runs: int = MAX_STORED_RUNS,
    ) -> None:
        """Initializes the service.

        Args:
            site: The exported replay site that live runs are added to.
            state: Directory for live run directories.
            pages: The pages a live run may walk.
            pipeline: Runs one live run.
            ledger: The lifetime credit ledger.
            limiter: The per-client start limit.
            transport: Sends the model calls; metered per run.
            key_available: Whether an API key is configured.
            clock: Monotonic clock in seconds.
            max_stored_runs: Live runs kept in the state directory before
                live mode turns off.
        """
        self._site = site
        self._state = state
        self._pages = {page.id: page for page in pages}
        self._pipeline = pipeline
        self._ledger = ledger
        self._limiter = limiter
        self._transport = transport
        self._key_available = key_available
        self._clock = clock
        self._max_stored_runs = max_stored_runs
        self._lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None
        self._paused_until = float("-inf")

    def status(self) -> dict[str, Any]:
        """Returns whether live mode is on, why not, the caps and the pages."""
        message = self._off_reason()
        return {
            "live": message is None,
            "message": message,
            "busy": self._busy(),
            "max_keys": LIVE_MAX_KEYS,
            "max_task_chars": navigator.MAX_TASK_CHARS,
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "cap_usd": self._ledger.cap_usd,
            "spent_usd": self._spent(),
            "pages": [
                {**asdict(page), "replay_id": self._replay_id(page)}
                for page in self._pages.values()
            ],
        }

    def start(
        self, client: str, page_id: object, task: object = None
    ) -> tuple[int, dict[str, Any]]:
        """Starts a live run of a listed page, if live mode allows it.

        Args:
            client: The client's address, for the rate limit.
            page_id: The page id the client sent.
            task: The typed task the client sent, or None for a driver walk.
                It is untrusted text: it goes to the navigator only, cleaned
                and capped, and changes neither the page, the key cap nor
                the key whitelist.

        Returns:
            An HTTP status and its JSON body: 202 with the run id; 400 for a
            page that is not listed or a task that is not one short line of
            text; 503 when live mode is off, or for a task when the cap
            cannot take the larger task reserve; 409 while another run is
            going; 429 when the client is over its limit.
        """
        page = self._pages.get(page_id) if isinstance(page_id, str) else None
        if page is None:
            return 400, {
                "error": "unknown_page",
                "message": "Choose one of the listed pages; live runs take no"
                " other address.",
            }
        if task is not None:
            try:
                task = navigator.clean_task(task)
            except ValueError:
                return 400, {
                    "error": "bad_task",
                    "message": "The task must be one line of text, at most"
                    f" {navigator.MAX_TASK_CHARS} characters.",
                }
        fallback = {"replay_id": self._replay_id(page)}
        # Checked under the lock, after the busy check, so the cap is read
        # only once the previous run has recorded all of its calls.
        with self._lock:
            if self._busy():
                return 409, {
                    "error": "busy",
                    "message": "A live run is already going (one at a time)."
                    " Try again when it finishes, or open the replay.",
                    **fallback,
                }
            if (message := self._off_reason()) is not None:
                return 503, {"error": "live_off", "message": message, **fallback}
            if task is not None and (message := self._task_off_reason()):
                return 503, {"error": "live_off", "message": message, **fallback}
            if not self._limiter.allow(client):
                return 429, {
                    "error": "rate_limited",
                    "message": f"This address has started {self._limiter.max_runs}"
                    f" live runs in the last {self._limiter.window_s / 60:.0f}"
                    " minutes; try later, or open the replay.",
                    **fallback,
                }
            run_id = self._new_run_id()
            self._runs[run_id] = {
                "run": run_id,
                "page": page.id,
                "task": task,
                "state": "queued",
                "message": None,
                "page_id": None,
                "cost_usd": 0.0,
                **fallback,
            }
            self._thread = threading.Thread(
                target=self._run, args=(run_id, page, task), daemon=True
            )
            self._thread.start()
        return 202, {"run": run_id}

    def run(self, run_id: str) -> dict[str, Any] | None:
        """Returns a live run's state, or None for an unknown run id.

        Args:
            run_id: The id `start` returned.
        """
        with self._lock:
            state = self._runs.get(run_id)
            return None if state is None else dict(state)

    def join(self) -> None:
        """Waits for the current run, if any, to end."""
        if self._thread is not None:
            self._thread.join()

    def _run(self, run_id: str, page: LivePage, task: str | None) -> None:
        run_dir = self._state / "runs" / run_id
        started = _utc_now()

        def progress(stage: str) -> None:
            self._update(run_id, state=stage)

        try:
            self._pipeline(
                page,
                run_dir,
                self._ledger.metered(self._transport, run_id),
                progress,
                task,
            )
            cost = self._ledger.spent_usd(run_id)
            live = {
                "page": page.id,
                "task": task,
                "started_at": started,
                "finished_at": _utc_now(),
                "max_keys": LIVE_MAX_KEYS,
                "model": MODEL,
                "reasoning_effort": REASONING_EFFORT,
                "calls": self._ledger.calls(run_id),
                "cost_usd": cost,
            }
            export_run(run_dir, self._site, review_runs=(REVIEW_RUN,), live=live)
        except (SparkError, urllib.error.URLError, TimeoutError, ConnectionError) as e:
            _log.warning("live run %s: API call failed (%s)", run_id, _kind(e))
            self._paused_until = self._clock() + API_PAUSE_S
            self._fail(
                run_id,
                "The live run stopped: it could not reach the Meta Model API, or"
                " the API refused the call. Live mode is paused for a few minutes;"
                " the replay below needs no key.",
            )
        except Exception as error:
            _log.warning("live run %s failed (%s)", run_id, _kind(error))
            self._fail(
                run_id,
                "The live run could not finish. The replay below needs no key.",
            )
        else:
            self._update(run_id, state="done", page_id=run_id, cost_usd=cost)

    def _fail(self, run_id: str, message: str) -> None:
        self._update(run_id, state="failed", message=message)

    def _update(self, run_id: str, **changes: Any) -> None:
        with self._lock:
            self._runs[run_id].update(changes)

    def _busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _spent(self) -> float | None:
        try:
            return self._ledger.spent_usd()
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _off_reason(self) -> str | None:
        """Returns why live mode is off, or None when it is on."""
        if not self._key_available:
            return (
                "Live mode is off: no Meta Model API key is configured on this"
                f" server. {_REPLAY_NOTE}"
            )
        spent = self._spent()
        if spent is None:
            return (
                "Live mode is off: the live credit ledger cannot be read."
                f" {_REPLAY_NOTE}"
            )
        cap = self._ledger.cap_usd
        if spent + RUN_RESERVE_USD > cap:
            return (
                f"Live mode is off: the lifetime live credit cap of USD {cap:.2f}"
                f" is used up (USD {spent:.2f} spent; a run may cost up to USD"
                f" {RUN_RESERVE_USD:.2f}). {_REPLAY_NOTE}"
            )
        runs = self._state / "runs"
        if runs.is_dir() and len(list(runs.iterdir())) >= self._max_stored_runs:
            return (
                f"Live mode is off: this server keeps at most"
                f" {self._max_stored_runs} live runs, and they are all used."
                f" {_REPLAY_NOTE}"
            )
        if self._clock() < self._paused_until:
            return (
                "Live mode is paused: the last live run could not reach the Meta"
                f" Model API. Try again in a few minutes. {_REPLAY_NOTE}"
            )
        return None

    def _task_off_reason(self) -> str | None:
        """Returns why a run with a typed task cannot start, or None."""
        spent = self._spent() or 0.0
        cap = self._ledger.cap_usd
        if spent + TASK_RESERVE_USD <= cap:
            return None
        return (
            f"A run with a typed task may cost up to USD {TASK_RESERVE_USD:.2f},"
            f" more than the USD {cap - spent:.2f} left under the lifetime live"
            " credit cap. Start a run without a task, or open the replay."
        )

    def _replay_id(self, page: LivePage) -> str | None:
        """Returns the replay page of a page's frozen run, if exported.

        A page revised before any model run has a `-v2` run that replaces
        the first one.
        """
        for replay_id in (f"{page.replay_id}-v2", page.replay_id):
            if (self._site / "data" / "pages" / f"{replay_id}.json").is_file():
                return replay_id
        return None

    def _new_run_id(self) -> str:
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        run_id, number = f"live-{stamp}", 1
        while run_id in self._runs or (self._state / "runs" / run_id).exists():
            number += 1
            run_id = f"live-{stamp}-{number}"
        return run_id


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _kind(error: Exception) -> str:
    """Names an error for the server log without its message."""
    if isinstance(error, SparkError):
        return f"HTTP {error.status}"
    return type(error).__name__
