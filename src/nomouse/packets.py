"""Packet writer: one packet per finding, with one plain sentence.

Walker W1's frozen findings become VERIFIED packets (W1 runs every W0 rule
too). Focus reviewer form (i) answers of `fail` become NEEDS REVIEW packets,
unless W1 fired on the same Enter or Escape: then the model's answer is kept
on the VERIFIED packet as corroboration, since the status says who saw it.
Order packets come from W1 alone and are marked best practice, because a Tab
order need not follow the layout.

The sentence comes from one Muse Spark text call over the witness data, or
from a fixed template when asked to, when the call fails, or for any
sentence that is not one plain sentence or that speaks of conformance,
compliance or the law. Every packet carries the fixed disclaimer.
"""

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nomouse.driver import RECORD_NAME
from nomouse.reviewer import ReviewError, frozen_digest, review_path
from nomouse.spark import (
    MODEL,
    REASONING_EFFORT,
    SparkError,
    Transport,
    ask,
    urllib_transport,
)
from nomouse.walkers import VERDICTS_NAME

PACKETS_NAME = "packets.json"
DISCLAIMER = (
    "Observed keyboard-only test evidence."
    " Not a conformance claim and not legal advice."
)
VERIFIED = "VERIFIED"
NEEDS_REVIEW = "NEEDS REVIEW"

WRITER_PROMPT = (
    "You write the evidence packets of a keyboard-only test of a web page."
    " Each finding below says what was observed while a driver pressed Tab,"
    " Enter and Escape: the rule, its status (VERIFIED: a deterministic check"
    " measured it; NEEDS REVIEW: only a model saw it in screenshots), the key"
    " step, the element's role and accessible name, and the observation. For"
    " each finding write one plain sentence for a developer that says what"
    " happened to keyboard focus, using only the facts given. For a NEEDS"
    " REVIEW finding say that a model saw it and a person should confirm."
    " Never say whether the page is compliant, conformant, accessible or"
    " legal, and do not name laws, standards or WCAG.\n"
)
WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "sentence": {"type": "string"},
                },
                "required": ["id", "sentence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}

_TITLES = {
    "trap": "Keyboard trap",
    "unreachable": "Unreachable",
    "focus-not-visible": "Focus not visible",
    "order": "Order (best practice)",
    "cover": "Focus covered",
    "focus-management": "Focus management",
}
_MODEL_SAW = {
    "inside": "focus left outside what opened after Enter",
    "returned": "focus not return to it after Escape",
}
_QUESTION_STEP = {"inside": "enter", "returned": "escape"}
_MAX_SENTENCE = 300
_FORBIDDEN = re.compile(
    r"complian|conform|violat|\blaw|legal|lawsuit|\bADA\b|section 508|WCAG"
    r"|certif|success criteri",
    re.IGNORECASE,
)


class PacketError(Exception):
    """The run cannot be turned into packets."""


class _ReplyError(Exception):
    """The writer's reply does not give one sentence per finding."""


def acceptable_sentence(text: str) -> bool:
    """Tells whether a sentence may stand in a packet.

    Args:
        text: A candidate sentence.

    Returns:
        True when it is one line, one sentence ending in a period, at most
        300 characters, and says nothing about conformance, compliance,
        certification or the law.
    """
    sentence = text.strip()
    return (
        bool(sentence)
        and "\n" not in sentence
        and len(sentence) <= _MAX_SENTENCE
        and sentence.endswith(".")
        and not re.search(r"[.!?]\s", sentence)
        and not _FORBIDDEN.search(sentence)
    )


def write_packets(
    run_dir: Path,
    *,
    run: str | None = None,
    template: bool = False,
    transport: Transport = urllib_transport,
) -> dict[str, Any]:
    """Writes `packets.json` for a frozen walk and, if present, its review.

    Args:
        run_dir: Run directory with the walk record and frozen verdicts.
        run: Review run label: reads `review-<run>.json`, which must exist.
            Without it, `review.json` is read when present.
        template: Write every sentence from the template, with no call.
        transport: Sends the HTTP request of the writer call.

    Returns:
        The packets file as written: page, verdicts hash, the review used,
        the disclaimer, how the sentences were written (mode, model, usage,
        error) and the packets.

    Raises:
        ValueError: The run label is not a plain name.
        FileExistsError: `packets.json` already exists.
        PacketError: The verdicts are missing or do not match their hash,
            the named review is missing, or the review was made on other
            verdicts.
    """
    out = run_dir / PACKETS_NAME
    if out.exists():
        raise FileExistsError(f"{out} already exists")
    try:
        digest = frozen_digest(run_dir)
    except ReviewError as error:
        raise PacketError(str(error)) from error
    record = json.loads((run_dir / RECORD_NAME).read_text(encoding="utf-8"))
    verdicts = json.loads((run_dir / VERDICTS_NAME).read_text(encoding="utf-8"))
    w1 = next(v for v in verdicts["verdicts"] if v["walker"] == "W1")
    review_file = review_path(run_dir, run)
    if run is not None and not review_file.is_file():
        raise PacketError(f"no {review_file.name} in {run_dir}")
    review = None
    if review_file.is_file():
        review = json.loads(review_file.read_text(encoding="utf-8"))
        if review["verdicts_sha256"] != digest:
            raise PacketError(f"{review_file.name} was made on other verdicts")

    observations = [record["initial"], *record["steps"]]
    packets = _walker_packets(w1["findings"])
    if review is not None:
        packets += _model_packets(review["instances"], observations, packets)
    for number, packet in enumerate(packets, start=1):
        packet["id"] = f"P{number}"
    witnesses = [_witness(packet, observations) for packet in packets]
    writer = _write_sentences(packets, witnesses, template or not packets, transport)
    result = {
        "page": record["url"],
        "verdicts_sha256": digest,
        "review": None
        if review is None
        else {"file": review_file.name, "prompt_sha256": review["prompt_sha256"]},
        "disclaimer": DISCLAIMER,
        "writer": writer,
        "packets": [_ordered(packet) for packet in packets],
    }
    with out.open("x", encoding="utf-8") as file:
        file.write(json.dumps(result, indent=2) + "\n")
    return result


def _walker_packets(findings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "status": VERIFIED,
            "best_practice": finding["rule"] == "order",
            "source": "W1",
            "rule": finding["rule"],
            "step": finding["step"],
            "element": _element(finding["element"]),
            "observed": finding["detail"],
        }
        for finding in findings
    ]


