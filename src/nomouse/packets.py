"""Packet writer: one packet per finding, with one plain sentence.

Walker W1's frozen findings become VERIFIED packets (W1 runs every W0 rule
too). Focus reviewer form (i) answers of `fail` become NEEDS REVIEW packets,
unless W1 fired on the same Enter or Escape: then the model's answer is kept
on the VERIFIED packet as corroboration, since the status says who saw it.
Order packets come from W1 alone and are marked best practice, because a Tab
order need not follow the layout.

Each packet's sentence comes from a fixed template over the witness data,
and every packet carries the fixed disclaimer.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nomouse.driver import RECORD_NAME
from nomouse.reviewer import ReviewError, frozen_digest, review_path
from nomouse.walkers import VERDICTS_NAME

PACKETS_NAME = "packets.json"
DISCLAIMER = (
    "Observed keyboard-only test evidence."
    " Not a conformance claim and not legal advice."
)
VERIFIED = "VERIFIED"
NEEDS_REVIEW = "NEEDS REVIEW"

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


class PacketError(Exception):
    """The run cannot be turned into packets."""


def write_packets(
    run_dir: Path,
    *,
    run: str | None = None,
) -> dict[str, Any]:
    """Writes `packets.json` for a frozen walk and, if present, its review.

    Args:
        run_dir: Run directory with the walk record and frozen verdicts.
        run: Review run label: reads `review-<run>.json`, which must exist.
            Without it, `review.json` is read when present.

    Returns:
        The packets file as written: page, verdicts hash, the review used,
        the disclaimer, how the sentences were written and the packets.

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

    packets = _walker_packets(w1["findings"])
    if review is not None:
        observations = [record["initial"], *record["steps"]]
        packets += _model_packets(review["instances"], observations, packets)
    for number, packet in enumerate(packets, start=1):
        packet["id"] = f"P{number}"
    for packet in packets:
        _use_template(packet)
    result = {
        "page": record["url"],
        "verdicts_sha256": digest,
        "review": None
        if review is None
        else {"file": review_file.name, "prompt_sha256": review["prompt_sha256"]},
        "disclaimer": DISCLAIMER,
        "writer": {"mode": "template", "error": None, "usage": None},
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
            if judged["answer"] != "fail":
                continue
            step = instance["steps"][step_name]
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
    return {"role": found["role"], "name": found["name"]}


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


def _describe(element: Mapping[str, Any]) -> str:
    role, name = element["role"], element["name"]
    if role and name:
        return f'{role} "{name}"'
    if name:
        return f'element "{name}"'
    return str(role or "page")


def _ordered(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Puts the id first and the disclaimer last, for reading."""
    rest = {key: value for key, value in packet.items() if key != "id"}
    return {"id": packet["id"], **rest, "disclaimer": DISCLAIMER}
