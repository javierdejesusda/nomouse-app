import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nomouse.live import LivePage, live_pages, walk_and_review
from nomouse.packets import WRITER_PROMPT
from nomouse.reviewer import FORM_I_PROMPT

BUILDER = Path(__file__).resolve().parents[1] / "builder"
USAGE = {"input_tokens": 4_000, "output_tokens": 200, "total_tokens": 4_200}


def test_live_pages_are_the_w3c_pages_without_news_and_the_builder_pages() -> None:
    pages = live_pages(BUILDER)

    ids = [page.id for page in pages]
    assert ids[:6] == [
        "before/home.html",
        "before/tickets.html",
        "before/survey.html",
        "after/home.html",
        "after/tickets.html",
        "after/survey.html",
    ]
    assert not any("news" in page_id for page_id in ids)
    builder = [page for page in pages if page.set == "builder"]
    assert len(builder) == 20
    assert {page.id for page in builder} == {
        f"builder/{path.name}" for path in BUILDER.glob("*.html")
    }
    home = pages[0]
    assert (home.set, home.title, home.replay_id) == (
        "w3c",
        "W3C Before: Home",
        "w3c-before-home",
    )
    menu = next(p for p in pages if p.id == "builder/menu-focus-stays.html")
    assert (menu.title, menu.replay_id) == (
        "Menu focus stays (builder-made)",
        "menu-focus-stays",
    )


class ModelTransport:
    """Answers the reviewer and the packet writer like the API."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        request = json.loads(body)
        self.bodies.append(request)
        text = request["input"][0]["content"][0]["text"]
        if text.startswith(FORM_I_PROMPT):
            asked = json.loads(text.removeprefix(FORM_I_PROMPT))["instances"]
            answer: dict[str, Any] = {
                "instances": [
                    {
                        "step": item["step"],
                        "opened": "yes",
                        "inside": {"answer": "pass", "reason": "On Close."},
                        "returned": {"answer": "pass", "reason": "Back on Open."},
                    }
                    for item in asked
                ]
            }
        else:
            assert text.startswith(WRITER_PROMPT)
            findings = json.loads(text.removeprefix(WRITER_PROMPT))["findings"]
            answer = {
                "sentences": [
                    {"id": f["id"], "sentence": "Observed."} for f in findings
                ]
            }
        reply = {
            "model": "muse-spark-1.3",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(answer)}],
                }
            ],
            "usage": USAGE,
        }
        return 200, json.dumps(reply).encode()


def test_the_pipeline_walks_freezes_reviews_and_writes_packets(
    tmp_path: Path,
) -> None:
    pages = Path(__file__).parent / "pages"
    page = LivePage("builder/dialog.html", "Dialog", "builder", "dialog")
    transport = ModelTransport()
    stages: list[str] = []
    run_dir = tmp_path / "live-1"

    walk_and_review(page, run_dir, transport, stages.append, roots={"builder": pages})

    assert stages == ["walking", "reviewing", "writing packets"]
    record = json.loads((run_dir / "walk.json").read_text())
    assert record["url"] == "/dialog.html"
    assert record["keys"][:3] == ["Tab", "Enter", "Escape"]
    assert (run_dir / "verdicts.json.sha256").is_file()
    assert (run_dir / "verdicts.json.frozen_at").is_file()
    review = json.loads((run_dir / "review-live.json").read_text())
    assert review["model"] == "muse-spark-1.3"
    assert review["reasoning_effort"] == "minimal"
    assert len(review["instances"]) >= 1
    assert (run_dir / "packets-live.json").is_file()
    assert transport.bodies
    for body in transport.bodies:
        assert body["model"] == "muse-spark-1.3"
        assert body["reasoning"] == {"effort": "minimal"}


def test_the_pipeline_stops_the_walk_at_forty_keys(tmp_path: Path) -> None:
    root = tmp_path / "pages"
    root.mkdir()
    links = "".join(f'<a href="#l{n}">Link {n}</a>\n' for n in range(60))
    (root / "many.html").write_text(
        f'<!doctype html><html lang="en"><title>Many</title><body>{links}</body></html>'
    )
    page = LivePage("builder/many.html", "Many", "builder", "many")
    run_dir = tmp_path / "live-1"

    walk_and_review(
        page, run_dir, ModelTransport(), lambda stage: None, roots={"builder": root}
    )

    record = json.loads((run_dir / "walk.json").read_text())
    assert len(record["keys"]) == 40
    assert record["end_reason"] == "max_keys"
