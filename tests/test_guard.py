from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext

from nomouse.guard import BlockedRequest, RequestGuard
from nomouse.server import serve_directory


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    context = browser.new_context(service_workers="block")
    yield context
    context.close()


@pytest.fixture
def other_origin(tmp_path: Path) -> Iterator[str]:
    (tmp_path / "index.html").write_text("<p>other origin</p>")
    with serve_directory(tmp_path) as base_url:
        yield base_url


def test_guard_aborts_non_get_requests(context: BrowserContext, site: str) -> None:
    guard = RequestGuard(site)
    guard.install(context)
    page = context.new_page()
    page.goto(site + "requests.html")

    outcome = page.evaluate(
        "fetch('landing.html', {method: 'POST'}).then(r => r.status, () => 'aborted')"
    )

    assert outcome == "aborted"
    assert guard.blocked == [
        BlockedRequest(method="POST", url=site + "landing.html", reason="non-GET")
    ]


def test_guard_lets_same_origin_get_requests_through(
    context: BrowserContext, site: str
) -> None:
    guard = RequestGuard(site)
    guard.install(context)
    page = context.new_page()
    page.goto(site + "requests.html")

    status = page.evaluate("fetch('landing.html').then(r => r.status)")

    assert status == 200
    assert guard.blocked == []


def test_guard_aborts_off_origin_navigation(
    context: BrowserContext, site: str, other_origin: str
) -> None:
    guard = RequestGuard(site)
    guard.install(context)
    page = context.new_page()
    page.goto(site + "requests.html")

    with page.expect_event("requestfailed"):
        page.evaluate("url => { window.location.href = url; }", other_origin)

    assert page.url == site + "requests.html"
    assert guard.blocked == [
        BlockedRequest(method="GET", url=other_origin, reason="off-origin")
    ]


def test_guard_aborts_off_origin_subresources(
    context: BrowserContext, site: str, other_origin: str
) -> None:
    guard = RequestGuard(site)
    guard.install(context)
    page = context.new_page()
    page.goto(site + "requests.html")

    outcome = page.evaluate(
        "url => fetch(url).then(r => r.status, () => 'aborted')", other_origin
    )

    assert outcome == "aborted"
    assert [request.reason for request in guard.blocked] == ["off-origin"]


def test_pinned_guard_aborts_navigation_away_from_the_page(
    context: BrowserContext, site: str
) -> None:
    guard = RequestGuard(site)
    guard.install(context)
    page = context.new_page()
    page.goto(site + "requests.html")
    guard.pin(page.url)

    with page.expect_event("requestfailed"):
        page.focus("#submit")
        page.keyboard.press("Enter")

    assert page.url == site + "requests.html"
    assert guard.blocked == [
        BlockedRequest(
            method="GET",
            url=site + "landing.html?",
            reason="off-page navigation",
        )
    ]
