from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from nomouse.export import W3C_NOTICE, export_site
from nomouse.packets import DISCLAIMER
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


def tab_to(page: Page, name: str, limit: int = 40) -> None:
    for _ in range(limit):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.textContent.trim()") == name:
            return
    raise AssertionError(f"Tab never reached {name!r}")


def focus_outline(page: Page) -> str:
    return str(page.evaluate("getComputedStyle(document.activeElement).outlineStyle"))


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


def test_the_site_is_keyboard_operable_with_a_visible_focus(
    replay: tuple[Page, str],
) -> None:
    page, base_url = replay
    page.goto(base_url + "index.html")

    page.keyboard.press("Tab")
    expect(page.locator(":focus")).to_have_text("Skip to main content")
    assert focus_outline(page) != "none"
    tab_to(page, "Menu")
    assert focus_outline(page) != "none"
    page.keyboard.press("Enter")

    expect(page).to_have_url(base_url + "page.html?id=menu")
    expect(page.get_by_role("heading", level=1)).to_have_text("Menu")
    status = page.locator("#step-status")
    expect(status).to_have_text("Before the first key (0 of 3)")
    tab_to(page, "Next key")
    page.keyboard.press("Enter")
    expect(status).to_have_text("Key 1 of 3: Tab")
    page.keyboard.press("Enter")
    expect(status).to_have_text("Key 2 of 3: Enter")
    slider = page.get_by_role("slider", name="Key")
    slider.focus()
    page.keyboard.press("End")
    expect(status).to_have_text("Key 3 of 3: Escape")
    page.keyboard.press("ArrowLeft")
    expect(status).to_have_text("Key 2 of 3: Enter")
    page.keyboard.press("Home")
    expect(status).to_have_text("Before the first key (0 of 3)")
    page.locator("#captures").focus()
    assert focus_outline(page) != "none"
    page.keyboard.press("ArrowRight")
    expect(status).to_have_text("Key 1 of 3: Tab")
    tab_to(page, "Show key 2")
    page.keyboard.press("Enter")
    expect(status).to_have_text("Key 2 of 3: Enter")


def test_a_builder_page_says_builder_made_and_shows_review_and_packets(
    replay: tuple[Page, str],
) -> None:
    page, base_url = replay
    page.goto(base_url + "page.html?id=menu")

    main = page.get_by_role("main")
    expect(main.locator(".provenance")).to_have_text(
        "builder-made: written and labelled by the builder (MIT)"
    )
    expect(main).not_to_contain_text("W3C")
    expect(main.locator("#reviews")).to_contain_text("NEEDS REVIEW")
    expect(main.locator("#reviews")).to_contain_text(
        "The runs disagree on: after Enter at key 2, focus inside what opened"
    )
    expect(main.locator("#packets")).to_contain_text(
        "A model saw focus stay on Menu; a person should confirm."
    )
    expect(main.locator("#packets .disclaimer")).to_have_text([DISCLAIMER] * 2)
    expect(main.locator("#packets")).to_contain_text("Packets of run eval2No packet.")
    expect(page.locator("#capture")).to_have_attribute(
        "src", "captures/menu/initial.png"
    )
    page.get_by_role("button", name="Next key").click()
    expect(page.locator("#capture")).to_have_attribute(
        "src", "captures/menu/step-000.png"
    )
    box = page.locator("#panel .focus-box")
    expect(box).to_have_attribute("x", "10")
    expect(box).to_have_attribute("width", "80")


def test_a_w3c_page_shows_its_url_notice_and_verified_verdicts(
    replay: tuple[Page, str],
) -> None:
    page, base_url = replay
    page.goto(base_url + "page.html?id=w3c-before-survey")

    provenance = page.locator(".provenance")
    expect(provenance.get_by_role("link", name=SURVEY_URL)).to_have_attribute(
        "href", SURVEY_URL
    )
    expect(provenance.get_by_text(W3C_NOTICE, exact=True)).to_be_visible()
    verdicts = page.locator("#verdicts")
    expect(verdicts).to_contain_text("VERIFIED")
    expect(verdicts).to_contain_text("best practice")
    expect(verdicts).to_contain_text("SHA-256")
    expect(verdicts).to_contain_text("frozen")
    expect(page.locator("#reviews")).to_contain_text("No model review")
    page.get_by_role("slider", name="Key").focus()
    page.keyboard.press("End")
    expect(page.locator("#panel .tab-path")).to_have_attribute(
        "points", "140,310 140,210"
    )


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


def test_focus_on_the_document_draws_no_focus_box_or_tab_stop(
    replay: tuple[Page, str],
) -> None:
    page, base_url = replay
    page.goto(base_url + "page.html?id=menu-v1")

    page.get_by_role("button", name="Next key").click()

    expect(page.locator("#step-status")).to_have_text("Key 1 of 1: Tab")
    expect(page.locator("#panel .focus-box")).to_have_count(0)
    expect(page.locator("#panel .tab-stop")).to_have_count(0)
    expect(page.locator("#focus-desc")).to_have_text(
        "Panel: focus on the document (no element box); Tab path of 0 stops."
    )
