"""Scorer: compares frozen reviews with the hashed builder labels.

Deterministic and eval only: it reads `labels.json`, each walk record, its
frozen walker verdicts and one review per run directory, and makes no model
call. The model never sees the labels or the DOM ids; the scorer maps the
model's answers to DOM ids through the walk record's step numbers.

Form (ii) answers exist only in reviews made before the product shrank to
form (i) (2026-10-30); the scorer keeps reading them so the G3a reviews on
record can be scored again.

Scoring rules, fixed before any gate run:

- A page is scored under the form its label names: order pages under form
  (ii), focus-management pages under form (i). Findings under the other form
  are ignored for that page.
- An instance is on the carrier when its elements carry labelled ids: a form
  (ii) Tab transition when both the element focus left and the one it landed
  on have a DOM id in the page's `carrier_ids`; a form (i) instance when the
  element focused before Enter (the trigger) has one.
- Strict catch of a failure page: form (ii), an answer `breaks` on a carrier
  transition; form (i), an `inside` answer `fail` on a carrier instance
  (every focus-management failure label describes where focus goes after
  Enter, so a `returned` fail alone is not the labelled barrier).
- Loose catch of a failure page: any finding under its form on the page.
- False flag: a clean page with any finding under its form.
- G3a: the failures are the order pages of kind `monotone` on which W1's
  frozen verdicts have no `order` finding, the controls are the clean order
  pages. PASS when the strict catches are at least 2 and at most 1 control
  is flagged.
- Focus-management gate: the failures are the focus-management failure pages
  on which W1's frozen verdicts have no `focus-management` finding, the
  controls are the clean focus-management pages. PASS when the strict
  catches are at least 2 and at most 1 control is flagged.
- A gate is INCOMPLETE, never PASS or FAIL, unless every labelled page of
  its population (all monotone and clean order pages for G3a, all
  focus-management pages for the other) was scored.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from nomouse.driver import RECORD_NAME
from nomouse.reviewer import review_path
from nomouse.walkers import VERDICTS_NAME

GATE_MIN_CATCHES = 2
GATE_MAX_FALSE_FLAGS = 1

_FORMS = {"order": "ii", "focus-management": "i"}


class ScoreError(Exception):
    """The inputs cannot be scored: a hash mismatch, a missing file or label."""


def score(labels_path: Path, run_dirs: Sequence[Path], *, run: str) -> dict[str, Any]:
    """Scores the reviews of a set of walks against the labels.

    Args:
        labels_path: The labels file; `<labels>.sha256` must hold its hash.
        run_dirs: Walk run directories, one per page, each with its frozen
            verdicts and the review labelled `run`.
        run: The review's run label (`review-<run>.json`).

    Returns:
        The report: labels hash, the prompt hashes the reviews used, summed
        usage, one entry per page (form, W1 flag, answers on the carrier,
        strict catch, finding count) and both gates with their pages, counts
        and verdict.

    Raises:
        ScoreError: The labels or verdicts do not match their hash, a review
            was made on other verdicts or is missing, a page has no label,
            is scored twice or has a form outside the two gates.
    """
    labels_sha256 = _checked_digest(labels_path)
    labels = {
        entry["page"]: entry
        for entry in json.loads(labels_path.read_text(encoding="utf-8"))["pages"]
    }
    pages: list[dict[str, Any]] = []
    prompts: list[str] = []
    usages: list[Mapping[str, Any]] = []
    for run_dir in run_dirs:
        page, review = _page(run_dir, run, labels)
        if any(scored["page"] == page["page"] for scored in pages):
            raise ScoreError(f"{page['page']} is scored twice")
        pages.append(page)
        if review["prompt_sha256"] not in prompts:
            prompts.append(review["prompt_sha256"])
        usages.append(review["usage"])
    scored = {page["page"]: page for page in pages}
    return {
        "labels_sha256": labels_sha256,
        "run": run,
        "prompt_sha256": prompts,
        "usage": {key: sum(int(u[key]) for u in usages) for key in usages[0]},
        "pages": pages,
        "gates": {
            "G3a": _gate(
                scored,
                labels,
                lambda e: (
                    e["form"] == "order"
                    and (e["status"] == "clean" or e["kind"] == "monotone")
                ),
            ),
            "focus-management": _gate(
                scored, labels, lambda e: e["form"] == "focus-management"
            ),
        },
    }


def _checked_digest(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    recorded = path.with_name(f"{path.name}.sha256").read_text(encoding="utf-8")
    if recorded.split()[:1] != [digest]:
        raise ScoreError(f"{path} does not match its hash")
    return digest


def _page(
    run_dir: Path, run: str, labels: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Scores one run directory; returns the page entry and its review."""
    record = json.loads((run_dir / RECORD_NAME).read_text(encoding="utf-8"))
    name = record["url"].rsplit("/", 1)[-1]
    label = labels.get(name)
    if label is None:
        raise ScoreError(f"no label for {name}")
    if label["form"] not in _FORMS:
        raise ScoreError(f"{name} has form {label['form']}, outside the gates")
    verdicts = run_dir / VERDICTS_NAME
    digest = _checked_digest(verdicts)
    path = review_path(run_dir, run)
    if not path.is_file():
        raise ScoreError(f"no {path.name} in {run_dir}")
    review = json.loads(path.read_text(encoding="utf-8"))
    if review["verdicts_sha256"] != digest:
        raise ScoreError(f"the review of {name} was made on other verdicts")
    w1 = next(
        v
        for v in json.loads(verdicts.read_text(encoding="utf-8"))["verdicts"]
        if v["walker"] == "W1"
    )
    form = _FORMS[label["form"]]
    ids = [record["initial"]["focus"]["dom_id"]]
    ids += [step["focus"]["dom_id"] for step in record["steps"]]
    carriers = set(label["carrier_ids"])
    instances = [i for i in review["instances"] if i["form"] == form]
    answers = []
    strict = False
    for instance in instances:
        ends = ("from", "to") if form == "ii" else ("trigger",)
        on = [
            ids[0 if instance["steps"][end] is None else instance["steps"][end] + 1]
            for end in ends
        ]
        if not all(dom_id in carriers for dom_id in on):
            continue
        answers.append({"steps": instance["steps"], "ids": on, **instance["answer"]})
        if form == "ii":
            strict = strict or instance["answer"]["answer"] == "breaks"
        else:
            strict = strict or instance["answer"]["inside"]["answer"] == "fail"
    page = {
        "page": name,
        "form": form,
        "status": label["status"],
        "kind": label["kind"],
        # W1's rule names match the label forms: order, focus-management.
        "w1_flagged": any(f["rule"] == label["form"] for f in w1["findings"]),
        "carrier_answers": answers,
        "strict": strict and label["status"] == "failure",
        "findings": sum(bool(i["finding"]) for i in instances),
        "verdicts_sha256": digest,
        "prompt_sha256": review["prompt_sha256"],
    }
    return page, review


def _gate(
    scored: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    member: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    population = sorted(name for name, entry in labels.items() if member(entry))
    missing = [name for name in population if name not in scored]
    present = [scored[name] for name in population if name in scored]
    failures = [
        p["page"] for p in present if p["status"] == "failure" and not p["w1_flagged"]
    ]
    clean = [p["page"] for p in present if p["status"] == "clean"]
    caught_strict = [n for n in failures if scored[n]["strict"]]
    caught_loose = [n for n in failures if scored[n]["findings"]]
    false_flags = [n for n in clean if scored[n]["findings"]]
    if missing:
        verdict = "INCOMPLETE"
    elif (
        len(caught_strict) >= GATE_MIN_CATCHES
        and len(false_flags) <= GATE_MAX_FALSE_FLAGS
    ):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {
        "failures": failures,
        "caught_strict": caught_strict,
        "caught_loose": caught_loose,
        "clean": clean,
        "false_flags": false_flags,
        "complete": not missing,
        "missing": missing,
        "verdict": verdict,
    }
