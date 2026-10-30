import hashlib
import json
import re
import urllib.error
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from nomouse import spark
from nomouse.cli import main
from nomouse.packets import (
    DISCLAIMER,
    PacketError,
    acceptable_sentence,
    write_packets,
)


def element(dom_id: str, role: str, name: str, tag: str = "BUTTON") -> dict[str, Any]:
    return {"dom_id": dom_id, "role": role, "name": name, "tag": tag}


INVITE = element("invite", "button", "Invite people")
MENU = element("menu", "button", "Menu")
EMAIL = element("email", "textbox", "Email", "INPUT")
NAME = element("name", "textbox", "Name", "INPUT")
HELP = {"dom_id": "help", "role": None, "name": None, "tag": "A"}

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
        "element": HELP,
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

    Steps: 0 Tab to Menu, 1 Enter, 2 Escape, 3 Enter on Invite, 4 Escape,
    5 Tab to Name, 6 Tab to Email.
    """
    run_dir.mkdir(parents=True)
    path = [MENU, MENU, MENU, INVITE, INVITE, NAME, EMAIL]
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


class SentenceTransport:
    """Answers like the API, writing sentences with `write(id, finding)`."""

    def __init__(self, write: Callable[[str, dict[str, Any]], str]) -> None:
        self.write = write
        self.bodies: list[dict[str, Any]] = []

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        request = json.loads(body)
        self.bodies.append(request)
        text = request["input"][0]["content"][0]["text"]
        findings = json.loads(text[text.index("{") :])["findings"]
        answer = {
            "sentences": [
                {"id": f["id"], "sentence": self.write(f["id"], f)} for f in findings
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
            "usage": {"input_tokens": 400, "output_tokens": 90, "total_tokens": 490},
        }
        return 200, json.dumps(reply).encode()


ORDER_CAVEAT = (
    ", a best practice finding: the Tab order differs from the visual layout,"
    " and W3C allows other meaningful orders, so a person should review it"
)
MODEL_CAVEAT = ", which a model saw and a person should confirm"


def plain(packet_id: str, finding: dict[str, Any]) -> str:
    """Writes a sentence with the caveat each kind of finding needs."""
    sentence = f"Observed {finding['rule']} on {finding['element']['name']}"
    if finding["best_practice"]:
        sentence += ORDER_CAVEAT
    if finding["status"] == "NEEDS REVIEW":
        sentence += MODEL_CAVEAT
    return sentence + "."


def test_packets_cover_walker_and_model_findings_with_statuses(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)

    result = write_packets(run_dir, template=True)

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
    assert packets[0]["element"] == INVITE
    assert packets[2]["element"] == HELP
    assert packets[0]["observed"] == W1_FINDINGS[0]["detail"]
    assert packets[0]["reviewer"] == {
        "question": "inside",
        "answer": "fail",
        "reason": "Focus stayed behind the dialog.",
    }
    model = packets[3]
    assert model["question"] == "inside"
    assert model["step"] == 1
    assert model["element"] == MENU
    assert model["observed"] == "Focus stayed on Menu while the menu opened."
    for packet in packets:
        assert packet["disclaimer"] == DISCLAIMER
        assert packet["sentence_source"] == "template"
        assert acceptable_sentence(packet["sentence"])
    assert DISCLAIMER == (
        "Observed keyboard-only test evidence."
        " Not a conformance claim and not legal advice."
    )


def test_template_packets_make_no_call_and_record_their_inputs(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)
    transport = SentenceTransport(plain)

    result = write_packets(run_dir, template=True, transport=transport)

    assert transport.bodies == []
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
        "Unreachable: the element A#help was never focused in a full Tab cycle."
    )
    assert result["packets"][3]["sentence"] == (
        'Focus management at step 1 on the button "Menu": a model saw focus'
        " left outside what opened after Enter; a person should confirm."
    )


def test_spark_writes_one_sentence_per_finding_from_the_witness_data(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)
    transport = SentenceTransport(plain)

    result = write_packets(run_dir, transport=transport)

    assert len(transport.bodies) == 1
    request = transport.bodies[0]
    assert request["model"] == "muse-spark-1.3"
    assert request["reasoning"] == {"effort": "minimal"}
    assert request["text"]["format"]["type"] == "json_schema"
    assert len(request["input"][0]["content"]) == 1
    text = request["input"][0]["content"][0]["text"]
    sent = json.loads(text[text.index("{") :])["findings"]
    assert sent[0] == {
        "id": "P1",
        "rule": "focus-management",
        "status": "VERIFIED",
        "step": 3,
        "element": {"role": "button", "name": "Invite people", "tag": "BUTTON"},
        "best_practice": False,
        "observed": W1_FINDINGS[0]["detail"],
    }
    assert sent[1]["best_practice"] is True
    assert sent[1]["observed"] == 'jumps up 92 px from textbox "Name"'
    assert sent[2]["element"] == {"role": None, "name": None, "tag": "A"}
    assert sent[3]["status"] == "NEEDS REVIEW"
    assert "W3C allows other meaningful orders" in text
    assert [p["sentence"] for p in result["packets"]] == [
        "Observed focus-management on Invite people.",
        f"Observed order on Email{ORDER_CAVEAT}.",
        "Observed unreachable on None.",
        f"Observed focus-management on Menu{MODEL_CAVEAT}.",
    ]
    assert {p["sentence_source"] for p in result["packets"]} == {"spark"}
    assert result["writer"]["mode"] == "spark"
    assert result["writer"]["model"] == "muse-spark-1.3"
    assert result["writer"]["reasoning_effort"] == "minimal"
    assert result["writer"]["usage"]["input_tokens"] == 400
    assert result["writer"]["error"] is None


@pytest.mark.parametrize(
    "sentence",
    [
        "The page is not compliant because focus stays on Menu.",
        "This dialog is non-compliant.",
        "Focus order is conformant here.",
        "This violates the law for public sites.",
        "Focus stays on Invite, a WCAG 2.4.3 failure.",
        "The ADA requires focus to move.",
        "The menu meets accessibility requirements.",
        "The dialog passes the standard.",
        "The page is accessible.",
        "The page is inaccessible to keyboard users.",
        "Focus order fails wcag.",
        "This breaks Section  508.",
        "This fails 2.4.3.",
        "This is a Level AA failure.",
        "This fails EN 301 549.",
        "Focus order breaks the guidelines.",
        "The site could be sued over this.",
        "This would expose the site to liability.",
        "This is a regulatory problem.",
        "Focus stayed on Menu.\rA second line.",
        "Focus stayed on Menu.\nA second line.",
        "Focus stayed on Menu",
        "Focus stayed on Menu. It did not move.",
        "",
    ],
)
def test_a_sentence_that_claims_status_or_is_not_one_sentence_falls_back(
    tmp_path: Path, sentence: str
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)

    def write(packet_id: str, finding: dict[str, Any]) -> str:
        return sentence if packet_id == "P2" else plain(packet_id, finding)

    result = write_packets(run_dir, transport=SentenceTransport(write))

    assert not acceptable_sentence(sentence)
    sources = [p["sentence_source"] for p in result["packets"]]
    assert sources == ["spark", "template", "spark", "spark"]
    assert result["packets"][1]["sentence"] == (
        'Order (best practice) at step 6 on the textbox "Email":'
        " jumps up 92 px from INPUT#name; the Tab order differs from the visual"
        " layout, and W3C allows other meaningful orders, so a person should"
        " review it."
    )
    assert result["packets"][1]["rejected"] == sentence


def strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for item in value.values() for s in strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in strings(item)]
    return [value] if isinstance(value, str) else []


def test_the_writer_call_never_sees_dom_ids_or_the_page_file(tmp_path: Path) -> None:
    findings = [
        *W1_FINDINGS,
        {
            "rule": "focus-management",
            "step": 6,
            "element": INVITE,
            "detail": "after Escape, focus did not return to the trigger"
            " (now INPUT#email)",
        },
    ]
    run_dir = write_run(tmp_path / "run", findings=findings)
    transport = SentenceTransport(plain)

    result = write_packets(run_dir, transport=transport)

    text = transport.bodies[0]["input"][0]["content"][0]["text"]
    sent = json.loads(text[text.index("{") :])["findings"]
    assert sent[3]["observed"] == (
        'after Escape, focus did not return to the trigger (now textbox "Email")'
    )
    values = strings(sent)
    for dom_id in ("invite", "menu", "email", "name", "help"):
        assert not any(re.search(rf"\b{dom_id}\b", value) for value in values)
    assert "#" not in text
    assert "p.html" not in text
    assert "127.0.0.1" not in text
    assert result["packets"][3]["observed"] == findings[3]["detail"]


@pytest.mark.parametrize(
    ("packet_id", "sentence"),
    [
        ("P2", "Tab moved up from Name to Email."),
        ("P2", "Best practice: Tab moved up from Name to Email, so check it."),
        (
            "P2",
            "Tab moved up from Name to Email, so the order differs from the"
            " visual layout; W3C allows other meaningful orders.",
        ),
        ("P4", "Focus stayed on Menu while the menu opened."),
        ("P4", "A check measured that focus stayed on Menu."),
    ],
)
def test_a_sentence_without_the_caveat_its_status_needs_falls_back(
    tmp_path: Path, packet_id: str, sentence: str
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)

    def write(asked_id: str, finding: dict[str, Any]) -> str:
        return sentence if asked_id == packet_id else plain(asked_id, finding)

    result = write_packets(run_dir, transport=SentenceTransport(write))

    assert acceptable_sentence(sentence)
    packets = {p["id"]: p for p in result["packets"]}
    assert packets[packet_id]["sentence_source"] == "template"
    assert packets[packet_id]["rejected"] == sentence
    others = [p["sentence_source"] for p in packets.values() if p["id"] != packet_id]
    assert others == ["spark", "spark", "spark"]


def no_network(url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
    raise urllib.error.URLError("no network")


def server_error(
    url: str, body: bytes, headers: Mapping[str, str]
) -> tuple[int, bytes]:
    return 500, b'{"error": {"message": "down"}}'


class EditingTransport(SentenceTransport):
    """Answers every finding, then lets `edit` change the sentences list."""

    def __init__(self, edit: Callable[[list[dict[str, Any]]], None]) -> None:
        super().__init__(plain)
        self.edit = edit

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        status, raw = super().__call__(url, body, headers)
        reply = json.loads(raw)
        part = reply["output"][0]["content"][0]
        answer = json.loads(part["text"])
        self.edit(answer["sentences"])
        part["text"] = json.dumps(answer)
        return status, json.dumps(reply).encode()


def drop_first(sentences: list[dict[str, Any]]) -> None:
    del sentences[0]


def number_first(sentences: list[dict[str, Any]]) -> None:
    sentences[0]["id"] = 1


@pytest.mark.parametrize(
    "transport",
    [
        no_network,
        server_error,
        EditingTransport(drop_first),
        EditingTransport(number_first),
    ],
    ids=["no-network", "http-500", "missing-id", "numeric-id"],
)
def test_a_failed_call_falls_back_to_the_template_for_every_packet(
    tmp_path: Path, transport: Any
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)

    result = write_packets(run_dir, transport=transport)

    assert result["writer"]["mode"] == "template"
    assert result["writer"]["error"]
    assert {p["sentence_source"] for p in result["packets"]} == {"template"}
    assert (run_dir / "packets.json").is_file()


def test_packets_without_a_review_hold_the_walker_findings(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run")

    result = write_packets(run_dir, template=True)

    assert result["review"] is None
    assert {p["status"] for p in result["packets"]} == {"VERIFIED"}
    assert "reviewer" not in result["packets"][0]


def test_a_named_review_is_used_and_must_exist(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW, review_name="review-g.json")

    with pytest.raises(PacketError, match="review-h.json"):
        write_packets(run_dir, run="h", template=True)
    result = write_packets(run_dir, run="g", template=True)

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

    result = write_packets(run_dir, template=True)

    assert result["packets"] == []


def test_a_returned_fail_the_walker_missed_is_its_own_packet(tmp_path: Path) -> None:
    run_dir = write_run(
        tmp_path / "run", findings=[], review=[form_i(0, "pass", "fail")]
    )

    result = write_packets(run_dir, template=True)

    (packet,) = result["packets"]
    assert (packet["question"], packet["step"]) == ("returned", 2)
    assert packet["sentence"] == (
        'Focus management at step 2 on the button "Menu": a model saw focus'
        " not return to it after Escape; a person should confirm."
    )


def test_a_returned_answer_without_an_escape_makes_no_packet(tmp_path: Path) -> None:
    instance = form_i(0, "fail", "fail")
    instance["steps"]["escape"] = None
    run_dir = write_run(tmp_path / "run", findings=[], review=[instance])

    result = write_packets(run_dir, template=True)

    assert [(p["question"], p["step"]) for p in result["packets"]] == [("inside", 1)]


def test_packets_refuse_unfrozen_verdicts_or_a_review_of_other_verdicts(
    tmp_path: Path,
) -> None:
    run_dir = write_run(tmp_path / "a", review=REVIEW)
    (run_dir / "verdicts.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PacketError, match="hash"):
        write_packets(run_dir, template=True)

    other = write_run(tmp_path / "b", review=REVIEW)
    review = json.loads((other / "review.json").read_text())
    review["verdicts_sha256"] = "0" * 64
    (other / "review.json").write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(PacketError, match="other verdicts"):
        write_packets(other, template=True)

    assert not (run_dir / "packets.json").exists()
    assert not (other / "packets.json").exists()


def test_packets_never_overwrite(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)
    (run_dir / "packets.json").write_text("{}", encoding="utf-8")
    transport = SentenceTransport(plain)

    with pytest.raises(FileExistsError):
        write_packets(run_dir, transport=transport)

    assert transport.bodies == []
    assert (run_dir / "packets.json").read_text(encoding="utf-8") == "{}"


def test_a_walk_without_findings_makes_no_call(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path / "run", findings=[])
    transport = SentenceTransport(plain)

    result = write_packets(run_dir, transport=transport)

    assert transport.bodies == []
    assert result["packets"] == []
    assert result["writer"] == {"mode": "template", "error": None, "usage": None}


def test_cli_packets_writes_the_file_and_prints_the_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)
    transport = SentenceTransport(plain)
    monkeypatch.setattr(spark, "urllib_transport", transport)

    assert main(["packets", str(run_dir)]) == 0

    out = capsys.readouterr().out
    assert len(transport.bodies) == 1
    assert (run_dir / "packets.json").is_file()
    assert "4 packets: 3 VERIFIED, 1 NEEDS REVIEW" in out
    assert "sentences: spark (4 from the model, 0 from the template)" in out
    assert "400 input and 90 output tokens" in out
    assert str(run_dir / "packets.json") in out


def test_cli_packets_with_template_makes_no_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW, review_name="review-g.json")
    transport = SentenceTransport(plain)
    monkeypatch.setattr(spark, "urllib_transport", transport)

    assert main(["packets", str(run_dir), "--run", "g", "--template"]) == 0

    assert transport.bodies == []
    out = capsys.readouterr().out
    assert "sentences: template (0 from the model, 4 from the template)" in out
    written = json.loads((run_dir / "packets.json").read_text(encoding="utf-8"))
    assert written["review"]["file"] == "review-g.json"


def test_cli_packets_names_the_fallback_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)
    monkeypatch.setattr(spark, "urllib_transport", server_error)

    assert main(["packets", str(run_dir)]) == 0

    assert "template fallback: HTTP 500: down" in capsys.readouterr().out


@pytest.mark.parametrize("existing", ["packets.json", None])
def test_cli_packets_refuses_an_existing_file_or_unfrozen_verdicts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], existing: str | None
) -> None:
    run_dir = write_run(tmp_path / "run", review=REVIEW)
    if existing:
        (run_dir / existing).write_text("{}", encoding="utf-8")
    else:
        (run_dir / "verdicts.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main(["packets", str(run_dir), "--template"])

    assert exit_info.value.code == 2
    assert ("already exists" if existing else "hash") in capsys.readouterr().err