def _model_packets(
    instances: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    verified: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Returns NEEDS REVIEW packets; W1-corroborated answers join `verified`.

    Only form (i) answers count; form (ii) answers exist only in reviews made
    before the Tab-order form left the product.
    """
    walker_steps = {
        packet["step"]: packet
        for packet in verified
        if packet["rule"] == "focus-management"
    }
    packets = []
    for instance in instances:
        if instance["form"] != "i":
            continue
        trigger = instance["steps"]["trigger"]
        focus = observations[0 if trigger is None else trigger + 1]["focus"]
        for question, step_name in _QUESTION_STEP.items():
            judged = instance["answer"][question]
            step = instance["steps"][step_name]
            # Without an Escape there was nothing to return from.
            if judged["answer"] != "fail" or step is None:
                continue
            if step in walker_steps:
                walker_steps[step]["reviewer"] = {"question": question, **judged}
                continue
            packets.append(
                {
                    "status": NEEDS_REVIEW,
                    "best_practice": False,
                    "source": "reviewer form (i)",
                    "rule": "focus-management",
                    "question": question,
                    "step": step,
                    "element": _element(focus),
                    "observed": judged["reason"],
                }
            )
    return packets


def _element(found: Mapping[str, Any]) -> dict[str, Any]:
    return {key: found[key] for key in ("tag", "dom_id", "role", "name")}


def _write_sentences(
    packets: list[dict[str, Any]],
    witnesses: Sequence[Mapping[str, Any]],
    template: bool,
    transport: Transport,
) -> dict[str, Any]:
    """Fills each packet's sentence and returns how they were written."""
    if template:
        for packet in packets:
            _use_template(packet)
        return {"mode": "template", "error": None, "usage": None}
    usage = None
    try:
        reply = ask(
            WRITER_PROMPT + json.dumps({"findings": witnesses}),
            schema=("PacketSentences", WRITER_SCHEMA),
            transport=transport,
        )
        usage = reply.usage
        sentences = _sentences(reply.text, [p["id"] for p in packets])
    except (SparkError, OSError, _ReplyError) as error:
        for packet in packets:
            _use_template(packet)
        return {
            "mode": "template",
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "error": str(error),
            "usage": usage,
        }
    for packet in packets:
        sentence = sentences[packet["id"]]
        if acceptable_sentence(sentence):
            packet["sentence"] = sentence.strip()
            packet["sentence_source"] = "spark"
        else:
            _use_template(packet)
            packet["rejected"] = sentence
    return {
        "mode": "spark",
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "error": None,
        "usage": usage,
    }


def _witness(
    packet: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Returns what the writer call sees of a packet: no DOM ids.

    A walker detail names the element focus left or moved to as `TAG#id`
    (the element before the step, or at it); it is restated by role and
    accessible name.
    """
    observed = packet["observed"]
    if packet["source"] == "W1" and packet["step"] is not None:
        step = packet["step"]
        for observation in observations[step : step + 2]:
            focus = observation["focus"]
            if focus["dom_id"]:
                label = re.escape(f"{focus['tag']}#{focus['dom_id']}")
                plain = _describe(focus, with_id=False).replace("\\", "\\\\")
                observed = re.sub(rf"{label}(?=\)|$)", plain, observed)
    element = packet["element"]
    return {
        "id": packet["id"],
        "rule": packet["rule"],
        "status": packet["status"],
        "step": packet["step"],
        "element": {key: element[key] for key in ("role", "name", "tag")},
        "observed": observed,
    }


def _sentences(text: str, ids: Sequence[str]) -> dict[str, str]:
    try:
        data = json.loads(text)
        sentences = {item["id"]: item["sentence"] for item in data["sentences"]}
    except (ValueError, KeyError, TypeError) as error:
        raise _ReplyError("the writer's reply is not the sentences list") from error
    if len(sentences) != len(data["sentences"]) or set(sentences) != set(ids):
        raise _ReplyError(f"the writer answered {list(sentences)}, asked {list(ids)}")
    if not all(isinstance(s, str) for s in sentences.values()):
        raise _ReplyError("the writer's reply has a sentence that is not text")
    return sentences


def _use_template(packet: dict[str, Any]) -> None:
    packet["sentence"] = _template(packet)
    packet["sentence_source"] = "template"


def _template(packet: Mapping[str, Any]) -> str:
    target = _describe(packet["element"])
    title = _TITLES.get(packet["rule"], packet["rule"])
    if packet["status"] == NEEDS_REVIEW:
        saw = _MODEL_SAW[packet["question"]]
        return (
            f"{title} at step {packet['step']} on the {target}: a model saw"
            f" {saw}; a person should confirm."
        )
    if packet["step"] is None:
        return f"{title}: the {target} was {packet['observed']}."
    return f"{title} at step {packet['step']} on the {target}: {packet['observed']}."


def _describe(element: Mapping[str, Any], *, with_id: bool = True) -> str:
    role, name, tag = element["role"], element["name"], element["tag"]
    if role and name:
        return f'{role} "{name}"'
    if name:
        return f'element "{name}"'
    if role:
        return str(role)
    if not tag or tag in ("BODY", "HTML"):
        return "page"
    dom_id = element["dom_id"]
    return f"element {tag}#{dom_id}" if with_id and dom_id else f"element {tag}"


def _ordered(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Puts the id first and the disclaimer last, for reading."""
    rest = {key: value for key, value in packet.items() if key != "id"}
    return {"id": packet["id"], **rest, "disclaimer": DISCLAIMER}
