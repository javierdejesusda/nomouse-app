import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from nomouse.cli import main
from nomouse.scorer import ScoreError, score

PROMPT = "p" * 64


def label(page: str, form: str, status: str, kind: str, carriers: list[str]) -> dict:
    return {
        "page": f"{page}.html",
        "form": form,
        "status": status,
        "kind": kind,
        "w1_design": None,
        "barrier": "Barrier.",
        "transition": "Enter on #x" if form == "focus-management" else "Tab",
        "carrier_ids": carriers,
    }


LABELS = [
    label("mono-a", "order", "failure", "monotone", ["a1", "a2", "a3"]),
    label("mono-b", "order", "failure", "monotone", ["b1", "b2"]),
    label("mono-c", "order", "failure", "monotone", ["c1", "c2"]),
    label("geo", "order", "failure", "geometric", ["g1", "g2"]),
    label("clean-1", "order", "clean", "non-top-to-bottom", ["k1"]),
    label("clean-2", "order", "clean", "non-top-to-bottom", ["k2"]),
    label("fm-miss-1", "focus-management", "failure", "dialog", ["open1", "dlg1"]),
    label("fm-miss-2", "focus-management", "failure", "dialog", ["open2", "dlg2"]),
    label("fm-caught", "focus-management", "failure", "menu", ["open3"]),
    label("fm-clean", "focus-management", "clean", "disclosure", ["t1", "t2"]),
    label("trap", "trap", "failure", "trap", ["w"]),
]


def write_labels(root: Path, pages: list[dict[str, Any]] = LABELS) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "labels.json"
    path.write_text(json.dumps({"about": "x", "pages": pages}), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "labels.json.sha256").write_text(f"{digest}  labels.json\n")
    return path


def focus(dom_id: str | None) -> dict[str, Any]:
    return {"tag": "BODY" if dom_id is None else "BUTTON", "dom_id": dom_id}


def write_run(
    root: Path,
    page: str,
    walk: list[tuple[str, str | None]],
    instances: list[dict[str, Any]],
    *,
    w1_rules: Sequence[str] = (),
    run: str = "gate",
    prompt: str = PROMPT,
) -> Path:
    """Writes a walk (key, focused id per step), frozen verdicts and a review."""
    run_dir = root / page
    run_dir.mkdir(parents=True)
    record = {
        "url": f"/{page}.html",
        "initial": {"focus": focus(None)},
        "steps": [
            {"index": i, "key": key, "focus": focus(dom_id)}
            for i, (key, dom_id) in enumerate(walk)
        ],
    }
    (run_dir / "walk.json").write_text(json.dumps(record), encoding="utf-8")
    verdicts = json.dumps(
        {
            "verdicts": [
                {"walker": "W0", "findings": []},
                {"walker": "W1", "findings": [{"rule": r} for r in w1_rules]},
            ]
        }
    ).encode()
    (run_dir / "verdicts.json").write_bytes(verdicts)
    digest = hashlib.sha256(verdicts).hexdigest()
    (run_dir / "verdicts.json.sha256").write_text(f"{digest}  verdicts.json\n")
    usage = {
        "calls": 2,
        "input_tokens": 1000,
        "cached_tokens": 0,
        "output_tokens": 100,
        "reasoning_tokens": 10,
        "total_tokens": 1100,
    }
    review = {
        "page": record["url"],
        "run": run,
        "verdicts_sha256": digest,
        "prompt_sha256": prompt,
        "instances": instances,
        "usage": usage,
    }
    (run_dir / f"review-{run}.json").write_text(json.dumps(review), encoding="utf-8")
    return run_dir


def tab(frm: int | None, to: int, answer: str) -> dict[str, Any]:
    return {
        "form": "ii",
        "steps": {"from": frm, "to": to},
        "answer": {"answer": answer, "reason": f"{answer} reason"},
        "finding": answer == "breaks",
    }


def enter(trigger: int, inside: str, returned: str = "pass") -> dict[str, Any]:
    return {
        "form": "i",
        "steps": {"trigger": trigger, "enter": trigger + 1, "escape": trigger + 2},
        "answer": {
            "opened": "yes",
            "inside": {"answer": inside, "reason": "inside reason"},
            "returned": {"answer": returned, "reason": "returned reason"},
        },
        "finding": "fail" in (inside, returned),
    }


ORDER_WALK: list[tuple[str, str | None]] = [
    ("Tab", "nav"),
    ("Tab", "a1"),
    ("Tab", "a2"),
    ("Tab", "a3"),
]
FM_WALK: list[tuple[str, str | None]] = [
    ("Tab", "nav"),
    ("Tab", "open1"),
    ("Enter", "open1"),
    ("Escape", "open1"),
]


