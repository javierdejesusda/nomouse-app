import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from nomouse import spark
from nomouse.cli import main
from nomouse.reviewer import (
    FORM_I_PROMPT,
    FORM_II_PROMPT,
    ReviewError,
    review,
)

VERDICTS = b'{"verdicts":[{"walker":"W1","findings":[{"rule":"order"}]}]}\n'


def box(y: float, x: float = 10.0) -> dict[str, float]:
    return {"x": x, "y": y, "width": 80.0, "height": 20.0}


def focus(
    node: int | None, role: str | None = None, name: str | None = None, y: float = 0
) -> dict[str, Any]:
    return {
        "backend_node_id": node,
        "tag": "BODY" if node is None else "A",
        "dom_id": None if node is None else f"secret-id-{node}",
        "role": role,
        "name": name,
        "box": None if node is None else box(y),
        "cover": None,
        "document_has_focus": True,
    }


def observation(
    shot: str, the_focus: dict[str, Any], scroll_y: float = 0
) -> dict[str, Any]:
    return {
        "url": "/p.html",
        "scroll_x": 0,
        "scroll_y": scroll_y,
        "focus": the_focus,
        "screenshot": shot,
        "settled": True,
        "appeared": [],
    }


def write_run(
    run_dir: Path,
    steps: list[tuple[str, dict[str, Any], float]],
    *,
    freeze: bool = True,
) -> Path:
    """Writes a walk record, one screenshot per observation and frozen verdicts.

    Each step is (key, focus, scroll_y).
    """
    run_dir.mkdir(parents=True)
    initial = observation("initial.png", focus(None))
    record_steps = [
        {**observation(f"step-{i:03d}.png", f, scroll), "index": i, "key": key}
        for i, (key, f, scroll) in enumerate(steps)
    ]
    record = {
        "url": "http://127.0.0.1/p.html",
        "end_reason": "max_keys",
        "repeat_step": None,
        "keys": [key for key, _, _ in steps],
        "initial": initial,
        "steps": record_steps,
    }
    (run_dir / "walk.json").write_text(json.dumps(record), encoding="utf-8")
    for item in [initial, *record_steps]:
        (run_dir / item["screenshot"]).write_bytes(item["screenshot"].encode())
    if freeze:
        (run_dir / "verdicts.json").write_bytes(VERDICTS)
        digest = hashlib.sha256(VERDICTS).hexdigest()
        (run_dir / "verdicts.json.sha256").write_text(f"{digest}  verdicts.json\n")
    return run_dir


MENU = focus(2, "button", "Menu", 50)

STEPS = [
    ("Tab", focus(1, "link", "Home", 10), 0.0),
    ("Tab", MENU, 0.0),
    ("Enter", MENU, 0.0),
    ("Escape", MENU, 0.0),
    ("Tab", focus(3, "link", "About", 100), 0.0),
    ("Tab", focus(4, "link", "Contact", 100), 500.0),
]


def default_answers(form: str, asked: list[int]) -> dict[str, Any]:
    if form == "i":
        return {
            "instances": [
                {
                    "step": step,
                    "opened": "yes",
                    "inside": {"answer": "fail", "reason": "Focus stayed outside."},
                    "returned": {"answer": "pass", "reason": "Back on Menu."},
                }
                for step in asked
            ]
        }
    return {
        "transitions": [
            {
                "step": step,
                "answer": "breaks" if n == 0 else "follows",
                "reason": "Reason.",
            }
            for n, step in enumerate(asked)
        ]
    }


class ReviewTransport:
    """Answers like the API with structured output, 1,037 tokens per image."""

    def __init__(
        self,
        answer: Callable[[str, list[int]], object] = default_answers,
    ) -> None:
        self.answer = answer
        self.bodies: list[dict[str, Any]] = []

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        request = json.loads(body)
        self.bodies.append(request)
        form, payload = parse(request)
        key = "instances" if form == "i" else "transitions"
        asked = [item["step"] for item in payload[key]]
        answer = self.answer(form, asked)
        images = len(request["input"][0]["content"]) - 1
        usage = {
            "input_tokens": 1037 * images + 300,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 100,
            "output_tokens_details": {"reasoning_tokens": 20},
            "total_tokens": 1037 * images + 400,
        }
        text = answer if isinstance(answer, str) else json.dumps(answer)
        reply = {
            "model": "muse-spark-1.3",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]}
            ],
            "usage": usage,
        }
        return 200, json.dumps(reply).encode()


