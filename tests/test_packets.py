import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from nomouse.packets import (
    DISCLAIMER,
    PacketError,
    write_packets,
)


def element(dom_id: str, role: str, name: str, tag: str = "BUTTON") -> dict[str, Any]:
    return {"dom_id": dom_id, "role": role, "name": name, "tag": tag}


INVITE = element("invite", "button", "Invite people")
MENU = element("menu", "button", "Menu")
EMAIL = element("email", "textbox", "Email", "INPUT")

W1_FINDINGS = [
    {
        "rule": "focus-management",
        "step": 3,
        "element": INVITE,
        "detail": "after Enter, focus is outside the 13 elements that appeared",
    },
    {
        "rule": "order",
        "step": 6,
        "element": EMAIL,
        "detail": "jumps up 92 px from INPUT#name",
    },
    {
        "rule": "unreachable",
        "step": None,
        "element": element("help", "link", "Help", "A"),
        "detail": "never focused in a full Tab cycle",
    },
]


def focus(el: dict[str, Any] | None) -> dict[str, Any]:
    if el is None:
        return {"tag": "BODY", "dom_id": None, "role": None, "name": None}
    return dict(el)


def write_run(
    run_dir: Path,
    *,
    findings: list[dict[str, Any]] | None = None,
    review: list[dict[str, Any]] | None = None,
    review_name: str = "review.json",
) -> Path:
    """Writes a walk record, frozen verdicts and optionally a review.

    Steps: 0 Tab to Menu, 1 Enter, 2 Escape, 3 Enter on Invite, 4 Escape.
    """
    run_dir.mkdir(parents=True)
    path = [MENU, MENU, MENU, INVITE, INVITE, None, EMAIL]
    keys = ["Tab", "Enter", "Escape", "Enter", "Escape", "Tab", "Tab"]
    record = {
        "url": "http://127.0.0.1/p.html",
        "initial": {"focus": focus(None)},
        "steps": [
            {"index": i, "key": key, "focus": focus(el)}
            for i, (key, el) in enumerate(zip(keys, path, strict=True))
        ],
    }
    (run_dir / "walk.json").write_text(json.dumps(record), encoding="utf-8")
    verdicts = {
        "page": "/p.html",
        "verdicts": [
            {"walker": "W0", "findings": [W1_FINDINGS[2]]},
            {"walker": "W1", "findings": W1_FINDINGS if findings is None else findings},
        ],
    }
    raw = json.dumps(verdicts, sort_keys=True).encode()
    (run_dir / "verdicts.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (run_dir / "verdicts.json.sha256").write_text(f"{digest}  verdicts.json\n")
    if review is not None:
        written = {
            "verdicts_sha256": digest,
            "prompt_sha256": "p" * 64,
            "instances": review,
        }
        (run_dir / review_name).write_text(json.dumps(written), encoding="utf-8")
    return run_dir


def judged(answer: str, reason: str) -> dict[str, str]:
    return {"answer": answer, "reason": reason}


def form_i(
    trigger: int, inside: str, returned: str, reason: str = "Seen."
) -> dict[str, Any]:
    finding = "fail" in (inside, returned)
    return {
        "form": "i",
        "steps": {"trigger": trigger, "enter": trigger + 1, "escape": trigger + 2},
        "answer": {
            "opened": "yes",
            "inside": judged(inside, reason),
            "returned": judged(returned, reason),
        },
        "finding": finding,
        "status": "NEEDS REVIEW" if finding else None,
    }


REVIEW = [
    # Menu: only the model saw focus left outside: NEEDS REVIEW.
    form_i(0, "fail", "pass", "Focus stayed on Menu while the menu opened."),
    # Invite: W1 fired on the same Enter, so the model only corroborates it.
    form_i(2, "fail", "pass", "Focus stayed behind the dialog."),
]


def test_packets_cover_walker_and_model_findings_with_statuses(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)

    result = write_packets(run_dir)

    packets = result["packets"]
    assert [(p["rule"], p["status"], p["source"]) for p in packets] == [
        ("focus-management", "VERIFIED", "W1"),
        ("order", "VERIFIED", "W1"),
        ("unreachable", "VERIFIED", "W1"),
        ("focus-management", "NEEDS REVIEW", "reviewer form (i)"),
    ]
    assert [p["id"] for p in packets] == ["P1", "P2", "P3", "P4"]
    assert [p["best_practice"] for p in packets] == [False, True, False, False]
    assert packets[0]["step"] == 3
    assert packets[0]["element"] == {"role": "button", "name": "Invite people"}
    assert packets[0]["observed"] == W1_FINDINGS[0]["detail"]
    assert packets[0]["reviewer"] == {
        "question": "inside",
        "answer": "fail",
        "reason": "Focus stayed behind the dialog.",
    }
    model = packets[3]
    assert model["question"] == "inside"
    assert model["step"] == 1
    assert model["element"] == {"role": "button", "name": "Menu"}
    assert model["observed"] == "Focus stayed on Menu while the menu opened."
    for packet in packets:
        assert packet["disclaimer"] == DISCLAIMER
        assert packet["sentence_source"] == "template"
    assert DISCLAIMER == (
        "Observed keyboard-only test evidence."
        " Not a conformance claim and not legal advice."
    )


def test_packets_record_their_inputs_and_template_sentences(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)

    result = write_packets(run_dir)

    written = json.loads((run_dir / "packets.json").read_text(encoding="utf-8"))
    assert written == result
    assert result["page"] == "http://127.0.0.1/p.html"
    digest = (run_dir / "verdicts.json.sha256").read_text().split()[0]
    assert result["verdicts_sha256"] == digest
    assert result["review"] == {"file": "review.json", "prompt_sha256": "p" * 64}
    assert result["disclaimer"] == DISCLAIMER
    assert result["writer"] == {"mode": "template", "error": None, "usage": None}
    assert result["packets"][0]["sentence"] == (
        'Focus management at step 3 on the button "Invite people":'
        " after Enter, focus is outside the 13 elements that appeared."
    )
    assert result["packets"][2]["sentence"] == (
        'Unreachable: the link "Help" was never focused in a full Tab cycle.'
    )
    assert result["packets"][3]["sentence"] == (
        'Focus management at step 1 on the button "Menu": a model saw focus'
        " left outside what opened after Enter; a person should confirm."
    )


def test_packets_without_a_review_hold_the_walker_findings(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run")

    result = write_packets(run_dir)

    assert result["review"] is None
    assert {p["status"] for p in result["packets"]} == {"VERIFIED"}
    assert "reviewer" not in result["packets"][0]


def test_a_named_review_is_used_and_must_exist(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW, review_name="review-g.json")

    with pytest.raises(PacketError, match="review-h.json"):
        write_packets(run_dir, run="h")
    result = write_packets(run_dir, run="g")

    assert result["review"]["file"] == "review-g.json"
    assert len(result["packets"]) == 4


def test_tab_order_answers_in_an_old_review_make_no_packet(tmp_path: Path) -> None:
    order = {
        "form": "ii",
        "steps": {"from": 5, "to": 6},
        "answer": {"answer": "breaks", "reason": "Up."},
        "finding": True,
        "status": "best practice, NEEDS REVIEW",
    }
    run_dir = write_run(tmp_path / "run", findings=[], review=[order])

    result = write_packets(run_dir)

    assert result["packets"] == []


def test_a_returned_fail_the_walker_missed_is_its_own_packet(tmp_path: Path) -> None:
    run_dir = write_run(
        tmp_path / "run", findings=[], review=[form_i(0, "pass", "fail")]
    )

    result = write_packets(run_dir)

    (packet,) = result["packets"]
    assert (packet["question"], packet["step"]) == ("returned", 2)
    assert packet["sentence"] == (
        'Focus management at step 2 on the button "Menu": a model saw focus'
        " not return to it after Escape; a person should confirm."
    )


def test_packets_refuse_unfrozen_verdicts_or_a_review_of_other_verdicts(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "a", review=REVIEW)
    (run_dir / "verdicts.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PacketError, match="hash"):
        write_packets(run_dir)

    other = write_run(tmp_path / "b", review=REVIEW)
    review = json.loads((other / "review.json").read_text())
    review["verdicts_sha256"] = "0" * 64
    (other / "review.json").write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(PacketError, match="other verdicts"):
        write_packets(other)

    assert not (run_dir / "packets.json").exists()
    assert not (other / "packets.json").exists()


def test_packets_never_overwrite(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)
    (run_dir / "packets.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_packets(run_dir)

    assert (run_dir / "packets.json").read_text(encoding="utf-8") == "{}"
