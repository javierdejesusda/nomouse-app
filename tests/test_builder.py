import hashlib
import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from nomouse.cli import BUILDER_ROOT

PAGES = sorted(BUILDER_ROOT.glob("*.html"))
LABELS_PATH = BUILDER_ROOT / "labels.json"
FORMS = {"order", "focus-management", "trap"}
STATUSES = {"failure", "clean"}


def load_labels() -> list[dict[str, object]]:
    labels: list[dict[str, object]] = json.loads(
        LABELS_PATH.read_text(encoding="utf-8")
    )["pages"]
    return labels


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


def test_the_labels_hash_file_matches_the_labels() -> None:
    digest = hashlib.sha256(LABELS_PATH.read_bytes()).hexdigest()

    assert (BUILDER_ROOT / "labels.json.sha256").read_text(encoding="utf-8") == (
        f"{digest}  labels.json\n"
    )


def test_every_builder_page_has_exactly_one_label_and_every_label_a_page() -> None:
    labelled = [str(label["page"]) for label in load_labels()]

    assert sorted(labelled) == [path.name for path in PAGES]


def test_labels_name_a_form_a_status_a_barrier_and_ids_on_the_page() -> None:
    for label in load_labels():
        html = (BUILDER_ROOT / str(label["page"])).read_text(encoding="utf-8")
        page_ids = set(re.findall(r'\bid="([^"]+)"', html))
        carrier_ids = label["carrier_ids"]

        assert label["form"] in FORMS, label["page"]
        assert label["status"] in STATUSES, label["page"]
        assert isinstance(label["barrier"], str) and label["barrier"], label["page"]
        assert isinstance(label["transition"], str), label["page"]
        assert isinstance(carrier_ids, list) and carrier_ids, label["page"]
        assert set(carrier_ids) <= page_ids, label["page"]
