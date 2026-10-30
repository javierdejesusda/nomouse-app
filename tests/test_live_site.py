import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect
from test_live import BUILDER, api_reply, copy_menu

from nomouse.credits import CreditLedger
from nomouse.export import export_site
from nomouse.live import LiveService, RateLimiter, live_pages
from nomouse.server import LiveApi, make_server, serve_directory

AUP_URL = "https://dev.meta.ai/legal/acceptable-use-policy"
ISSUES_URL = "https://github.com/javierdejesusda/nomouse/issues"


@pytest.fixture
def open_site(
    fixture_runs: Path, tmp_path: Path, browser: Browser
) -> Iterator[Callable[..., tuple[Page, str]]]:
    site = tmp_path / "site"
    export_site(fixture_runs, site)
    errors: list[str] = []
    stops: list[Callable[[], None]] = []
    pages: list[Page] = []

    def open_(*, key_available: bool = True, live: bool = True) -> tuple[Page, str]:
        service: LiveApi | None = None
        if live:
            service = LiveService(
                site=site,
                state=tmp_path / "state",
                pages=live_pages(BUILDER),
                pipeline=copy_menu(fixture_runs),
                ledger=CreditLedger(tmp_path / "state" / "live-usage.jsonl"),
                limiter=RateLimiter(max_runs=3, window_s=3600),
                transport=api_reply,
                key_available=key_available,
            )
        server = make_server(site, 0, live=service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join()

        stops.append(stop)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        pages.append(page)
        return page, f"http://127.0.0.1:{server.server_address[1]}/"

    yield open_
    for page in pages:
        page.close()
    for stop in stops:
        stop()
    assert errors == []


def test_a_live_run_is_started_from_the_list_and_opens_in_the_viewer(
    open_site: Callable[..., tuple[Page, str]],
) -> None:
    page, base_url = open_site()
    page.goto(base_url + "live.html")

    main = page.get_by_role("main")
    expect(main).to_contain_text("at most 40 keys")
    expect(main).to_contain_text("muse-spark-1.3")
    expect(main).to_contain_text("reasoning effort minimal")
    expect(main).to_contain_text("USD 20.00")
    choice = page.get_by_label("Page to walk")
    expect(choice.locator("option")).to_have_count(26)
    choice.select_option("builder/menu-focus-stays.html")
    choice.focus()
    page.keyboard.press("Tab")
    expect(page.locator(":focus")).to_have_text("Start a live run")
    page.keyboard.press("Enter")

    result = page.get_by_role("link", name="Open the live run")
    expect(result).to_be_focused(timeout=10_000)
    expect(page.locator("#live-status")).to_contain_text("finished")
    result.press("Enter")

    expect(page.get_by_role("heading", level=1)).to_have_text("Menu")
    about = page.locator("#about")
    expect(about).to_contain_text("builder-made")
    expect(about).to_contain_text("Live run of builder/menu-focus-stays.html")
    expect(about).to_contain_text("1 model call")
    expect(about).to_contain_text("muse-spark-1.3, reasoning effort minimal")


def test_without_a_key_live_mode_points_to_the_replay(
    open_site: Callable[..., tuple[Page, str]],
) -> None:
    page, base_url = open_site(key_available=False)
    page.goto(base_url + "live.html")

    expect(page.get_by_role("alert")).to_contain_text(
        "Live mode is off: no Meta Model API key"
    )
    expect(page.get_by_role("button", name="Start a live run")).to_be_disabled()
    page.get_by_label("Page to walk").select_option("before/survey.html")
    replay = page.get_by_role("link", name="Open the replay of W3C Before: Survey")
    expect(replay).to_have_attribute("href", "page.html?id=w3c-before-survey")


def test_a_replay_only_server_says_live_mode_is_not_running(
    open_site: Callable[..., tuple[Page, str]],
) -> None:
    page, base_url = open_site(live=False)
    page.goto(base_url + "live.html")

    expect(page.get_by_role("alert")).to_contain_text(
        "Live mode is not running on this server"
    )
    expect(page.get_by_role("button", name="Start a live run")).to_be_disabled()


def test_every_page_links_privacy_and_the_aup_line(
    fixture_runs: Path, tmp_path: Path, browser: Browser
) -> None:
    site = tmp_path / "site"
    export_site(fixture_runs, site)
    page = browser.new_page()
    with serve_directory(site) as base_url:
        for path in ("index.html", "eval.html", "page.html?id=menu", "live.html"):
            page.goto(base_url + path)
            nav = page.get_by_role("navigation", name="Site")
            expect(nav.get_by_role("link", name="Live run")).to_be_visible()
            expect(nav.get_by_role("link", name="Privacy")).to_be_visible()
            footer = page.get_by_role("contentinfo")
            aup = footer.get_by_role(
                "link", name="Meta Model API Acceptable Use Policy"
            )
            expect(aup).to_have_attribute("href", AUP_URL)
            expect(footer).to_contain_text("must comply with the")
    page.close()


def test_the_privacy_page_says_what_is_sent_retention_and_a_contact(
    fixture_runs: Path, tmp_path: Path, browser: Browser
) -> None:
    site = tmp_path / "site"
    export_site(fixture_runs, site)
    page = browser.new_page()
    with serve_directory(site) as base_url:
        page.goto(base_url + "privacy.html")
        main = page.get_by_role("main")
        expect(main).to_contain_text("screenshots")
        expect(main).to_contain_text("focus box")
        expect(main).to_contain_text("Standard")
        expect(main).to_contain_text(
            "Meta does not use your Content from Standard Services to train Meta"
            " Models."
        )
        expect(main).to_contain_text("no fixed retention period")
        expect(
            main.get_by_role("link", name="Meta Model API Data Commitments")
        ).to_have_attribute("href", "https://dev.meta.ai/legal/commitments")
        expect(main.get_by_role("link", name=ISSUES_URL)).to_have_attribute(
            "href", ISSUES_URL
        )
        assert "@" not in main.inner_text()
    page.close()
