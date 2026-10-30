from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext

from nomouse.driver import new_walk_context, walk
from nomouse.walkers import Element, Finding, run_w0


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    context = new_walk_context(browser)
    yield context
    context.close()


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

    assert {finding.element.dom_id for finding in findings} == {"corner"}


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
