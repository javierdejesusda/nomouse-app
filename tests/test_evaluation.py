import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from nomouse.evaluation import EvalError, evaluate, names_match

RUNS = ("r1", "r2")


def hashed(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n")
    return path


def builder_label(page: str, form: str, status: str, carriers: list[str]) -> dict:
    return {
        "page": f"{page}.html",
        "form": form,
        "status": status,
        "kind": "k",
        "w1_design": None,
        "barrier": "B.",
        "transition": "T",
        "carrier_ids": carriers,
    }


BUILDER = [
    builder_label("fm-miss", "focus-management", "failure", ["open1", "dlg1"]),
    builder_label("fm-caught", "focus-management", "failure", ["open2"]),
    builder_label("fm-clean", "focus-management", "clean", ["t1"]),
    builder_label("order-geo", "order", "failure", ["g1", "g2"]),
    builder_label("trap", "trap", "failure", ["w1"]),
]


def w3c_row(row_id: str, page: str, status: str, types: list[str], **carriers: Any):
    return {
        "id": row_id,
        "page": page,
        "status": status,
        "source": "W3C Note",
        "barrier": "B.",
        "types": types,
        "carrier_ids": carriers.get("ids", []),
        "carrier_tags": carriers.get("tags", []),
        "carrier_names": carriers.get("names", []),
        "walk_exercised": True,
    }


SURVEY = {"ids": ["em", "n"], "names": ["Name", "eMail Address"]}
W3C = [
    w3c_row("b-06", "before/survey.html", "failure", ["focus-order"], **SURVEY),
    w3c_row(
        "b-08",
        "before/survey.html",
        "failure",
        ["not-focusable", "focus-not-visible"],
        tags=["A"],
        names=["Home", "News"],
    ),
    w3c_row("b-05", "before/survey.html", "failure", ["no-bypass"]),
    w3c_row("a-06", "after/survey.html", "clean", ["focus-order"], **SURVEY),
]


def labels(root: Path) -> tuple[Path, Path]:
    builder = hashed(root / "builder" / "labels.json", {"about": "", "pages": BUILDER})
    w3c = hashed(root / "eval" / "w3c-labels.json", {"about": "", "rows": W3C})
    return builder, w3c


def el(dom_id: str | None, name: str = "", tag: str = "BUTTON") -> dict[str, Any]:
    return {"tag": tag, "dom_id": dom_id, "name": name, "role": "button"}


def finding(rule: str, element: dict[str, Any], detail: str = "d") -> dict:
    return {"rule": rule, "element": element, "step": 1, "detail": detail}


def usage(tokens: int) -> dict[str, Any]:
    return {
        "input_tokens": tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": tokens // 10,
        "total_tokens": tokens + tokens // 10,
    }


def enter(trigger: int, inside: str, returned: str = "pass") -> dict[str, Any]:
    return {
        "form": "i",
        "steps": {"trigger": trigger, "enter": trigger + 1, "escape": trigger + 2},
        "answer": {
            "opened": "yes",
            "inside": {"answer": inside, "reason": "r"},
            "returned": {"answer": returned, "reason": "r"},
        },
        "finding": "fail" in (inside, returned),
    }


def bare_item(element: str, kind: str) -> dict[str, Any]:
    return {"element": element, "type": kind, "second": 1, "description": "d"}


def write_run(
    root: Path,
    page: str,
    walk: Sequence[dict[str, Any]],
    *,
    w0: Sequence[dict[str, Any]] = (),
    w1: Sequence[dict[str, Any]] = (),
    reviews: dict[str, list[dict[str, Any]]] | None = None,
    bares: dict[str, list[dict[str, Any]]] | None = None,
) -> Path:
    """Writes a walk (focus per step), frozen verdicts and per-run answers."""
    run_dir = root / "runs" / page.replace("/", "-")
    run_dir.mkdir(parents=True)
    record = {
        "url": f"http://127.0.0.1/{page}",
        "initial": {"focus": el(None, tag="BODY")},
        "steps": [{"index": i, "focus": f} for i, f in enumerate(walk)],
    }
    (run_dir / "walk.json").write_text(json.dumps(record), encoding="utf-8")
    verdicts = hashed(
        run_dir / "verdicts.json",
        {
            "page": f"/{page}",
            "verdicts": [
                {"walker": "W0", "findings": list(w0)},
                {"walker": "W1", "findings": list(w0) + list(w1)},
            ],
        },
    )
    digest = hashlib.sha256(verdicts.read_bytes()).hexdigest()
    for run in RUNS:
        instances = (reviews or {}).get(run, [])
        review = {
            "verdicts_sha256": digest,
            "prompt_sha256": "p" * 64,
            "instances": instances,
            "usage": {"calls": 1, "input_tokens": 1000, "output_tokens": 100},
        }
        (run_dir / f"review-{run}.json").write_text(json.dumps(review))
        bare = {
            "verdicts_sha256": digest,
            "prompt_sha256": "b" * 64,
            "failures": (bares or {}).get(run, []),
            "usage": usage(2000),
        }
        (run_dir / f"bare-{run}.json").write_text(json.dumps(bare))
    return run_dir


FM_WALK = [el("x", "Home"), el("open1", "Open"), el("open1", "Open"), el("open1")]


def all_runs(root: Path) -> list[Path]:
    """A run per labelled page; each test overrides the page it is about."""
    return [
        write_run(root, "fm-caught.html", [el("open2", "Menu")]),
        write_run(root, "fm-clean.html", [el("t1", "FAQ")]),
        write_run(root, "order-geo.html", [el("g1", "Email")]),
        write_run(root, "trap.html", [el("w1", "Card number")]),
        write_run(root, "before/survey.html", [el("em", "", tag="INPUT")]),
        write_run(root, "after/survey.html", [el("em", "", tag="INPUT")]),
    ]


def run_eval(root: Path, runs: list[Path]) -> dict[str, Any]:
    builder, w3c = labels(root)
    return evaluate(builder, w3c, runs, runs_labels=RUNS)


def row(report: dict[str, Any], row_id: str) -> dict[str, Any]:
    return next(r for r in report["rows"] if r["id"] == row_id)


def test_walker_arms_match_rows_by_type_and_carrier(tmp_path: Path) -> None:
    runs = all_runs(tmp_path)
    runs[2:3] = [
        write_run(
            tmp_path / "x",
            "order-geo.html",
            [el("g1")],
            w1=[finding("order", el("other"))],
        )
    ]
    runs[3:4] = [
        write_run(
            tmp_path / "x",
            "trap.html",
            [el("w1")],
            w0=[finding("trap", el("w1"))],
        )
    ]
    runs[4:5] = [
        write_run(
            tmp_path / "x",
            "before/survey.html",
            [el("em", tag="INPUT")],
            w0=[finding("unreachable", el(None, tag="A"))],
            w1=[finding("order", el("n", tag="INPUT"))],
        )
    ]
    runs.append(write_run(tmp_path, "fm-miss.html", FM_WALK))

    report = run_eval(tmp_path, runs)

    assert row(report, "order-geo")["arms"]["W1"] == {
        "strict": False,
        "loose": True,
        "status": None,
    }
    assert row(report, "trap")["arms"]["W0"]["status"] == "VERIFIED"
    assert row(report, "b-06")["arms"]["W0"]["loose"] is False
    assert row(report, "b-06")["arms"]["W1"]["strict"] is True
    assert row(report, "b-08")["arms"]["W0"]["strict"] is True
    assert row(report, "b-05")["arms"]["W1"]["loose"] is False


def test_nomouse_marks_walker_catches_verified_and_model_only_needs_review(
    tmp_path: Path,
) -> None:
    runs = all_runs(tmp_path)
    runs[0] = write_run(
        tmp_path / "x",
        "fm-caught.html",
        [el("open2"), el("open2")],
        w1=[finding("focus-management", el("open2"), "after Enter, focus is out")],
        reviews={"r1": [enter(0, "fail")]},
    )
    runs.append(
        write_run(
            tmp_path,
            "fm-miss.html",
            FM_WALK,
            reviews={"r1": [enter(1, "fail")], "r2": [enter(0, "fail")]},
        )
    )

    report = run_eval(tmp_path, runs)

    caught = row(report, "fm-caught")["arms"]
    assert caught["NoMouse r1"]["status"] == "VERIFIED"
    assert caught["NoMouse r2"]["status"] == "VERIFIED"
    missed = row(report, "fm-miss")["arms"]
    assert missed["W1"]["loose"] is False
    assert missed["NoMouse r1"] == {
        "strict": True,
        "loose": True,
        "status": "NEEDS REVIEW",
    }
    assert missed["NoMouse r2"] == {"strict": False, "loose": True, "status": None}
    assert report["model_only"] == {"r1": ["fm-miss"], "r2": []}
    assert "fm-miss" in report["disagreements"]["NoMouse"]


def test_a_returned_fail_flags_a_clean_control_but_is_no_strict_catch(
    tmp_path: Path,
) -> None:
    runs = all_runs(tmp_path)
    runs[1] = write_run(
        tmp_path / "x",
        "fm-clean.html",
        [el("t1"), el("t1")],
        reviews={"r1": [enter(0, "pass", "fail")]},
    )
    runs.append(
        write_run(
            tmp_path,
            "fm-miss.html",
            FM_WALK,
            reviews={"r1": [enter(1, "pass", "fail")]},
        )
    )

    report = run_eval(tmp_path, runs)

    assert row(report, "fm-clean")["arms"]["NoMouse r1"]["strict"] is True
    assert row(report, "fm-miss")["arms"]["NoMouse r1"]["strict"] is False
    assert report["arms"]["NoMouse r1"]["false_flags"] == 1
    assert report["bar"]["runs"]["r1"] == {
        "model_only_catches": 0,
        "control_flags": 1,
        "met": False,
    }


def test_bare_answers_match_by_type_and_visible_name(tmp_path: Path) -> None:
    runs = all_runs(tmp_path)
    runs[4] = write_run(
        tmp_path / "x",
        "before/survey.html",
        [el("em", tag="INPUT")],
        bares={
            "r1": [
                bare_item("The eMail address text field", "focus-order"),
                bare_item("Home Page", "not-focusable"),
                bare_item("", "no-bypass"),
            ],
            "r2": [
                bare_item("Password", "focus-order"),
                bare_item("Search", "not-focusable"),
            ],
        },
    )
    runs[2] = write_run(
        tmp_path / "x",
        "order-geo.html",
        [el("g1", "Email"), el("g2", "Password")],
        bares={"r1": [bare_item("password field", "focus-order")]},
    )
    runs.append(write_run(tmp_path, "fm-miss.html", FM_WALK))

    report = run_eval(tmp_path, runs)

    survey = {r: row(report, r)["arms"] for r in ("b-06", "b-08", "b-05")}
    assert survey["b-06"]["bare r1"]["status"] == "NEEDS REVIEW"
    assert survey["b-08"]["bare r1"]["strict"] is True
    assert survey["b-05"]["bare r1"]["strict"] is True
    assert survey["b-06"]["bare r2"] == {"strict": False, "loose": True, "status": None}
    assert survey["b-08"]["bare r2"]["strict"] is False
    assert row(report, "order-geo")["arms"]["bare r1"]["strict"] is True
    assert set(report["disagreements"]["bare"]) == {"b-06", "b-08", "b-05", "order-geo"}


def test_arm_counts_split_catches_and_false_flags(tmp_path: Path) -> None:
    runs = all_runs(tmp_path)
    runs[5] = write_run(
        tmp_path / "x",
        "after/survey.html",
        [el("em", tag="INPUT")],
        w1=[finding("order", el("x", tag="A")), finding("cover", el("n"))],
        bares={"r1": [bare_item("Name", "focus-order")]},
    )
    runs[1] = write_run(
        tmp_path / "x",
        "fm-clean.html",
        [el("t1")],
        w1=[finding("focus-not-visible", el("t1"))],
    )
    runs.append(
        write_run(
            tmp_path,
            "fm-miss.html",
            FM_WALK,
            reviews={"r1": [enter(1, "fail")]},
        )
    )

    report = run_eval(tmp_path, runs)

    w1 = report["arms"]["W1"]
    assert (w1["failures"], w1["clean"]) == (7, 2)
    assert (w1["false_flags"], w1["false_flags_strict"]) == (1, 0)
    assert (w1["clean_pages"], w1["clean_pages_any_finding"]) == (2, 2)
    nomouse = report["arms"]["NoMouse r1"]
    assert (nomouse["found"], nomouse["verified"], nomouse["needs_review"]) == (
        1,
        0,
        1,
    )
    bare = report["arms"]["bare r1"]
    assert (bare["false_flags"], bare["false_flags_strict"]) == (1, 1)
    assert report["arms"]["bare r2"]["clean_pages_any_finding"] == 0


def test_bar_needs_three_model_only_catches_and_at_most_one_control_flag(
    tmp_path: Path,
) -> None:
    report = run_eval(
        tmp_path,
        [*all_runs(tmp_path), write_run(tmp_path, "fm-miss.html", FM_WALK)],
    )

    assert report["bar"]["runs"]["r1"]["met"] is False
    assert report["bar"]["met"] is False


def test_usage_and_cost_are_summed_per_model_arm(tmp_path: Path) -> None:
    runs = [*all_runs(tmp_path), write_run(tmp_path, "fm-miss.html", FM_WALK)]

    report = run_eval(tmp_path, runs)

    assert report["usage"]["reviewer r1"] == {
        "calls": 7,
        "input_tokens": 7000,
        "output_tokens": 700,
    }
    assert report["usage"]["bare r2"] == {
        "calls": 7,
        "input_tokens": 14000,
        "output_tokens": 1400,
    }
    total = report["usage"]["total"]
    assert total["input_tokens"] == 2 * (7000 + 14000)
    assert total["cost_usd"] == pytest.approx(
        42000 * 1.25e-6 + 4200 * 4.25e-6, abs=1e-9
    )
    assert report["prompt_sha256"] == {"reviewer": ["p" * 64], "bare": ["b" * 64]}
    assert report["complete"] is True


def test_report_names_labels_hashes_and_missing_pages(tmp_path: Path) -> None:
    report = run_eval(tmp_path, all_runs(tmp_path))

    builder, w3c = tmp_path / "builder/labels.json", tmp_path / "eval/w3c-labels.json"
    assert report["labels_sha256"] == {
        "builder": hashlib.sha256(builder.read_bytes()).hexdigest(),
        "w3c": hashlib.sha256(w3c.read_bytes()).hexdigest(),
    }
    assert report["missing"] == ["fm-miss.html"]
    assert report["complete"] is False


def test_builder_rows_take_carrier_names_from_the_walk(tmp_path: Path) -> None:
    runs = [*all_runs(tmp_path), write_run(tmp_path, "fm-miss.html", FM_WALK)]

    report = run_eval(tmp_path, runs)

    assert row(report, "fm-miss")["carrier_names"] == ["Open"]
    assert row(report, "fm-miss")["types"] == ["focus-not-moved"]
    assert row(report, "fm-clean")["types"] == [
        "focus-not-moved",
        "focus-not-returned",
    ]


@pytest.mark.parametrize("damage", ["labels", "review", "bare", "label", "twice"])
def test_evaluate_refuses_inputs_it_cannot_trust(tmp_path: Path, damage: str) -> None:
    runs = all_runs(tmp_path)
    builder, w3c = labels(tmp_path)
    if damage == "labels":
        w3c.write_text("{}", encoding="utf-8")
    elif damage == "review":
        review = runs[0] / "review-r2.json"
        data = json.loads(review.read_text())
        review.write_text(json.dumps({**data, "verdicts_sha256": "0" * 64}))
    elif damage == "bare":
        (runs[0] / "bare-r1.json").unlink()
    elif damage == "label":
        runs.append(write_run(tmp_path, "unknown.html", []))
    else:
        runs.append(runs[0])

    with pytest.raises(EvalError):
        evaluate(builder, w3c, runs, runs_labels=RUNS)


@pytest.mark.parametrize(
    ("said", "carrier", "expected"),
    [
        ("Email", "eMail Address", True),
        ("the Sign-in button", "Sign in", True),
        ("Retype eMail field", "eMail Address", False),
        ("Home Page link", "Home", True),
        ("button", "Button", False),
        ("", "Name", False),
        ("Names list", "Name", False),
    ],
)
def test_names_match_whole_words_either_way(
    said: str, carrier: str, expected: bool
) -> None:
    assert names_match(said, carrier) is expected


def test_arm_counts_are_also_split_by_label_set(tmp_path: Path) -> None:
    runs = [*all_runs(tmp_path), write_run(tmp_path, "fm-miss.html", FM_WALK)]

    report = run_eval(tmp_path, runs)

    w1 = report["arms"]["W1"]
    assert (w1["builder"]["failures"], w1["w3c"]["failures"]) == (4, 3)
    assert (w1["builder"]["clean"], w1["w3c"]["clean"]) == (1, 1)
