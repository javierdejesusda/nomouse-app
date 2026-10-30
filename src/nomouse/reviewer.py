"""Focus reviewer: Muse Spark judges a frozen walk from its screenshots.

Form (i) asks, for each Enter and the Escape after it, whether focus landed
inside what visibly opened and came back to the trigger. It is the only form:
the Tab-order form (ii) failed gate G3a and left the product (owner decision,
2026-10-30); order findings come from walker W1 alone.

The model sees screenshots and the focused element's role, accessible name
and box as JSON text, never labels, DOM ids or walker verdicts. It runs only
on a walk whose walker verdicts are frozen and match their hash, and records
that hash. Every model finding has status NEEDS REVIEW.
"""

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from nomouse.driver import RECORD_NAME
from nomouse.spark import (
    MAX_IMAGES,
    MODEL,
    REASONING_EFFORT,
    Transport,
    ask,
    urllib_transport,
)
from nomouse.walkers import VERDICTS_NAME

REVIEW_NAME = "review.json"
NEEDS_REVIEW = "NEEDS REVIEW"
_PARTIAL_NOTE = (
    "incomplete: completed calls of a review that has not finished or failed;"
    " not a review"
)

FORM_I_PROMPT = (
    "You are reviewing a keyboard-only walk of a web page. A driver pressed"
    " Tab to reach a control, then Enter, then Escape. For each instance in"
    " the JSON below, up to three screenshots of the 1024x768 viewport are"
    " attached, numbered from 1 across the whole message in the order"
    " listed: before Enter (focus on the control), after Enter, and after"
    " Escape. For each screenshot the JSON gives the element the browser"
    " reports as focused: its role, accessible name and border box in"
    " screenshot pixels, or null when no element is focused. Judge what a"
    " sighted keyboard user would see.\n"
    "For each instance answer:\n"
    "- opened: after Enter, did something visibly open, such as a menu,"
    " dialog, drawer, panel or revealed content? yes, no or unsure.\n"
    "- inside: after Enter, is focus where the user can operate what opened?"
    " pass when focus is inside the content that opened, or when it stays on"
    " the control and the revealed content follows the control directly in"
    " reading order; fail when focus is left outside what opened; unsure when"
    " the screenshots do not show enough. pass when nothing opened.\n"
    "- returned: after Escape, is focus back on the control that was"
    " activated? pass, fail or unsure; unsure when there is no after-Escape"
    " screenshot.\n"
    "Give each answer a one-sentence reason naming what you saw.\n"
)

_VERDICT = {"type": "string", "enum": ["pass", "fail", "unsure"]}
_REASON = {"type": "string"}


def _strict_object(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(properties),
        "additionalProperties": False,
    }


def _answer_list(key: str, item: Mapping[str, Any]) -> dict[str, Any]:
    return _strict_object({key: {"type": "array", "items": _strict_object(item)}})


_JUDGED = _strict_object({"answer": _VERDICT, "reason": _REASON})
FORM_I_SCHEMA = _answer_list(
    "instances",
    {
        "step": {"type": "integer"},
        "opened": {"type": "string", "enum": ["yes", "no", "unsure"]},
        "inside": _JUDGED,
        "returned": _JUDGED,
    },
)
_DOCUMENT_TAGS = frozenset({"BODY", "HTML"})
_RUN_LABEL = re.compile(r"[A-Za-z0-9_-]+")


class ReviewError(Exception):
    """The run cannot be reviewed, or the model's answer does not fit."""


class AnswerError(ReviewError):
    """The model's answer does not fit its question."""


@dataclass(frozen=True)
class _Instance:
    steps: dict[str, int | None]
    question: dict[str, Any]
    images: list[str]