def parse(request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    text = request["input"][0]["content"][0]["text"]
    for form, prompt in (("i", FORM_I_PROMPT), ("ii", FORM_II_PROMPT)):
        if text.startswith(prompt):
            return form, json.loads(text.removeprefix(prompt))
    raise AssertionError("unknown prompt")


def images(request: dict[str, Any]) -> list[str]:
    return [
        base64.b64decode(part["image_url"].removeprefix("data:image/png;base64,"))
        .decode()
        .removesuffix(".png")
        for part in request["input"][0]["content"][1:]
    ]


def test_review_refuses_a_run_without_frozen_verdicts(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", STEPS, freeze=False)
    transport = ReviewTransport()

    with pytest.raises(ReviewError, match="verdicts"):
        review(run_dir, transport=transport)

    assert transport.bodies == []
    assert not (run_dir / "review.json").exists()


def test_review_refuses_verdicts_that_do_not_match_their_hash(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)
    (run_dir / "verdicts.json").write_bytes(VERDICTS.replace(b"order", b"cover"))
    transport = ReviewTransport()

    with pytest.raises(ReviewError, match="hash"):
        review(run_dir, transport=transport)

    assert transport.bodies == []
    assert not (run_dir / "review.json").exists()


def test_review_asks_form_i_about_each_enter_with_its_escape(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)
    transport = ReviewTransport()

    review(run_dir, transport=transport)

    form_i = [b for b in transport.bodies if parse(b)[0] == "i"]
    assert len(form_i) == 1
    request = form_i[0]
    assert request["model"] == "muse-spark-1.3"
    assert request["reasoning"] == {"effort": "minimal"}
    assert request["text"]["format"]["type"] == "json_schema"
    assert images(request) == ["step-001", "step-002", "step-003"]
    menu = {"role": "button", "name": "Menu", "box": box(50)}
    assert parse(request)[1] == {
        "instances": [
            {
                "step": 2,
                "screenshots": [
                    {"image": 1, "moment": "before Enter", "focus": menu},
                    {"image": 2, "moment": "after Enter", "focus": menu},
                    {"image": 3, "moment": "after Escape", "focus": menu},
                ],
            }
        ]
    }


def test_review_asks_form_ii_with_one_layout_screenshot_per_view(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)
    transport = ReviewTransport()

    review(run_dir, transport=transport)

    form_ii = [b for b in transport.bodies if parse(b)[0] == "ii"]
    assert len(form_ii) == 1
    request = form_ii[0]
    # A view is a scroll position between two non-Tab keys; its first
    # screenshot stands for it.
    assert images(request) == ["initial", "step-003", "step-005"]

    def element(image: int, role: str, name: str, y: float) -> dict[str, Any]:
        return {"image": image, "role": role, "name": name, "box": box(y)}

    assert parse(request)[1] == {
        "transitions": [
            {
                "step": 1,
                "from": element(1, "link", "Home", 10),
                "to": element(1, "button", "Menu", 50),
            },
            {
                "step": 4,
                "from": element(2, "button", "Menu", 50),
                "to": element(2, "link", "About", 100),
            },
            {
                "step": 5,
                "from": element(2, "link", "About", 100),
                "to": element(3, "link", "Contact", 100),
            },
        ]
    }


def test_review_never_shows_the_model_verdicts_or_dom_ids(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)
    transport = ReviewTransport()

    review(run_dir, transport=transport)

    for request in transport.bodies:
        text = request["input"][0]["content"][0]["text"]
        assert "secret-id" not in text
        assert "W1" not in text
        assert "verdict" not in text.lower()


def test_review_writes_answers_statuses_hash_and_usage(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)

    result = review(run_dir, transport=ReviewTransport())

    written = json.loads((run_dir / "review.json").read_text(encoding="utf-8"))
    assert written == result
    assert written["page"] == "http://127.0.0.1/p.html"
    assert written["run"] is None
    assert written["verdicts_sha256"] == hashlib.sha256(VERDICTS).hexdigest()
    assert written["model"] == "muse-spark-1.3"
    assert written["reasoning_effort"] == "minimal"
    assert written["instances"] == [
        {
            "form": "i",
            "steps": {"trigger": 1, "enter": 2, "escape": 3},
            "answer": {
                "opened": "yes",
                "inside": {"answer": "fail", "reason": "Focus stayed outside."},
                "returned": {"answer": "pass", "reason": "Back on Menu."},
            },
            "finding": True,
            "status": "NEEDS REVIEW",
        },
        {
            "form": "ii",
            "steps": {"from": 0, "to": 1},
            "answer": {"answer": "breaks", "reason": "Reason."},
            "finding": True,
            "status": "best practice, NEEDS REVIEW",
        },
        {
            "form": "ii",
            "steps": {"from": 3, "to": 4},
            "answer": {"answer": "follows", "reason": "Reason."},
            "finding": False,
            "status": None,
        },
        {
            "form": "ii",
            "steps": {"from": 4, "to": 5},
            "answer": {"answer": "follows", "reason": "Reason."},
            "finding": False,
            "status": None,
        },
    ]
    assert written["usage"] == {
        "calls": 2,
        "input_tokens": 1037 * 6 + 600,
        "cached_tokens": 0,
        "output_tokens": 200,
        "reasoning_tokens": 40,
        "total_tokens": 1037 * 6 + 800,
    }
    assert [(c["form"], c["images"], c["instances"]) for c in written["calls"]] == [
        ("i", 3, 1),
        ("ii", 3, 3),
    ]


def test_review_never_overwrites_a_review(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)
    (run_dir / "review.json").write_text("{}", encoding="utf-8")
    transport = ReviewTransport()

    with pytest.raises(FileExistsError):
        review(run_dir, transport=transport)

    assert transport.bodies == []
    assert (run_dir / "review.json").read_text(encoding="utf-8") == "{}"


def test_review_with_a_run_label_writes_a_separate_file(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)
    (run_dir / "review.json").write_text("{}", encoding="utf-8")

    result = review(run_dir, run="run1", transport=ReviewTransport())

    assert result["run"] == "run1"
    assert json.loads((run_dir / "review-run1.json").read_text()) == result
    assert (run_dir / "review.json").read_text(encoding="utf-8") == "{}"
    with pytest.raises(FileExistsError):
        review(run_dir, run="run1", transport=ReviewTransport())


@pytest.mark.parametrize("label", ["", "../x", "a b", "run/1"])
def test_review_refuses_a_run_label_that_is_not_a_plain_name(
    tmp_path: Path, label: str
) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)

    with pytest.raises(ValueError, match="run label"):
        review(run_dir, run=label, transport=ReviewTransport())


def drop_first(form: str, asked: list[int]) -> object:
    answers = default_answers(form, asked)
    key = "instances" if form == "i" else "transitions"
    answers[key] = answers[key][1:]
    return answers


def bad_enum(form: str, asked: list[int]) -> object:
    answers = default_answers(form, asked)
    if form == "i":
        answers["instances"][0]["opened"] = "maybe"
    return answers


def empty_reason(form: str, asked: list[int]) -> object:
    answers = default_answers(form, asked)
    if form == "i":
        answers["instances"][0]["returned"]["reason"] = " "
    return answers


@pytest.mark.parametrize(
    "answer",
    [
        drop_first,
        bad_enum,
        empty_reason,
        lambda form, asked: "not json",
        lambda form, asked: [],
    ],
)
def test_review_rejects_answers_that_do_not_fit_the_question(
    tmp_path: Path, answer: Callable[[str, list[int]], object]
) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)

    with pytest.raises(ReviewError, match="answer"):
        review(run_dir, transport=ReviewTransport(answer))

    assert not (run_dir / "review.json").exists()


