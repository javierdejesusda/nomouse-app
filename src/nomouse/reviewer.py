"""Focus reviewer: Muse Spark judges a frozen walk from its screenshots.

Form (i) asks, for each Enter and the Escape after it, whether focus landed
inside what visibly opened and came back to the trigger. Form (ii) asks, for
each Tab transition, whether the move follows the visual reading order.

The model sees screenshots and the focused element's role, accessible name
and box as JSON text, never labels, DOM ids or walker verdicts. It runs only
on a walk whose walker verdicts are frozen and match their hash, and records
that hash. Every model finding has status NEEDS REVIEW.

Form (ii) sends one layout screenshot per view instead of one per Tab: a view
is a scroll position between two non-Tab keys, during which a page without
timers keeps its layout, so the first screenshot of the view shows where
every element focused in it sits. A page that fits the viewport costs one
image plus about 60 text tokens per transition, against about 1,037 tokens per
screenshot. Limit: content that only shows while focused (a skip link) is
absent from the layout screenshot; its box is still given.
"""

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nomouse.driver import RECORD_NAME
from nomouse.spark import (
    MAX_IMAGES,
    MODEL,
    REASONING_EFFORT,
    SparkReply,
    Transport,
    ask,
    urllib_transport,
)
from nomouse.walkers import VERDICTS_NAME

REVIEW_NAME = "review.json"
NEEDS_REVIEW = "NEEDS REVIEW"
ORDER_STATUS = "best practice, NEEDS REVIEW"

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

