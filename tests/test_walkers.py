import datetime
import hashlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext

from nomouse.driver import KeySession, new_walk_context, walk
from nomouse.server import serve_directory
from nomouse.walkers import Element, Finding, freeze, run_w0, run_w1

PAGES_DIR = Path(__file__).parent / "pages"


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    context = new_walk_context(browser)
    yield context
    context.close()


def element(tag: str, dom_id: str, role: str, name: str) -> Element:
    return Element(tag=tag, dom_id=dom_id, role=role, name=name)


def w0_findings(
    context: BrowserContext, url: str, run_dir: Path, rule: str
) -> list[Finding]:
    record = walk(context, url, run_dir)
    return [f for f in run_w0(record, run_dir).findings if f.rule == rule]


def test_w0_flags_a_trap_that_escape_does_not_leave(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "trap.html", tmp_path)

    verdicts = run_w0(record, tmp_path)

    assert verdicts.walker == "W0"
    assert verdicts.findings == [
        Finding(
            rule="trap",
            step=4,
            element=Element(tag="INPUT", dom_id="first", role="textbox", name="First"),
            detail="Tab cycles through 2 elements; Escape does not leave",
        )
    ]
    assert verdicts.checked["trap"] == 1


def test_w0_passes_a_trap_that_escape_leaves(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    assert w0_findings(context, site + "trap-escape.html", tmp_path, "trap") == []


def test_w0_flags_targets_never_focused_in_a_full_cycle(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "unreachable.html", tmp_path)

    verdicts = run_w0(record, tmp_path)

    assert [f for f in verdicts.findings if f.rule == "unreachable"] == [
        Finding(
            rule="unreachable",
            step=None,
            element=Element(tag="DIV", dom_id="fake", role=None, name=None),
            detail="never focused in a full Tab cycle",
        )
    ]
    assert verdicts.checked["unreachable"] == 3


def test_w0_does_not_judge_reachability_without_a_full_cycle(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "unreachable.html", tmp_path, max_keys=1)

    verdicts = run_w0(record, tmp_path)

    assert verdicts.findings == []
    assert verdicts.checked["unreachable"] == 0


def test_w0_flags_focus_that_changes_no_pixel_around_the_focus_box(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "outline.html", tmp_path)

    verdicts = run_w0(record, tmp_path)

    bare_step = next(s.index for s in record.steps if s.focus.dom_id == "bare")
    assert [f for f in verdicts.findings if f.rule == "focus-not-visible"] == [
        Finding(
            rule="focus-not-visible",
            step=bare_step,
            element=Element(tag="INPUT", dom_id="bare", role="textbox", name="Bare"),
            detail="no pixel changed within 8 px of the focus box",
        )
    ]
    assert verdicts.checked["focus-not-visible"] >= 2


def test_w0_finds_nothing_on_a_page_without_barriers(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "focus.html", tmp_path)

    assert run_w0(record, tmp_path).findings == []


def test_w0_passes_a_trap_whose_escape_moves_focus_out_before_tab_returns(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    assert w0_findings(context, site + "trap-return.html", tmp_path, "trap") == []


def test_w0_ignores_the_previous_focus_ring_disappearing_next_to_the_box(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    findings = w0_findings(
        context, site + "adjacent.html", tmp_path, "focus-not-visible"
    )

    assert [finding.element.dom_id for finding in findings] == ["bare"]


def test_w0_checks_focus_visibility_at_the_viewport_edge(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    findings = w0_findings(context, site + "edge.html", tmp_path, "focus-not-visible")

    assert [finding.element.dom_id for finding in findings] == ["corner"]


def test_w0_sees_a_ring_drawn_away_from_the_box(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    findings = w0_findings(context, site + "offset.html", tmp_path, "focus-not-visible")

    assert findings == []


def test_w0_does_not_judge_reachability_after_enter_moved_focus_away(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "jump.html", tmp_path)

    verdicts = run_w0(record, tmp_path)

    assert [f for f in verdicts.findings if f.rule == "unreachable"] == []
    assert verdicts.checked["unreachable"] == 0


def w1_findings(
    context: BrowserContext, url: str, run_dir: Path, rule: str
) -> list[Finding]:
    record = walk(context, url, run_dir)
    return [f for f in run_w1(record, run_dir).findings if f.rule == rule]


def test_w1_flags_tab_transitions_that_jump_up_or_left(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "order.html", tmp_path)

    verdicts = run_w1(record, tmp_path)

    steps = {step.focus.dom_id: step.index for step in record.steps}
    assert verdicts.walker == "W1"
    assert [f for f in verdicts.findings if f.rule == "order"] == [
        Finding(
            rule="order",
            step=steps["c"],
            element=element("INPUT", "c", "textbox", "C"),
            detail="jumps up 200 px from INPUT#b",
        ),
        Finding(
            rule="order",
            step=steps["e"],
            element=element("INPUT", "e", "textbox", "E"),
            detail="jumps left 400 px on the same row from INPUT#d",
        ),
    ]
    assert verdicts.checked["order"] == 5


def test_w1_keeps_the_w0_rules(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    findings = w1_findings(context, site + "trap.html", tmp_path, "trap")

    assert [finding.element.dom_id for finding in findings] == ["first"]


def test_w1_flags_a_focused_element_entirely_covered(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    findings = w1_findings(context, site + "cover.html", tmp_path, "cover")

    assert [(f.element.dom_id, f.detail) for f in findings] == [
        ("hidden", "all 9 sampled points are covered by other content")
    ]


def test_w1_flags_focus_left_outside_what_enter_opened_or_not_returned(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "manage.html", tmp_path)

    verdicts = run_w1(record, tmp_path)

    keys = [(step.key, step.focus.dom_id) for step in record.steps]
    assert keys[:6] == [
        ("Tab", "menu"),
        ("Enter", "menu"),
        ("Escape", "menu"),
        ("Tab", "modal"),
        ("Enter", "ok"),
        ("Escape", None),
    ]
    assert [f for f in verdicts.findings if f.rule == "focus-management"] == [
        Finding(
            rule="focus-management",
            step=1,
            element=element("BUTTON", "menu", "button", "Menu"),
            detail="after Enter, focus is outside the 3 elements that appeared",
        ),
        Finding(
            rule="focus-management",
            step=5,
            element=element("BUTTON", "modal", "button", "Modal"),
            detail="after Escape, focus did not return to the trigger (now BODY)",
        ),
    ]
    assert verdicts.checked["focus-management"] == 4


def test_w1_passes_a_dialog_that_takes_and_returns_focus(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "dialog.html", tmp_path)

    verdicts = run_w1(record, tmp_path)

    assert verdicts.findings == []
    assert verdicts.checked["focus-management"] == 2


def test_freeze_writes_verdicts_and_their_sha256(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "order.html", tmp_path)

    digest = freeze(record, tmp_path)

    payload = (tmp_path / "verdicts.json").read_bytes()
    assert digest == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "verdicts.json.sha256").read_text(encoding="utf-8") == (
        f"{digest}  verdicts.json\n"
    )
    frozen = json.loads(payload)
    assert frozen["page"] == "/order.html"
    assert frozen["keys"] == record.keys
    assert frozen["thresholds"] == {
        "focus_ring_margin_px": 8,
        "order_threshold_px": 24,
    }
    assert [v["walker"] for v in frozen["verdicts"]] == ["W0", "W1"]
    assert [f["rule"] for f in frozen["verdicts"][1]["findings"]] == [
        "order",
        "order",
    ]


def test_freeze_records_its_utc_time_beside_the_hashed_verdicts(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "order.html", tmp_path / "a")
    before = datetime.datetime.now(datetime.UTC).replace(microsecond=0)

    freeze(record, tmp_path / "a")

    after = datetime.datetime.now(datetime.UTC)
    stamp = (tmp_path / "a" / "verdicts.json.frozen_at").read_text(encoding="utf-8")
    assert stamp.endswith("Z\n")
    frozen_at = datetime.datetime.fromisoformat(stamp.strip())
    assert before <= frozen_at <= after
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    (tmp_path / "b" / "verdicts.json").unlink()
    (tmp_path / "b" / "verdicts.json.sha256").unlink()
    assert freeze(record, tmp_path / "b") == freeze_digest(tmp_path / "a")


def freeze_digest(run_dir: Path) -> str:
    return hashlib.sha256((run_dir / "verdicts.json").read_bytes()).hexdigest()


def test_freeze_refuses_to_overwrite_frozen_verdicts(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "order.html", tmp_path)
    freeze(record, tmp_path)

    with pytest.raises(FileExistsError):
        freeze(record, tmp_path)


def test_freeze_gives_the_same_hash_for_the_same_page_on_another_port(
    browser: Browser, site: str, tmp_path: Path
) -> None:
    def frozen_hash(base_url: str, run_dir: Path) -> str:
        context = new_walk_context(browser)
        try:
            return freeze(walk(context, base_url + "manage.html", run_dir), run_dir)
        finally:
            context.close()

    with serve_directory(PAGES_DIR) as other_site:
        assert frozen_hash(other_site, tmp_path / "a") == frozen_hash(
            site, tmp_path / "b"
        )


def test_w1_judges_order_only_from_a_focused_element(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "manage.html", tmp_path)

    verdicts = run_w1(record, tmp_path)

    hidden_source = record.steps[6]
    assert (record.steps[5].key, record.steps[5].focus.tag) == ("Escape", "BODY")
    assert hidden_source.key == "Tab"
    judged = [
        step.index
        for step in record.steps[1:]
        if step.key == "Tab"
        and not step.focus.is_document
        and not record.steps[step.index - 1].focus.is_document
    ]
    assert hidden_source.index not in judged
    assert verdicts.checked["order"] == len(judged)


def test_freeze_leaves_no_verdicts_when_the_hash_cannot_be_written(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    record = walk(context, site + "order.html", tmp_path)
    (tmp_path / "verdicts.json.sha256").mkdir()

    with pytest.raises(IsADirectoryError):
        freeze(record, tmp_path)

    assert not (tmp_path / "verdicts.json").exists()


def test_w1_judges_neither_the_cycle_closing_landing_nor_the_trap_probe(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    cover_record = walk(context, site + "cover.html", tmp_path / "cover")
    trap_record = walk(context, site + "trap.html", tmp_path / "trap")

    cover = run_w1(cover_record, tmp_path / "cover")
    trap = run_w1(trap_record, tmp_path / "trap")

    assert cover.checked["cover"] == 3
    assert trap.checked["order"] == 4
    assert all(f.step is None or f.step <= 4 for f in trap.findings)


def test_freeze_judges_a_navigator_run_that_starts_with_escape(
    context: BrowserContext, site: str, tmp_path: Path
) -> None:
    session = KeySession(context, site + "focus.html", tmp_path)
    session.press("Escape")
    record = session.finish("stuck", navigator={"task": "Close it"})

    freeze(record, tmp_path)

    verdicts = json.loads((tmp_path / "verdicts.json").read_text(encoding="utf-8"))
    assert verdicts["keys"] == ["Escape"]
    assert all(v["checked"]["unreachable"] == 0 for v in verdicts["verdicts"])
