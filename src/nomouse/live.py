"""Live runs on allowlisted pages, under a lifetime credit cap.

A live run walks one page from a fixed list (the W3C demo pages without
News, and the builder pages), freezes the walker verdicts, asks the form (i)
reviewer and writes packets, then adds the run to the replay site. One run
goes at a time, each client may start only a few runs per window, and when
the credit cap is used up, no key is configured or the API cannot be
reached, live mode is off and the replay carries the page.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from nomouse import packets, reviewer
from nomouse.driver import new_walk_context, walk
from nomouse.export import BUILDER_LABEL
from nomouse.mirror import WALK_PAGES
from nomouse.server import serve_directory
from nomouse.spark import Transport
from nomouse.walkers import freeze

LIVE_MAX_KEYS = 40
REVIEW_RUN = "live"

Progress = Callable[[str], None]
Pipeline = Callable[["LivePage", Path, Transport, Progress], None]
"""Runs one live run: (page, run directory, metered transport, progress)."""


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
    *,
    roots: Mapping[str, Path],
) -> None:
    """Runs one live run: walk, freeze, form (i) review, then packets.

    The page is served from its root on 127.0.0.1 and walked with at most
    40 keys under the walk's request guard (no other origin, no non-GET
    request). The verdicts are frozen before any model call.

    Args:
        page: A listed page.
        run_dir: New directory for the run.
        transport: Sends the model calls.
        progress: Told each stage as it starts.
        roots: The directory serving each page set (`w3c`, `builder`).

    Raises:
        SparkError: A review call failed.
        reviewer.AnswerError: A review answer did not fit its question.
    """
    progress("walking")
    path = page.id.removeprefix("builder/")
    with serve_directory(roots[page.set]) as base_url, sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            record = walk(
                new_walk_context(browser),
                base_url + path,
                run_dir,
                max_keys=LIVE_MAX_KEYS,
            )
        finally:
            browser.close()
    freeze(record, run_dir)
    progress("reviewing")
    reviewer.review(run_dir, run=REVIEW_RUN, transport=transport)
    progress("writing packets")
    packets.write_packets(run_dir, run=REVIEW_RUN, transport=transport)
