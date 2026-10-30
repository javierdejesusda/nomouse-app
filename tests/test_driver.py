import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext

from nomouse.driver import WalkRecord, new_walk_context, walk
from nomouse.guard import BlockedRequest
from nomouse.server import serve_directory
from nomouse.witness import Box

PAGES_DIR = Path(__file__).parent / "pages"


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    context = new_walk_context(browser)
    yield context
    context.close()


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def tab_steps(record: WalkRecord) -> list[tuple[str | None, str | None, str | None]]:
    return [
        (step.focus.dom_id, step.focus.role, step.focus.name)
        for step in record.steps
        if step.key == "Tab"
    ]


def test_walk_records_focus_box_role_and_name_after_each_tab(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "focus.html", tmp_path)

    landings = tab_steps(record)
    assert landings[:3] == [
        ("save", "button", "Save"),
        ("docs", "link", "Docs"),
        ("email", "textbox", "Email"),
    ]
    assert landings[4] == ("save", "button", "Save")
    past_last = [step for step in record.steps if step.key == "Tab"][3]
    assert past_last.focus.tag == "BODY"
    assert past_last.focus.is_document
    first_tab = record.steps[0]
    assert first_tab.focus.tag == "BUTTON"
    assert first_tab.focus.box == Box(x=100, y=50, width=120, height=30)
    assert first_tab.focus.backend_node_id is not None


def test_walk_presses_enter_then_escape_on_each_button(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "focus.html", tmp_path)

    assert record.keys == ["Tab", "Enter", "Escape", "Tab", "Tab", "Tab", "Tab"]
    page = context.pages[0]
    keylog = page.evaluate("window.keylog")
    assert keylog[1:3] == [["Enter", "save"], ["Escape", "save"]]
    assert page.evaluate("window.saveClicks") == 1


