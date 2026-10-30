from pathlib import Path

import pytest
from playwright.sync_api import Browser

from nomouse.cli import BUILDER_ROOT

PAGES = sorted(BUILDER_ROOT.glob("*.html"))


def test_the_builder_directory_holds_pages() -> None:
    assert PAGES


@pytest.mark.parametrize("path", PAGES, ids=lambda path: path.name)
def test_builder_pages_visibly_say_builder_made_and_mit(
    browser: Browser, path: Path
) -> None:
    page = browser.new_page()
    try:
        page.goto(path.as_uri())
        label = page.get_by_text("builder-made").first

        assert label.is_visible()
        assert "MIT" in label.inner_text()
    finally:
        page.close()