def test_review_splits_requests_to_stay_within_the_image_limit(
    tmp_path: Path,
) -> None:
    steps: list[tuple[str, dict[str, Any], float]] = []
    for n in range(20):
        button = focus(n + 1, "button", f"B{n}", 10)
        steps += [("Tab", button, 0.0), ("Enter", button, 0.0)]
        steps.append(("Escape", button, 0.0))
    steps += [
        ("Tab", focus(100 + n, "link", f"L{n}", 10), 100.0 * n) for n in range(60)
    ]
    run_dir = write_run(tmp_path / "run", steps)
    transport = ReviewTransport()

    result = review(run_dir, transport=transport)

    for request in transport.bodies:
        assert len(images(request)) <= spark.MAX_IMAGES
    assert sum(i["form"] == "i" for i in result["instances"]) == 20
    assert sum(i["form"] == "ii" for i in result["instances"]) == 79
    assert sum(c["form"] == "i" for c in result["calls"]) == 2
    asked = [
        item["step"]
        for body in transport.bodies
        if parse(body)[0] == "ii"
        for item in parse(body)[1]["transitions"]
    ]
    assert asked == sorted(set(asked))
    assert len(asked) == 79


def test_review_of_a_walk_without_buttons_or_moves_makes_no_call(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "run", [("Tab", focus(1, "link", "A", 10), 0.0)])
    transport = ReviewTransport()

    result = review(run_dir, transport=transport)

    assert transport.bodies == []
    assert result["instances"] == []
    assert result["usage"]["calls"] == 0


def test_cli_review_writes_a_labelled_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)
    monkeypatch.setattr(spark, "urllib_transport", ReviewTransport())

    assert main(["review", str(run_dir), "--run", "run2"]) == 0

    out = capsys.readouterr().out
    assert (run_dir / "review-run2.json").is_file()
    assert hashlib.sha256(VERDICTS).hexdigest() in out
    assert "2 NEEDS REVIEW findings" in out
    assert str(run_dir / "review-run2.json") in out


def test_cli_review_refuses_a_run_without_frozen_verdicts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = write_run(tmp_path / "run", STEPS, freeze=False)

    with pytest.raises(SystemExit) as exit_info:
        main(["review", str(run_dir)])

    assert exit_info.value.code == 2
    assert "verdicts" in capsys.readouterr().err


def test_cli_review_refuses_to_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)
    (run_dir / "review.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main(["review", str(run_dir)])

    assert exit_info.value.code == 2
    assert "--run" in capsys.readouterr().err


def test_cli_review_reports_an_unfit_answer_as_a_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = write_run(tmp_path / "run", STEPS)
    monkeypatch.setattr(spark, "urllib_transport", ReviewTransport(drop_first))

    assert main(["review", str(run_dir)]) == 1

    assert "answer" in capsys.readouterr().err
    assert not (run_dir / "review.json").exists()