def test_walk_never_activates_links(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    walk(context, site + "focus.html", tmp_path)

    page = context.pages[0]
    keys_on_link = [
        key for key, target in page.evaluate("window.keylog") if target == "docs"
    ]
    assert keys_on_link == ["Tab"]
    assert page.evaluate("window.linkActivations") == 0


def test_walk_records_focus_after_enter_and_escape(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "dialog.html", tmp_path)

    assert [(step.key, step.focus.dom_id) for step in record.steps] == [
        ("Tab", "open"),
        ("Enter", "close"),
        ("Escape", "open"),
        ("Tab", None),
        ("Tab", "open"),
    ]


def test_walk_takes_a_1024_px_screenshot_after_every_key(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "focus.html", tmp_path)

    shots = [record.initial.screenshot] + [step.screenshot for step in record.steps]
    assert len(set(shots)) == len(record.keys) + 1
    for shot in shots:
        assert png_size(tmp_path / shot) == (1024, 768)


def test_walk_writes_one_record_entry_per_key(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "focus.html", tmp_path)

    written = json.loads((tmp_path / "walk.json").read_text(encoding="utf-8"))
    assert written == record.to_dict()
    assert written["url"] == "/focus.html"
    assert written["viewport"] == {"width": 1024, "height": 768}
    assert [step["key"] for step in written["steps"]] == written["keys"]
    assert [step["index"] for step in written["steps"]] == list(
        range(len(written["keys"]))
    )
    assert written["steps"][0]["focus"]["box"] == {
        "x": 100,
        "y": 50,
        "width": 120,
        "height": 30,
    }


def test_walk_ends_after_one_full_tab_cycle(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "focus.html", tmp_path)

    assert record.end_reason == "cycle"
    assert record.steps[-1].focus.dom_id == record.steps[0].focus.dom_id


def test_walk_ends_on_a_page_without_focusable_elements(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "blank.html", tmp_path)

    assert record.end_reason == "cycle"
    assert record.keys == ["Tab", "Tab"]


def test_walk_ends_when_tab_focus_repeats_inside_a_trap(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "trap.html", tmp_path)

    assert record.end_reason == "repeat"
    assert [step.focus.dom_id for step in record.steps] == [
        "first",
        "second",
        "first",
        "second",
        "first",
    ]


def test_walk_stops_at_the_key_cap(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "focus.html", tmp_path, max_keys=2)

    assert record.end_reason == "max_keys"
    assert record.keys == ["Tab", "Enter"]
    assert len(record.steps) == 2


def test_walk_stays_on_the_page_and_records_blocked_requests(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    start = site + "requests.html"

    record = walk(context, start, tmp_path)

    assert record.end_reason == "cycle"
    assert {step.url for step in record.steps} == {"/requests.html"}
    assert record.blocked_requests == [
        BlockedRequest(method="POST", url="/landing.html", reason="non-GET"),
        BlockedRequest(
            method="GET", url="/landing.html?", reason="off-page navigation"
        ),
    ]


def test_walk_records_viewport_boxes_and_scroll_offsets(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "scroll.html", tmp_path)

    bottom = next(step for step in record.steps if step.focus.dom_id == "bottom")
    assert bottom.focus.box is not None
    assert 0 <= bottom.focus.box.y <= 768 - bottom.focus.box.height
    assert bottom.scroll_y > 0
    offset_top = context.pages[0].evaluate(
        "document.getElementById('bottom').offsetTop"
    )
    assert bottom.scroll_y + bottom.focus.box.y == offset_top
    assert record.initial.scroll_y == 0


def test_walk_continues_past_links_that_blur_on_focus(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "blur.html", tmp_path)

    tabs = [step.focus for step in record.steps if step.key == "Tab"]
    assert [focus.dom_id for focus in tabs] == [
        "before",
        None,
        None,
        "after",
        None,
        "before",
    ]
    assert [focus.document_has_focus for focus in tabs] == [
        True,
        True,
        True,
        True,
        False,
        True,
    ]
    assert record.end_reason == "cycle"


def test_walk_records_accessible_names_with_whitespace_collapsed(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "names.html", tmp_path)

    names = {step.focus.dom_id: step.focus.name for step in record.steps}
    assert names["spaced"] == "Save draft"
    assert names["nbsp"] == "Save\u00a0draft"


def test_walk_records_the_same_urls_whatever_the_server_port(
    browser: Browser, site: str, tmp_path: Path
) -> None:
    def urls(base_url: str, run_dir: Path) -> list[str]:
        context = new_walk_context(browser)
        try:
            record = walk(context, base_url + "requests.html", run_dir)
        finally:
            context.close()
        return (
            [record.url, record.initial.url]
            + [step.url for step in record.steps]
            + [blocked.url for blocked in record.blocked_requests]
        )

    with serve_directory(PAGES_DIR) as other_site:
        assert other_site != site
        assert urls(other_site, tmp_path / "other") == urls(site, tmp_path / "site")


def test_walk_keeps_off_origin_urls_absolute(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "offsite.html", tmp_path)

    assert record.blocked_requests == [
        BlockedRequest(
            method="GET", url="http://localhost:9/pixel.png", reason="off-origin"
        )
    ]


def test_walk_follows_focus_into_a_same_origin_frame(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "frame.html", tmp_path)

    first_tab = record.steps[0].focus
    assert (first_tab.tag, first_tab.dom_id, first_tab.role, first_tab.name) == (
        "BUTTON",
        "framed",
        "button",
        "Framed",
    )
    assert first_tab.box == Box(x=310, y=320, width=100, height=30)


def test_walk_records_a_scrollable_frame_without_focusable_content_as_the_frame(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "blankframe.html", tmp_path)

    first_tab = record.steps[0].focus
    assert (first_tab.tag, first_tab.dom_id) == ("IFRAME", "scroller")
    assert not first_tab.is_document


def test_walk_records_the_canonical_page_url(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "./focus.html", tmp_path)

    assert record.url == "/focus.html"