def gate_runs(root: Path) -> list[Path]:
    """Runs where strict and loose counts differ on purpose."""
    return [
        # Strict catch: breaks on a2 -> a3, both carriers.
        write_run(
            root, "mono-a", ORDER_WALK, [tab(0, 1, "follows"), tab(2, 3, "breaks")]
        ),
        # Loose only: breaks on nav -> b1, nav is not a carrier.
        write_run(
            root,
            "mono-b",
            [("Tab", "nav"), ("Tab", "b1"), ("Tab", "b2")],
            [tab(0, 1, "breaks"), tab(1, 2, "follows")],
        ),
        # Strict catch, but W1 flagged order here, so outside the gate.
        write_run(
            root,
            "mono-c",
            [("Tab", "c1"), ("Tab", "c2")],
            [tab(0, 1, "breaks")],
            w1_rules=["order"],
        ),
        write_run(
            root, "clean-1", [("Tab", "k1"), ("Tab", "z")], [tab(0, 1, "breaks")]
        ),
        # A form (i) finding on an order page is not an order false flag.
        write_run(
            root,
            "clean-2",
            [("Tab", "k2"), ("Enter", "k2"), ("Escape", "z")],
            [enter(0, "pass", "fail")],
        ),
        write_run(root, "fm-miss-1", FM_WALK, [enter(1, "fail")]),
        # Loose only: the finding is on returned, not inside.
        write_run(
            root,
            "fm-miss-2",
            [("Tab", "nav"), ("Tab", "open2"), ("Enter", "open2"), ("Escape", "x")],
            [enter(1, "pass", "fail")],
        ),
        write_run(
            root,
            "fm-caught",
            [("Tab", "open3"), ("Enter", "open3"), ("Escape", "open3")],
            [enter(0, "fail")],
            w1_rules=["focus-management"],
        ),
        write_run(
            root,
            "fm-clean",
            [("Tab", "t1"), ("Enter", "t1"), ("Escape", "t1")],
            [enter(0, "pass"), tab(None, 0, "breaks")],
            w1_rules=["focus-management"],
        ),
    ]