def prompt_digest() -> str:
    """Returns the SHA-256 of the prompt and answer schema the model gets.

    A review records it, so a run can be matched to a prompt frozen earlier.

    Returns:
        The hex SHA-256 of `[FORM_I_PROMPT, FORM_I_SCHEMA]` as compact JSON
        with sorted keys, UTF-8 encoded.
    """
    frozen = json.dumps(
        [FORM_I_PROMPT, FORM_I_SCHEMA],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(frozen.encode()).hexdigest()


def review_path(run_dir: Path, run: str | None = None) -> Path:
    """Returns where a review of a run is written.

    Args:
        run_dir: The walk's run directory.
        run: Optional run label, for repeated reviews of the same walk.

    Returns:
        `review.json`, or `review-<run>.json` when a label is given.

    Raises:
        ValueError: The label is not made of letters, digits, `_` and `-`.
    """
    if run is None:
        return run_dir / REVIEW_NAME
    if not _RUN_LABEL.fullmatch(run):
        raise ValueError(f"run label {run!r} must use only letters, digits, _ and -")
    return run_dir / f"review-{run}.json"


def review(
    run_dir: Path,
    *,
    run: str | None = None,
    transport: Transport = urllib_transport,
) -> dict[str, Any]:
    """Reviews a frozen walk with form (i) and writes the review.

    Each completed call is logged to the partial log (`partial_path`) as it
    returns, so a failed call does not lose answers already paid for; the log
    is removed once the review is written.

    Args:
        run_dir: Run directory with the walk record, its screenshots and the
            frozen walker verdicts with their `.sha256`.
        run: Optional run label; the review goes to `review-<run>.json`.
        transport: Sends the HTTP requests.

    Returns:
        The review, as written: page, run label, verdicts hash, prompt
        hash, model, reasoning effort, one entry per instance with the
        model's answer, whether it is a finding and its status, usage totals
        and per-call usage.

    Raises:
        ValueError: The run label is not a plain name.
        FileExistsError: The review file or its partial log already exists.
        ReviewError: The verdicts are missing or do not match their hash, or
            the walk record is missing.
        AnswerError: An answer does not fit its question (no review is
            written; the partial log keeps the reply).
        SparkError: A model call failed (no review is written; the partial
            log keeps the calls before it).
    """
    path = review_path(run_dir, run)
    partial = partial_path(path)
    for existing in (path, partial):
        if existing.exists():
            raise FileExistsError(f"{existing} already exists")
    digest = _frozen_digest(run_dir)
    record_path = run_dir / RECORD_NAME
    if not record_path.is_file():
        raise ReviewError(f"no {RECORD_NAME} in {run_dir}")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    observations: list[dict[str, Any]] = [record["initial"], *record["steps"]]
    head = {
        "page": record["url"],
        "run": run,
        "verdicts_sha256": digest,
        "prompt_sha256": prompt_digest(),
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
    }

    instances: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    with partial.open("x", encoding="utf-8") as log:
        _log_line(log, {"complete": False, "note": _PARTIAL_NOTE, **head})
        for batch in _batches(_enter_instances(observations)):
            try:
                answers, call = _ask(run_dir, batch, transport, log)
            except Exception as error:
                _log_line(log, {"error": str(error)})
                raise
            calls.append(call)
            for instance, answer in zip(batch, answers, strict=True):
                finding = "fail" in (
                    answer["inside"]["answer"],
                    answer["returned"]["answer"],
                )
                instances.append(
                    {
                        "form": "i",
                        "steps": instance.steps,
                        "answer": answer,
                        "finding": finding,
                        "status": NEEDS_REVIEW if finding else None,
                    }
                )
    result = {
        **head,
        "instances": instances,
        "usage": _totals([call["usage"] for call in calls]),
        "calls": calls,
    }
    with path.open("x", encoding="utf-8") as out:
        out.write(json.dumps(result, indent=2) + "\n")
    partial.unlink()
    return result


def partial_path(path: Path) -> Path:
    """Returns where a review in progress logs its completed calls.

    The log holds one JSON line per completed call, so answers already paid
    for survive a failed call. Its header says it is incomplete, and it is
    removed once the review itself is written.

    Args:
        path: The review file, as given by `review_path`.

    Returns:
        `<review name>.partial.jsonl` next to the review.
    """
    return path.with_name(f"{path.stem}.partial.jsonl")


def _log_line(log: TextIO, line: Mapping[str, Any]) -> None:
    log.write(json.dumps(line) + "\n")
    log.flush()


def _frozen_digest(run_dir: Path) -> str:
    verdicts = run_dir / VERDICTS_NAME
    hash_file = run_dir / f"{VERDICTS_NAME}.sha256"
    if not verdicts.is_file() or not hash_file.is_file():
        raise ReviewError(
            f"no frozen {VERDICTS_NAME} with its .sha256 in {run_dir};"
            " walker verdicts must be frozen before any model call"
        )
    digest = hashlib.sha256(verdicts.read_bytes()).hexdigest()
    recorded = hash_file.read_text(encoding="utf-8").split()
    if not recorded or recorded[0] != digest:
        raise ReviewError(f"{VERDICTS_NAME} in {run_dir} does not match its hash")
    return digest


def _enter_instances(observations: Sequence[dict[str, Any]]) -> Iterator[_Instance]:
    """Yields a form (i) instance per Enter, with the Escape right after it."""
    for position, step in enumerate(observations):
        if step.get("key") != "Enter":
            continue
        shown = [("before Enter", observations[position - 1])]
        shown.append(("after Enter", step))
        following = observations[position + 1 : position + 2]
        escape = following[0] if following and following[0]["key"] == "Escape" else None
        if escape is not None:
            shown.append(("after Escape", escape))
        yield _Instance(
            steps={
                "trigger": observations[position - 1].get("index"),
                "enter": step["index"],
                "escape": None if escape is None else escape["index"],
            },
            question={
                "step": step["index"],
                "screenshots": [
                    {"moment": moment, "focus": _focus(o["focus"])}
                    for moment, o in shown
                ],
            },
            images=[o["screenshot"] for _, o in shown],
        )


def _focus(focus: Mapping[str, Any]) -> dict[str, Any] | None:
    if focus["tag"] is None or focus["tag"] in _DOCUMENT_TAGS or not focus["box"]:
        return None
    return {"role": focus["role"], "name": focus["name"], "box": focus["box"]}


def _batches(instances: Iterator[_Instance]) -> Iterator[list[_Instance]]:
    """Groups instances in order, each group within the image limit.

    At 3 screenshots per instance a group holds up to 16 instances.
    """
    batch: list[_Instance] = []
    for instance in instances:
        if batch and len(_shots([*batch, instance])) > MAX_IMAGES:
            yield batch
            batch = []
        batch.append(instance)
    if batch:
        yield batch


def _shots(batch: Sequence[_Instance]) -> list[str]:
    """Returns the screenshots one request attaches, in order."""
    return [shot for instance in batch for shot in instance.images]


def _ask(
    run_dir: Path,
    batch: Sequence[_Instance],
    transport: Transport,
    log: TextIO,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Asks one batch and logs the reply, even one that does not fit.

    Returns:
        The validated answers in batch order, and the call's summary.
    """
    shots = _shots(batch)
    questions = []
    first = 1
    for instance in batch:
        question = dict(instance.question)
        question["screenshots"] = [
            {"image": n, **shown}
            for n, shown in enumerate(question["screenshots"], start=first)
        ]
        first += len(instance.images)
        questions.append(question)
    reply = ask(
        FORM_I_PROMPT + json.dumps({"instances": questions}),
        [(run_dir / shot).read_bytes() for shot in shots],
        schema=("FocusManagementReview", FORM_I_SCHEMA),
        transport=transport,
    )
    call = {
        "form": "i",
        "images": len(shots),
        "instances": len(batch),
        "usage": reply.usage,
        "latency_s": reply.latency_s,
    }
    asked = [question["step"] for question in questions]
    try:
        answers = _validated(reply.text, asked)
    except AnswerError:
        steps = [instance.steps for instance in batch]
        _log_line(log, {**call, "steps": steps, "text": reply.text})
        raise
    logged = [
        {"steps": instance.steps, "answer": answer}
        for instance, answer in zip(batch, answers, strict=True)
    ]
    _log_line(log, {**call, "answers": logged})
    return answers, call


def _validated(text: str, asked: Sequence[int]) -> list[dict[str, Any]]:
    """Checks the reply against the schema and returns one answer per step."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise AnswerError("the model's answer is not JSON") from error
    item_schema = FORM_I_SCHEMA["properties"]["instances"]["items"]
    if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
        raise AnswerError("the model's answer has no instances list")
    answers = data["instances"]
    for answer in answers:
        _check(answer, item_schema)
    by_step = {answer["step"]: answer for answer in answers}
    if len(by_step) != len(answers) or sorted(by_step) != sorted(asked):
        raise AnswerError(
            f"the model's answer covers steps {sorted(by_step)}, asked {list(asked)}"
        )
    return [{k: v for k, v in by_step[step].items() if k != "step"} for step in asked]


def _check(value: object, schema: Mapping[str, Any]) -> None:
    """Checks a value against the strict schema subset used here."""
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]):
            raise AnswerError(f"the model's answer has an unexpected shape: {value!r}")
        for name, sub in schema["properties"].items():
            _check(value[name], sub)
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise AnswerError(f"the model's answer has a non-integer step: {value!r}")
    elif not isinstance(value, str) or not value.strip():
        raise AnswerError(
            f"the model's answer has an empty or non-text field: {value!r}"
        )
    elif "enum" in schema and value not in schema["enum"]:
        raise AnswerError(f"the model's answer has an unknown value: {value!r}")


def _totals(usages: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "calls": len(usages),
        "input_tokens": sum(int(u["input_tokens"]) for u in usages),
        "cached_tokens": sum(
            int(u.get("input_tokens_details", {}).get("cached_tokens", 0))
            for u in usages
        ),
        "output_tokens": sum(int(u["output_tokens"]) for u in usages),
        "reasoning_tokens": sum(
            int(u.get("output_tokens_details", {}).get("reasoning_tokens", 0))
            for u in usages
        ),
        "total_tokens": sum(int(u["total_tokens"]) for u in usages),
    }
