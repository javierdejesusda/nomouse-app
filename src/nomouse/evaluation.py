"""Four-arm eval: W0, W1, NoMouse and bare Spark against the hashed labels.

Deterministic, no model call. Every arm is scored on the same frozen walk per
page. Rules, fixed before any eval run (2026-10-30):

- Rows: each builder label is one row (id: the page stem). Its failure types
  come from its form: order `focus-order`, trap `keyboard-trap`,
  focus-management `focus-not-moved` on a failure (every builder label
  describes focus after Enter) and `focus-not-moved` or `focus-not-returned`
  on a clean control. Its carrier names are the accessible names the walk
  recorded on elements with a carrier id. Each W3C row is taken as written in
  `eval/w3c-labels.json`.
- Findings and their types: walker rules map to types (order `focus-order`,
  trap `keyboard-trap`, unreachable `not-focusable`, focus-not-visible
  `focus-not-visible`, cover `focus-obscured`, focus-management after Enter
  `focus-not-moved`, after Escape `focus-not-returned`). A form (i) answer
  `inside: fail` is `focus-not-moved` and `returned: fail` is
  `focus-not-returned`, both on the trigger (the element focused before
  Enter). A bare-Spark item carries its own type and the element text it
  gave.
- Loose match: a finding on the row's page whose type is one of the row's.
- Strict match: a loose match on the carrier. A walker or reviewer finding
  is on the carrier when its element's DOM id is a carrier id or its tag a
  carrier tag; a bare-Spark item when its element text and a carrier name
  match (see `names_match`). A row with no carrier ids or tags (walker and
  reviewer), or no carrier names (bare Spark), is matched by type alone.
- Arms: W0 and W1 from the frozen verdicts (VERIFIED). NoMouse per run: W1,
  plus the form (i) review of that run; a row is VERIFIED when W1 matches it
  strictly, NEEDS REVIEW when only the review does. Bare Spark per run: its
  answer, NEEDS REVIEW.
- A barrier is found when a failure row is matched strictly (the loose count
  is reported beside it). A false flag is a clean row matched loosely (the
  strict count beside it). A clean page is a page whose rows are all clean;
  any finding of any type there is counted separately.
- Model-only catches of a run: failure rows NoMouse marks NEEDS REVIEW.
  Reporting bar (pre-registered, form (i) only): at least 3 model-only
  catches, and at most 1 clean focus-management control with a reviewer
  finding of its types; met only when met in every run.
- Disagreements: rows whose strict or loose result differs between runs.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nomouse.bare import bare_path
from nomouse.driver import RECORD_NAME
from nomouse.reviewer import review_path
from nomouse.walkers import VERDICTS_NAME

VERIFIED = "VERIFIED"
NEEDS_REVIEW = "NEEDS REVIEW"
BAR_MIN_CATCHES = 3
BAR_MAX_CONTROL_FLAGS = 1
# Standard prices, USD per token (https://dev.meta.ai/docs/pricing-rate-limits).
INPUT_PRICE = 1.25e-6
OUTPUT_PRICE = 4.25e-6

_RULE_TYPES = {
    "order": "focus-order",
    "trap": "keyboard-trap",
    "unreachable": "not-focusable",
    "focus-not-visible": "focus-not-visible",
    "cover": "focus-obscured",
}
_FORM_TYPES = {"order": ["focus-order"], "trap": ["keyboard-trap"]}
_FM_FAILURE = ["focus-not-moved"]
_FM_CLEAN = ["focus-not-moved", "focus-not-returned"]
# Words naming a role, not an element; dropped before names are compared.
_ROLE_WORDS = frozenset(
    {"a", "an", "the", "button", "link", "field", "input", "text", "box"}
    | {"textbox", "checkbox", "radio"}
)


class EvalError(Exception):
    """The inputs cannot be evaluated: a hash mismatch, a missing file or label."""


@dataclass(frozen=True)
class _Finding:
    type: str
    dom_id: str | None = None
    tag: str | None = None
    name: str | None = None
    named_only: bool = False


def names_match(said: str, carrier: str) -> bool:
    """Returns whether a model's element text names a carrier.

    Both are case-folded, split into words at anything but letters and
    digits, and stripped of role words (button, link, field and the like);
    they match when either word sequence occurs whole inside the other.

    Args:
        said: The element text the model gave.
        carrier: A carrier's visible name.

    Returns:
        True when one non-empty word sequence contains the other.
    """
    a, b = _words(said), _words(carrier)
    return bool(a and b) and (_contains(a, b) or _contains(b, a))


def _words(text: str) -> list[str]:
    words = re.split(r"[^0-9a-z]+", text.casefold())
    return [word for word in words if word and word not in _ROLE_WORDS]


def _contains(outer: Sequence[str], inner: Sequence[str]) -> bool:
    size = len(inner)
    return any(outer[i : i + size] == inner for i in range(len(outer) - size + 1))


def evaluate(
    builder_labels: Path,
    w3c_labels: Path,
    run_dirs: Sequence[Path],
    *,
    runs_labels: Sequence[str],
) -> dict[str, Any]:
    """Scores every arm on a set of frozen walks against both label files.

    Args:
        builder_labels: `builder/labels.json`; its `.sha256` must match.
        w3c_labels: `eval/w3c-labels.json`; its `.sha256` must match.
        run_dirs: Walk run directories, one per labelled page, each with its
            frozen verdicts and, per run label, `review-<run>.json` and
            `bare-<run>.json` made on those verdicts.
        runs_labels: The model arms' run labels, in order.

    Returns:
        The report: label hashes, run labels, prompt hashes, usage and cost,
        one entry per row with its result per arm, counts per arm, model-only
        catches, the reporting bar, disagreements between runs, and the
        labelled pages with no run.

    Raises:
        EvalError: A label file or verdicts do not match their hash, an
            answer was made on other verdicts or is missing, a page has no
            label or is given twice.
    """
    hashes = {"builder": _checked(builder_labels), "w3c": _checked(w3c_labels)}
    builder = json.loads(builder_labels.read_text(encoding="utf-8"))["pages"]
    w3c = json.loads(w3c_labels.read_text(encoding="utf-8"))["rows"]
    labelled = {entry["page"] for entry in builder} | {r["page"] for r in w3c}
    arms = ["W0", "W1"]
    arms += [f"NoMouse {run}" for run in runs_labels]
    arms += [f"bare {run}" for run in runs_labels]

    rows: list[dict[str, Any]] = []
    pages: dict[str, dict[str, list[_Finding]]] = {}
    prompts: dict[str, list[str]] = {"reviewer": [], "bare": []}
    usage: dict[str, dict[str, Any]] = {}
    for run_dir in run_dirs:
        page, record, found = _page(run_dir, runs_labels, prompts, usage)
        if page in pages:
            raise EvalError(f"{page} is given twice")
        if page not in labelled:
            raise EvalError(f"no label for {page}")
        pages[page] = found
        names = _names_by_id(record)
        rows += [_builder_row(e, names) for e in builder if e["page"] == page]
        rows += [_w3c_row(r) for r in w3c if r["page"] == page]
    for row in rows:
        row["arms"] = _results(row, pages[row["page"]], runs_labels)

    model_only = {
        run: [
            r["id"]
            for r in rows
            if r["status"] == "failure"
            and r["arms"][f"NoMouse {run}"]["status"] == NEEDS_REVIEW
        ]
        for run in runs_labels
    }
    per_run: dict[str, dict[str, Any]] = {}
    for run in runs_labels:
        flags = sum(
            r["status"] == "clean"
            and r["form"] == "focus-management"
            and _matches(r, pages[r["page"]][f"reviewer {run}"], strict=False)
            for r in rows
        )
        catches = len(model_only[run])
        per_run[run] = {
            "model_only_catches": catches,
            "control_flags": flags,
            "met": catches >= BAR_MIN_CATCHES and flags <= BAR_MAX_CONTROL_FLAGS,
        }
    bar = {"runs": per_run, "met": all(r["met"] for r in per_run.values())}
    totals = [usage[key] for key in usage]
    total_in = sum(u["input_tokens"] for u in totals)
    total_out = sum(u["output_tokens"] for u in totals)
    usage["total"] = {
        "calls": sum(u["calls"] for u in totals),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cost_usd": total_in * INPUT_PRICE + total_out * OUTPUT_PRICE,
    }
    missing = sorted(labelled - set(pages))
    return {
        "labels_sha256": hashes,
        "runs": list(runs_labels),
        "prompt_sha256": prompts,
        "usage": usage,
        "rows": rows,
        "arms": {
            arm: {
                **_counts(arm, rows, pages),
                **{
                    kind: _counts(arm, [r for r in rows if r["set"] == kind], pages)
                    for kind in ("builder", "w3c")
                },
            }
            for arm in arms
        },
        "model_only": model_only,
        "bar": bar,
        "disagreements": {
            kind: [
                r["id"]
                for r in rows
                if len({_pair(r["arms"][f"{kind} {run}"]) for run in runs_labels}) > 1
            ]
            for kind in ("NoMouse", "bare")
        },
        "missing": missing,
        "complete": not missing,
    }


def _checked(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    hash_file = path.with_name(f"{path.name}.sha256")
    recorded = hash_file.read_text(encoding="utf-8") if hash_file.is_file() else ""
    if recorded.split()[:1] != [digest]:
        raise EvalError(f"{path} does not match its hash")
    return digest


def _load_answer(path: Path, digest: str) -> dict[str, Any]:
    if not path.is_file():
        raise EvalError(f"no {path.name} in {path.parent}")
    answer: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if answer["verdicts_sha256"] != digest:
        raise EvalError(f"{path} was made on other verdicts")
    return answer


def _page(
    run_dir: Path,
    runs_labels: Sequence[str],
    prompts: dict[str, list[str]],
    usage: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any], dict[str, list[_Finding]]]:
    """Reads one run directory; returns its page, walk and findings per arm."""
    verdicts_path = run_dir / VERDICTS_NAME
    digest = _checked(verdicts_path)
    verdicts = json.loads(verdicts_path.read_text(encoding="utf-8"))
    record = json.loads((run_dir / RECORD_NAME).read_text(encoding="utf-8"))
    observations = [record["initial"], *record["steps"]]
    found = {
        v["walker"]: [_walker_finding(f) for f in v["findings"]]
        for v in verdicts["verdicts"]
    }
    for run in runs_labels:
        review = _load_answer(review_path(run_dir, run), digest)
        found[f"reviewer {run}"] = list(_review_findings(review, observations))
        bare = _load_answer(bare_path(run_dir, run), digest)
        found[f"bare {run}"] = [
            _Finding(item["type"], name=item["element"], named_only=True)
            for item in bare["failures"]
        ]
        for kind, answer in (("reviewer", review), ("bare", bare)):
            if answer["prompt_sha256"] not in prompts[kind]:
                prompts[kind].append(answer["prompt_sha256"])
            spent = usage.setdefault(
                f"{kind} {run}", {"calls": 0, "input_tokens": 0, "output_tokens": 0}
            )
            spent["calls"] += int(answer["usage"].get("calls", 1))
            spent["input_tokens"] += int(answer["usage"]["input_tokens"])
            spent["output_tokens"] += int(answer["usage"]["output_tokens"])
    return verdicts["page"].lstrip("/"), record, found


def _walker_finding(finding: Mapping[str, Any]) -> _Finding:
    rule = finding["rule"]
    if rule == "focus-management":
        escape = finding["detail"].startswith("after Escape")
        kind = "focus-not-returned" if escape else "focus-not-moved"
    else:
        kind = _RULE_TYPES[rule]
    element = finding["element"]
    return _Finding(kind, element["dom_id"], element["tag"], element["name"])


def _review_findings(
    review: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]
) -> Iterable[_Finding]:
    for instance in review["instances"]:
        trigger = instance["steps"]["trigger"]
        focus = observations[0 if trigger is None else trigger + 1]["focus"]
        answer = instance["answer"]
        for question, kind in (
            ("inside", "focus-not-moved"),
            ("returned", "focus-not-returned"),
        ):
            if answer[question]["answer"] == "fail":
                yield _Finding(kind, focus["dom_id"], focus["tag"], focus["name"])


def _names_by_id(record: Mapping[str, Any]) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    for observation in [record["initial"], *record["steps"]]:
        focus = observation["focus"]
        if focus["dom_id"] and focus["name"]:
            names.setdefault(focus["dom_id"], set()).add(focus["name"])
    return names


def _builder_row(
    entry: Mapping[str, Any], names: Mapping[str, set[str]]
) -> dict[str, Any]:
    form = entry["form"]
    if form == "focus-management":
        types = _FM_FAILURE if entry["status"] == "failure" else _FM_CLEAN
    else:
        types = _FORM_TYPES[form]
    carriers = entry["carrier_ids"]
    return {
        "id": entry["page"].removesuffix(".html"),
        "page": entry["page"],
        "set": "builder",
        "note": entry["barrier"],
        "status": entry["status"],
        "form": form,
        "types": list(types),
        "carrier_ids": list(carriers),
        "carrier_tags": [],
        "carrier_names": sorted(set().union(*(names.get(c, set()) for c in carriers))),
    }


def _w3c_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("id", "page", "status", "types")
    carriers = ("carrier_ids", "carrier_tags", "carrier_names")
    return {
        **{key: entry[key] for key in keys},
        "set": "w3c",
        "note": entry["source"],
        "form": None,
        **{key: list(entry[key]) for key in carriers},
    }


def _on_carrier(row: Mapping[str, Any], finding: _Finding) -> bool:
    if finding.named_only:
        names = row["carrier_names"]
        return not names or any(names_match(finding.name or "", n) for n in names)
    if not row["carrier_ids"] and not row["carrier_tags"]:
        return True
    return finding.dom_id in row["carrier_ids"] or finding.tag in row["carrier_tags"]


def _matches(
    row: Mapping[str, Any], findings: Iterable[_Finding], *, strict: bool
) -> bool:
    return any(
        f.type in row["types"] and (not strict or _on_carrier(row, f)) for f in findings
    )


def _results(
    row: Mapping[str, Any],
    found: Mapping[str, list[_Finding]],
    runs_labels: Sequence[str],
) -> dict[str, dict[str, Any]]:
    def result(findings: list[_Finding], status: str) -> dict[str, Any]:
        strict = _matches(row, findings, strict=True)
        return {
            "strict": strict,
            "loose": _matches(row, findings, strict=False),
            "status": status if strict else None,
        }

    results = {walker: result(found[walker], VERIFIED) for walker in ("W0", "W1")}
    for run in runs_labels:
        nomouse = result(found["W1"] + found[f"reviewer {run}"], NEEDS_REVIEW)
        if results["W1"]["strict"]:
            nomouse["status"] = VERIFIED
        results[f"NoMouse {run}"] = nomouse
        results[f"bare {run}"] = result(found[f"bare {run}"], NEEDS_REVIEW)
    return results


def _arm_findings(arm: str, found: Mapping[str, list[_Finding]]) -> list[_Finding]:
    if arm.startswith("NoMouse "):
        return found["W1"] + found[f"reviewer {arm.removeprefix('NoMouse ')}"]
    return found[arm]


def _counts(
    arm: str,
    rows: Sequence[Mapping[str, Any]],
    pages: Mapping[str, Mapping[str, list[_Finding]]],
) -> dict[str, int]:
    failures = [r["arms"][arm] for r in rows if r["status"] == "failure"]
    clean = [r["arms"][arm] for r in rows if r["status"] == "clean"]
    dirty = {r["page"] for r in rows if r["status"] == "failure"}
    clean_pages = sorted({r["page"] for r in rows} - dirty)
    return {
        "failures": len(failures),
        "found": sum(r["strict"] for r in failures),
        "verified": sum(r["status"] == VERIFIED for r in failures),
        "needs_review": sum(r["status"] == NEEDS_REVIEW for r in failures),
        "found_loose": sum(r["loose"] for r in failures),
        "clean": len(clean),
        "false_flags": sum(r["loose"] for r in clean),
        "false_flags_strict": sum(r["strict"] for r in clean),
        "clean_pages": len(clean_pages),
        "clean_pages_any_finding": sum(
            bool(_arm_findings(arm, pages[page])) for page in clean_pages
        ),
    }


def _pair(result: Mapping[str, Any]) -> tuple[bool, bool]:
    return result["strict"], result["loose"]