FORM_II_PROMPT = (
    "You are reviewing the Tab order of a web page from a keyboard-only walk."
    " The attached screenshots of the 1024x768 viewport show the page layout,"
    " numbered from 1 in the order attached; a screenshot may show a focus"
    " indicator on some element, which you can ignore. The JSON below lists"
    " the Tab transitions in the order they were pressed: for each, the"
    " element focus left and the element it landed on, each with the number"
    " of the screenshot that shows it, its role, accessible name and border"
    " box in that screenshot's pixels. For each transition answer follows if"
    " the move follows the visual reading order a sighted user would expect"
    " from the layout, breaks if it breaks that order, or unsure if the"
    " screenshots do not show enough, with a one-sentence reason naming what"
    " you saw.\n"
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
FORM_II_SCHEMA = _answer_list(
    "transitions",
    {
        "step": {"type": "integer"},
        "answer": {"type": "string", "enum": ["follows", "breaks", "unsure"]},
        "reason": _REASON,
    },
)

# Keeps one reply's reasons short enough to stay reliable.
_TAB_BATCH = 40

_DOCUMENT_TAGS = frozenset({"BODY", "HTML"})
_RUN_LABEL = re.compile(r"[A-Za-z0-9_-]+")


class ReviewError(Exception):
    """The run cannot be reviewed, or the model's answer does not fit."""


class AnswerError(ReviewError):
    """The model's answer does not fit its question."""


@dataclass(frozen=True)
class _Instance:
    form: str
    steps: dict[str, int | None]
    question: dict[str, Any]
    images: list[str]


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
    """Reviews a frozen walk with forms (i) and (ii) and writes the review.

    Args:
        run_dir: Run directory with the walk record, its screenshots and the
            frozen walker verdicts with their `.sha256`.
        run: Optional run label; the review goes to `review-<run>.json`.
        transport: Sends the HTTP requests.

    Returns:
        The review, as written: page, run label, verdicts hash, model,
        reasoning effort, one entry per instance with the model's answer,
        whether it is a finding and its status, usage totals and per-call
        usage.

    Raises:
        ValueError: The run label is not a plain name.
        FileExistsError: The review file already exists.
        ReviewError: The verdicts are missing or do not match their hash, or
            the walk record is missing.
        AnswerError: An answer does not fit its question (nothing is
            written).
        SparkError: A model call failed.
    """
    path = review_path(run_dir, run)
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    digest = _frozen_digest(run_dir)
    record_path = run_dir / RECORD_NAME
    if not record_path.is_file():
        raise ReviewError(f"no {RECORD_NAME} in {run_dir}")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    observations: list[dict[str, Any]] = [record["initial"], *record["steps"]]

    instances: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    batches = [
        *(("i", b) for b in _batches(_enter_instances(observations))),
        *(("ii", b) for b in _batches(_tab_instances(observations), _TAB_BATCH)),
    ]
    for form, batch in batches:
        answers, reply, images = _ask(run_dir, form, batch, transport)
        calls.append(
            {
                "form": form,
                "images": images,
                "instances": len(batch),
                "usage": reply.usage,
                "latency_s": reply.latency_s,
            }
        )
        for instance, answer in zip(batch, answers, strict=True):
            finding = _is_finding(form, answer)
            instances.append(
                {
                    "form": form,
                    "steps": instance.steps,
                    "answer": answer,
                    "finding": finding,
                    "status": _status(form) if finding else None,
                }
            )
    result = {
        "page": record["url"],
        "run": run,
        "verdicts_sha256": digest,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "instances": instances,
        "usage": _totals([call["usage"] for call in calls]),
        "calls": calls,
    }
    with path.open("x", encoding="utf-8") as out:
        out.write(json.dumps(result, indent=2) + "\n")
    return result


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
            form="i",
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


def _tab_instances(observations: Sequence[dict[str, Any]]) -> Iterator[_Instance]:
    """Yields a form (ii) instance per Tab that moves between two elements.

    Each element carries the screenshot that opens its view, standing for
    the layout it was focused in.
    """
    view_shot: dict[tuple[float, float, int], str] = {}
    shots: list[str] = []
    epoch = 0
    for observation in observations:
        if observation.get("key") not in (None, "Tab"):
            epoch += 1
        view = (observation["scroll_x"], observation["scroll_y"], epoch)
        shots.append(view_shot.setdefault(view, observation["screenshot"]))
    for position in range(1, len(observations)):
        before, step = observations[position - 1], observations[position]
        source, landing = before["focus"], step["focus"]
        if (
            step["key"] != "Tab"
            or _focus(source) is None
            or _focus(landing) is None
            or source["backend_node_id"] == landing["backend_node_id"]
        ):
            continue
        yield _Instance(
            form="ii",
            steps={"from": before.get("index"), "to": step["index"]},
            question={
                "step": step["index"],
                "from": _focus(source),
                "to": _focus(landing),
            },
            images=[shots[position - 1], shots[position]],
        )


def _focus(focus: Mapping[str, Any]) -> dict[str, Any] | None:
    if focus["tag"] is None or focus["tag"] in _DOCUMENT_TAGS or not focus["box"]:
        return None
    return {"role": focus["role"], "name": focus["name"], "box": focus["box"]}


def _batches(
    instances: Iterator[_Instance], size: int = MAX_IMAGES
) -> Iterator[list[_Instance]]:
    """Groups instances in order, each group within `size` and the image limit.

    Form (i) is bounded by images alone: 16 instances of 3 screenshots.
    """
    batch: list[_Instance] = []
    for instance in instances:
        if batch and (
            len(batch) >= size or len(_shots([*batch, instance])) > MAX_IMAGES
        ):
            yield batch
            batch = []
        batch.append(instance)
    if batch:
        yield batch


def _shots(batch: Sequence[_Instance]) -> list[str]:
    """Returns the screenshots one request attaches, in order.

    Form (i) attaches each instance's own screenshots; form (ii) attaches
    each layout screenshot once.
    """
    shots = [shot for instance in batch for shot in instance.images]
    return shots if batch[0].form == "i" else list(dict.fromkeys(shots))


def _ask(
    run_dir: Path, form: str, batch: Sequence[_Instance], transport: Transport
) -> tuple[list[dict[str, Any]], SparkReply, int]:
    """Asks one batch and returns the validated answers in batch order."""
    shots = _shots(batch)
    questions = []
    first = 1
    for instance in batch:
        question = dict(instance.question)
        if form == "i":
            question["screenshots"] = [
                {"image": n, **shown}
                for n, shown in enumerate(question["screenshots"], start=first)
            ]
            first += len(instance.images)
        else:
            for end, shot in zip(("from", "to"), instance.images, strict=True):
                question[end] = {"image": shots.index(shot) + 1, **question[end]}
        questions.append(question)
    key, prompt, schema, name = (
        ("instances", FORM_I_PROMPT, FORM_I_SCHEMA, "FocusManagementReview")
        if form == "i"
        else ("transitions", FORM_II_PROMPT, FORM_II_SCHEMA, "TabOrderReview")
    )
    reply = ask(
        prompt + json.dumps({key: questions}),
        [(run_dir / shot).read_bytes() for shot in shots],
        schema=(name, schema),
        transport=transport,
    )
    asked = [question["step"] for question in questions]
    return _validated(reply.text, key, asked, schema), reply, len(shots)


def _validated(
    text: str, key: str, asked: Sequence[int], schema: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Checks the reply against the schema and returns one answer per step."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise AnswerError("the model's answer is not JSON") from error
    item_schema = schema["properties"][key]["items"]
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise AnswerError(f"the model's answer has no {key} list")
    answers = data[key]
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


def _is_finding(form: str, answer: Mapping[str, Any]) -> bool:
    if form == "i":
        return "fail" in (answer["inside"]["answer"], answer["returned"]["answer"])
    return bool(answer["answer"] == "breaks")


def _status(form: str) -> str:
    return NEEDS_REVIEW if form == "i" else ORDER_STATUS


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
