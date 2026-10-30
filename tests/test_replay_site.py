from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from nomouse.export import W3C_NOTICE, export_site
from nomouse.server import serve_directory

SURVEY_URL = "https://www.w3.org/WAI/demos/bad/before/survey.html"


@pytest.fixture
def replay(
    fixture_runs: Path, tmp_path: Path, browser: Browser
) -> Iterator[tuple[Page, str]]:
    site = tmp_path / "site"
    export_site(fixture_runs, site)
    errors: list[str] = []
    with serve_directory(site) as base_url:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        yield page, base_url
        page.close()
    assert errors == []


def test_the_index_lists_w3c_pages_with_their_notice_and_builder_pages(
    replay: tuple[Page, str],
) -> None:
    page, base_url = replay
    page.goto(base_url + "index.html")

    for role in ("banner", "navigation", "main", "contentinfo"):
        expect(page.get_by_role(role).first).to_be_visible()
    w3c = page.locator("#w3c-pages")
    expect(w3c.get_by_role("link", name="Survey")).to_be_visible()
    expect(w3c.get_by_text(SURVEY_URL)).to_be_visible()
    expect(w3c.get_by_text(W3C_NOTICE, exact=True)).to_be_visible()
    builder = page.locator("#builder-pages")
    expect(builder.get_by_role("listitem")).to_have_count(3)
    for item in builder.get_by_role("listitem").all():
        expect(item).to_contain_text("builder-made")
    expect(builder).to_contain_text("superseded by menu-v1-v2")
    expect(builder).not_to_contain_text(W3C_NOTICE)


def test_the_eval_page_shows_counts_and_the_reference_point(
    replay: tuple[Page, str],
) -> None:
    page, base_url = replay
    page.goto(base_url + "eval.html")

    table = page.get_by_role("table").first
    expect(table.get_by_role("row")).to_have_count(3)
    expect(table).to_contain_text("NoMouse eval1")
    expect(table).to_contain_text("12 of 22")
    expect(page.get_by_role("main")).to_contain_text(
        "Reference point only: arXiv 2609.09379 reports 56% precision"
    )