def test_score_counts_strict_and_loose_and_judges_both_gates(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    runs = gate_runs(tmp_path / "runs")

    result = score(labels, runs, run="gate")

    assert result["labels_sha256"] == hashlib.sha256(labels.read_bytes()).hexdigest()
    assert result["prompt_sha256"] == [PROMPT]
    assert result["usage"]["input_tokens"] == 9000
    assert result["usage"]["calls"] == 18
    g3a = result["gates"]["G3a"]
    assert g3a["failures"] == ["mono-a.html", "mono-b.html"]
    assert g3a["caught_strict"] == ["mono-a.html"]
    assert g3a["caught_loose"] == ["mono-a.html", "mono-b.html"]
    assert g3a["clean"] == ["clean-1.html", "clean-2.html"]
    assert g3a["false_flags"] == ["clean-1.html"]
    assert g3a["complete"] is True
    assert g3a["verdict"] == "FAIL"
    fm = result["gates"]["focus-management"]
    assert fm["failures"] == ["fm-miss-1.html", "fm-miss-2.html"]
    assert fm["caught_strict"] == ["fm-miss-1.html"]
    assert fm["caught_loose"] == ["fm-miss-1.html", "fm-miss-2.html"]
    assert fm["clean"] == ["fm-clean.html"]
    assert fm["false_flags"] == []
    assert fm["verdict"] == "FAIL"


def test_score_reports_the_answers_on_each_pages_carriers(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    runs = gate_runs(tmp_path / "runs")

    pages = {p["page"]: p for p in score(labels, runs, run="gate")["pages"]}

    mono_a = pages["mono-a.html"]
    assert mono_a["form"] == "ii"
    assert mono_a["w1_flagged"] is False
    assert mono_a["carrier_answers"] == [
        {
            "steps": {"from": 2, "to": 3},
            "ids": ["a2", "a3"],
            "answer": "breaks",
            "reason": "breaks reason",
        },
    ]
    assert mono_a["strict"] is True
    assert mono_a["findings"] == 1
    assert pages["mono-c.html"]["w1_flagged"] is True
    fm2 = pages["fm-miss-2.html"]
    assert fm2["carrier_answers"] == [
        {
            "steps": {"trigger": 1, "enter": 2, "escape": 3},
            "ids": ["open2"],
            "opened": "yes",
            "inside": {"answer": "pass", "reason": "inside reason"},
            "returned": {"answer": "fail", "reason": "returned reason"},
        }
    ]
    assert fm2["strict"] is False
    assert fm2["findings"] == 1
    assert pages["fm-clean.html"]["findings"] == 0
    assert pages["clean-2.html"]["findings"] == 0


def test_gates_pass_when_the_strict_counts_meet_the_criteria(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    root = tmp_path / "runs"
    runs = [
        write_run(root, "mono-a", ORDER_WALK, [tab(1, 2, "breaks")]),
        write_run(
            root, "mono-b", [("Tab", "b1"), ("Tab", "b2")], [tab(0, 1, "breaks")]
        ),
        write_run(
            root, "mono-c", [("Tab", "c1"), ("Tab", "c2")], [tab(0, 1, "follows")]
        ),
        write_run(
            root, "clean-1", [("Tab", "k1"), ("Tab", "z")], [tab(0, 1, "breaks")]
        ),
        write_run(
            root, "clean-2", [("Tab", "k2"), ("Tab", "z")], [tab(0, 1, "follows")]
        ),
        write_run(root, "fm-miss-1", FM_WALK, [enter(1, "fail")]),
        write_run(
            root,
            "fm-miss-2",
            [("Tab", "open2"), ("Enter", "dlg2"), ("Escape", "open2")],
            [enter(0, "fail")],
        ),
        write_run(
            root,
            "fm-caught",
            [("Tab", "open3"), ("Enter", "open3"), ("Escape", "open3")],
            [enter(0, "pass")],
            w1_rules=["focus-management"],
        ),
        write_run(
            root,
            "fm-clean",
            [("Tab", "t1"), ("Enter", "t1"), ("Escape", "t1")],
            [enter(0, "fail")],
        ),
    ]

    gates = score(labels, runs, run="gate")["gates"]

    assert gates["G3a"]["caught_strict"] == ["mono-a.html", "mono-b.html"]
    assert gates["G3a"]["false_flags"] == ["clean-1.html"]
    assert gates["G3a"]["verdict"] == "PASS"
    assert gates["focus-management"]["false_flags"] == ["fm-clean.html"]
    assert gates["focus-management"]["verdict"] == "PASS"


def test_a_gate_missing_one_of_its_pages_is_incomplete(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    runs = [r for r in gate_runs(tmp_path / "runs") if r.name != "clean-2"]

    g3a = score(labels, runs, run="gate")["gates"]["G3a"]

    assert g3a["complete"] is False
    assert g3a["missing"] == ["clean-2.html"]
    assert g3a["verdict"] == "INCOMPLETE"


def test_score_refuses_labels_that_do_not_match_their_hash(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    runs = gate_runs(tmp_path / "runs")
    labels.write_text(labels.read_text().replace("Barrier", "Changed"))

    with pytest.raises(ScoreError, match="labels.json does not match its hash"):
        score(labels, runs, run="gate")


def test_score_refuses_a_review_of_other_verdicts(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    runs = gate_runs(tmp_path / "runs")
    review = runs[0] / "review-gate.json"
    data = json.loads(review.read_text())
    data["verdicts_sha256"] = "0" * 64
    review.write_text(json.dumps(data))

    with pytest.raises(ScoreError, match="mono-a.*other verdicts"):
        score(labels, runs, run="gate")


def test_score_refuses_verdicts_that_do_not_match_their_hash(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    runs = gate_runs(tmp_path / "runs")
    (runs[0] / "verdicts.json").write_text('{"verdicts": []}')

    with pytest.raises(
        ScoreError, match="mono-a/verdicts.json does not match its hash"
    ):
        score(labels, runs, run="gate")


def test_score_refuses_a_page_without_a_label_or_scored_twice(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder", LABELS[:3])
    runs = gate_runs(tmp_path / "runs")

    with pytest.raises(ScoreError, match="no label for clean-1.html"):
        score(labels, runs, run="gate")
    with pytest.raises(ScoreError, match="mono-a.html is scored twice"):
        score(labels, [runs[0], runs[0]], run="gate")


def test_score_refuses_a_page_outside_the_two_gate_forms(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    run = write_run(tmp_path / "runs", "trap", [("Tab", "w")], [])

    with pytest.raises(ScoreError, match="trap.html .* form trap"):
        score(labels, [run], run="gate")


def test_score_refuses_a_missing_review(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    runs = gate_runs(tmp_path / "runs")

    with pytest.raises(ScoreError, match="no review-other.json"):
        score(labels, runs, run="other")


def test_score_command_writes_the_report_and_prints_the_verdicts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = write_labels(tmp_path / "builder")
    runs = gate_runs(tmp_path / "runs")
    out = tmp_path / "score.json"

    code = main(
        [
            "score",
            *map(str, runs),
            "--run",
            "gate",
            "--labels",
            str(labels),
            "--out",
            str(out),
        ]
    )

    assert code == 0
    printed = capsys.readouterr().out
    digest = hashlib.sha256(labels.read_bytes()).hexdigest()
    assert f"labels sha256 {digest}" in printed
    assert f"prompt sha256 {PROMPT}" in printed
    assert "G3a: strict 1 of 2, loose 2 of 2; false flags 1 of 2: FAIL" in printed
    assert (
        "focus-management: strict 1 of 2, loose 2 of 2; false flags 0 of 1: FAIL"
        in printed
    )
    assert json.loads(out.read_text()) == score(labels, runs, run="gate")


def test_score_command_refuses_an_existing_report(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "builder")
    runs = gate_runs(tmp_path / "runs")
    out = tmp_path / "score.json"
    out.write_text("{}")

    with pytest.raises(SystemExit):
        main(["score", *map(str, runs), "--labels", str(labels), "--out", str(out)])

    assert out.read_text() == "{}"
