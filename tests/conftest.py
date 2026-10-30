import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from playwright.sync_api import Browser, sync_playwright

from nomouse.packets import DISCLAIMER
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


def _png(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (64, 48), color).save(path)


def _focus(tag: str, role: str, name: str, x: float, y: float) -> dict[str, Any]:
    return {
        "tag": tag,
        "dom_id": None,
        "role": role,
        "name": name,
        "box": {"x": x, "y": y, "width": 80, "height": 20},
        "document_has_focus": True,
    }


def _frozen(run_dir: Path, verdicts: dict[str, Any]) -> str:
    raw = (json.dumps(verdicts, sort_keys=True) + "\n").encode()
    (run_dir / "verdicts.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (run_dir / "verdicts.json.sha256").write_text(f"{digest}  verdicts.json\n")
    return digest


def _review(run_dir: Path, run: str, digest: str, inside: str) -> None:
    review = {
        "page": "/menu.html",
        "run": run,
        "verdicts_sha256": digest,
        "prompt_sha256": "p" * 64,
        "model": "muse-spark-1.3",
        "reasoning_effort": "minimal",
        "instances": [
            {
                "form": "i",
                "steps": {"trigger": 0, "enter": 1, "escape": 2},
                "answer": {
                    "opened": "yes",
                    "inside": {"answer": inside, "reason": f"Inside {inside}."},
                    "returned": {"answer": "pass", "reason": "Back on Menu."},
                },
                "finding": inside == "fail",
                "status": "NEEDS REVIEW" if inside == "fail" else None,
            }
        ],
    }
    (run_dir / f"review-{run}.json").write_text(json.dumps(review))


def write_fixture_runs(root: Path) -> Path:
    """Writes a runs directory: one W3C walk, one builder walk and its old run.

    `eval/w3c-before-survey` has an order finding at step 1 (Tab, Tab).
    `builder/menu` (Tab to Menu, Enter, Escape) has reviews `eval1` (inside
    fail) and `eval2` (inside pass) and packets (none in `eval2`).
    `builder/menu-v1` (Tab lands on the document) has a `menu-v1-v2`
    successor. `eval/eval-report.json` holds two arms.
    """
    survey = root / "eval" / "w3c-before-survey"
    menu = root / "builder" / "menu"
    for run_dir in (survey, menu, root / "builder" / "menu-v1"):
        run_dir.mkdir(parents=True)
    (root / "builder" / "menu-v1-v2").mkdir()
    name = _focus("INPUT", "textbox", "Name", 100, 300)
    email = _focus("INPUT", "textbox", "eMail", 100, 200)
    menu_button = _focus("BUTTON", "button", "Menu", 10, 10)
    body = _focus("BODY", "none", "", 0, 0)
    walks: dict[Path, tuple[str, list[str], list[dict[str, Any]]]] = {
        survey: ("/before/survey.html", ["Tab", "Tab"], [name, email]),
        menu: ("/menu.html", ["Tab", "Enter", "Escape"], [menu_button] * 3),
        root / "builder" / "menu-v1": ("/menu.html", ["Tab"], [body]),
        root / "builder" / "menu-v1-v2": ("/menu.html", ["Tab"], [menu_button]),
    }
    order = {
        "rule": "order",
        "step": 1,
        "element": {"tag": "INPUT", "dom_id": "em", "role": "textbox", "name": "eMail"},
        "detail": "jumps up 100 px from INPUT#n",
    }
    for index, (run_dir, (page, keys, path)) in enumerate(walks.items()):
        _png(run_dir / "initial.png", (index * 40, 0, 0))
        steps = []
        for step, (key, focus) in enumerate(zip(keys, path, strict=True)):
            _png(run_dir / f"step-{step:03}.png", (index * 40, step * 40, 200))
            steps.append(
                {
                    "index": step,
                    "key": key,
                    "focus": focus,
                    "scroll_x": 0,
                    "scroll_y": 0,
                    "screenshot": f"step-{step:03}.png",
                    "appeared": [5, 6] if key == "Enter" else [],
                }
            )
        record = {
            "url": page,
            "viewport": {"width": 1024, "height": 768},
            "keys": keys,
            "initial": {
                "focus": {**_focus("BODY", "none", "", 0, 0), "box": None},
                "scroll_x": 0,
                "scroll_y": 0,
                "screenshot": "initial.png",
                "appeared": [],
            },
            "steps": steps,
        }
        (run_dir / "walk.json").write_text(json.dumps(record))
        findings = [order] if run_dir == survey else []
        digest = _frozen(
            run_dir,
            {
                "page": page,
                "keys": keys,
                "thresholds": {"order_threshold_px": 24},
                "verdicts": [
                    {"walker": "W0", "checked": {"trap": 0}, "findings": []},
                    {"walker": "W1", "checked": {"order": 1}, "findings": findings},
                ],
            },
        )
        if run_dir == menu:
            _review(menu, "eval1", digest, "fail")
            _review(menu, "eval2", digest, "pass")
            packet = {
                "id": "P1",
                "status": "NEEDS REVIEW",
                "best_practice": False,
                "rule": "focus-management",
                "step": 1,
                "sentence": "A model saw focus stay on Menu; a person should confirm.",
                "disclaimer": DISCLAIMER,
            }
            for run, packets in (("eval1", [packet]), ("eval2", [])):
                (menu / f"packets-{run}.json").write_text(
                    json.dumps({"disclaimer": DISCLAIMER, "packets": packets})
                )
    counts = {
        "failures": 22,
        "found": 9,
        "verified": 9,
        "needs_review": 0,
        "found_loose": 9,
        "clean": 20,
        "false_flags": 9,
        "false_flags_strict": 8,
        "clean_pages": 12,
        "clean_pages_any_finding": 8,
    }
    report = {
        "runs": ["eval1"],
        "labels_sha256": {"builder": "b" * 64, "w3c": "c" * 64},
        "arms": {
            "W1": {**counts, "builder": counts, "w3c": counts},
            "NoMouse eval1": {**counts, "found": 12, "builder": counts, "w3c": counts},
        },
        "bar": {"runs": {"eval1": {"model_only_catches": 3}}, "met": True},
        "model_only": {"eval1": ["dialog-fades-in"]},
        "disagreements": {"NoMouse": []},
        "rows": [{"id": "before-survey-06", "note": "W3C annotation text"}],
    }
    (root / "eval" / "eval-report.json").write_text(json.dumps(report))
    return root


AUDIT_REASON = "A carrier tag matched a named link that is no carrier."


def write_audit(path: Path) -> Path:
    """Writes an audit correction of the fixture report's W1 strict false flags."""
    audit = {
        "date": "2026-10-30",
        "source": "eval/results.md, Audit",
        "reason": AUDIT_REASON,
        "false_flags_strict": {"W1": 5},
    }
    path.write_text(json.dumps(audit))
    return path


@pytest.fixture
def fixture_runs(tmp_path: Path) -> Path:
    return write_fixture_runs(tmp_path / "runs")
