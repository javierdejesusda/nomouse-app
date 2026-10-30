from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, sync_playwright

from nomouse.server import serve_directory

PAGES_DIR = Path(__file__).parent / "pages"


@pytest.fixture(scope="session")
def site() -> Iterator[str]:
    with serve_directory(PAGES_DIR) as base_url:
        yield base_url


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()
