"""Static replay site built from frozen runs, viewable with no API key.

The export copies each walk's screenshots byte for byte and writes JSON for a
plain HTML, CSS and JavaScript frontend: an index of pages, a per-page viewer
and the eval counts. W3C captures are third-party content, so the export is a
build artifact that is never committed; it ignores itself in git.
"""

import datetime
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nomouse import reviewer
from nomouse.driver import RECORD_NAME
from nomouse.mirror import WALK_PAGES
from nomouse.walkers import FROZEN_AT_NAME, VERDICTS_NAME

W3C_NOTICE = "Copyright © 2012 W3C (MIT, ERCIM, Keio), All Rights Reserved"
W3C_BASE_URL = "https://www.w3.org/WAI/demos/bad/"
BUILDER_LABEL = "builder-made"
DEFAULT_REVIEW_RUNS: tuple[str, ...] = ("eval1", "eval2")
EVAL_REPORT_NAME = "eval-report.json"

_FRONTEND = Path(__file__).parent / "replay"
_RUN_SETS = ("eval", "builder", "navigator")
_NAVIGATOR_TITLE = " (keyboard navigator)"
_REVIEW_QUESTIONS = ("inside", "returned")
_EVAL_KEYS = ("runs", "labels_sha256", "arms", "bar", "model_only", "disagreements")


class ExportError(Exception):
    """A run cannot be exported as it stands."""


def export_site(
    runs_dir: Path,
    out: Path,
    *,
    review_runs: Sequence[str] = DEFAULT_REVIEW_RUNS,
    audit: Path | None = None,
) -> dict[str, Any]:
    """Builds the replay site from the frozen runs under a runs directory.

    Every directory in `<runs_dir>/eval`, `<runs_dir>/builder` and
    `<runs_dir>/navigator` (keyboard navigator runs) holding frozen verdicts
    becomes a page. The eval counts come from
    `<runs_dir>/eval/eval-report.json` when it exists.

    Args:
        runs_dir: The runs directory.
        out: Directory to write the site into; created, and must be empty.
        review_runs: Review run labels whose reviews and packets are shown.
        audit: Optional JSON file of audit-corrected strict false-flag
            counts per arm (`false_flags_strict`) with their `date`,
            `source` and `reason`, shown beside the recorded counts.

    Returns:
        The index written to `data/index.json`.

    Raises:
        ExportError: The output directory is not empty, two runs share a
            name, verdicts do not match their hash, a review was made on
            other verdicts, or the audit names an arm the report lacks.
    """
    if out.exists() and any(out.iterdir()):
        raise ExportError(f"{out} is not empty; choose a new directory")
    run_dirs = [
        path
        for name in _RUN_SETS
        for path in (runs_dir / name).glob("*/")
        if (path / VERDICTS_NAME).is_file()
    ]
    sources = {path.name: path for path in run_dirs}
    if len(sources) < len(run_dirs):
        names = [path.name for path in run_dirs]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise ExportError(f"two runs named {', '.join(duplicates)}")
    pages = sorted(
        (_page(path, set(sources), review_runs) for path in run_dirs),
        key=lambda page: (
            page["set"] != "w3c",
            WALK_PAGES.index(page["page"]) if page["set"] == "w3c" else 0,
            page["id"],
        ),
    )

    report_path = runs_dir / "eval" / EVAL_REPORT_NAME
    eval_data = _eval_data(report_path, audit) if report_path.is_file() else None

    shutil.copytree(_FRONTEND, out, dirs_exist_ok=True)
    (out / ".gitignore").write_text("*\n", encoding="utf-8")
    (out / "data" / "pages").mkdir(parents=True)
    for page in pages:
        _write_page(out, page, sources[page["id"]])

    if eval_data is not None:
        _write_json(out / "data" / "eval.json", eval_data)
    index = {
        "pages": [_summary(page) for page in pages],
        "eval": eval_data is not None,
    }
    _write_json(out / "data" / "index.json", index)
    return index


def export_run(
    run_dir: Path,
    site: Path,
    *,
    review_runs: Sequence[str],
    live: dict[str, Any],
) -> dict[str, Any]:
    """Adds one live run to an exported site, viewable at `page.html?id=<name>`.

    The site index is left unchanged. A builder page is titled after its
    file, not after the run.

    Args:
        run_dir: The live run directory, named after the run.
        site: A site written by `export_site`.
        review_runs: Review run labels whose reviews and packets are shown.
        live: Facts about the live run, shown with it.

    Returns:
        The page's index entry.

    Raises:
        ExportError: The site already holds a page of that name, the
            verdicts do not match their hash, or a review was made on other
            verdicts.
    """
    if (site / "data" / "pages" / f"{run_dir.name}.json").exists():
        raise ExportError(f"the site already holds a page named {run_dir.name}")
    page = {**_page(run_dir, set(), review_runs), "live": live}
    if page["set"] == "builder" and page["navigator"] is None:
        page["title"] = Path(page["page"]).stem.replace("-", " ").capitalize()
    _write_page(site, page, run_dir)
    return _summary(page)


def _write_page(out: Path, page: dict[str, Any], source: Path) -> None:
    """Writes a page's JSON and copies its captures byte for byte."""
    (out / "captures" / page["id"]).mkdir(parents=True)
    for frame in page["frames"]:
        shutil.copyfile(
            source / Path(frame["screenshot"]).name, out / frame["screenshot"]
        )
    _write_json(out / "data" / "pages" / f"{page['id']}.json", page)


def _eval_data(report_path: Path, audit: Path | None) -> dict[str, Any]:
    """Returns the counts the eval page shows, with any audit correction."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    data = {key: report[key] for key in _EVAL_KEYS}
    if audit is not None:
        corrections = json.loads(audit.read_text(encoding="utf-8"))
        unknown = sorted(set(corrections["false_flags_strict"]) - set(report["arms"]))
        if unknown:
            raise ExportError(f"the audit names arms not in the report: {unknown}")
        data["audit"] = {
            key: corrections[key]
            for key in ("date", "source", "reason", "false_flags_strict")
        }
    return data


def _page(run_dir: Path, names: set[str], review_runs: Sequence[str]) -> dict[str, Any]:
    """Returns the viewer data of one frozen run."""
    try:
        digest = reviewer.frozen_digest(run_dir)
    except reviewer.ReviewError as error:
        raise ExportError(str(error)) from error
    record = json.loads((run_dir / RECORD_NAME).read_text(encoding="utf-8"))
    verdicts = json.loads((run_dir / VERDICTS_NAME).read_text(encoding="utf-8"))
    page_path = verdicts["page"].lstrip("/")
    w3c = page_path in WALK_PAGES
    frozen_at, frozen_at_source = _frozen_at(run_dir)
    reviews = [
        _review(run_dir, run, digest)
        for run in review_runs
        if reviewer.review_path(run_dir, run).is_file()
    ]
    successor = f"{run_dir.name}-v2"
    navigator = _navigator(record)
    # Navigator runs are named by whoever ran them; their page names them.
    name = run_dir.name if navigator is None else Path(page_path).stem
    title = _title(name, page_path, w3c)
    if navigator is not None:
        title += _NAVIGATOR_TITLE
    return {
        "id": run_dir.name,
        "set": "w3c" if w3c else "builder",
        "label": f"W3C {page_path.split('/')[0].title()}" if w3c else BUILDER_LABEL,
        "title": title,
        "page": page_path,
        "source_url": W3C_BASE_URL + page_path if w3c else None,
        "notice": W3C_NOTICE if w3c else None,
        "superseded_by": successor if successor in names else None,
        "keys": verdicts["keys"],
        "navigator": navigator,
        "verdicts": {
            "sha256": digest,
            "frozen_at": frozen_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "frozen_at_source": frozen_at_source,
            "thresholds": verdicts["thresholds"],
            "walkers": [_walker(v) for v in verdicts["verdicts"]],
        },
        "frames": [
            _frame(run_dir.name, observation, step)
            for step, observation in enumerate(
                [record["initial"], *record["steps"]], start=-1
            )
        ],
        "reviews": reviews,
        "disagreements": _disagreements(reviews),
        "packets": [
            _packets(run_dir / f"packets-{run}.json", run)
            for run in review_runs
            if (run_dir / f"packets-{run}.json").is_file()
        ],
    }


def _navigator(record: dict[str, Any]) -> dict[str, Any] | None:
    """Returns what a keyboard navigator was asked and answered, if it ran.

    Per-turn usage and latency stay in the walk record.
    """
    navigator = record.get("navigator")
    if navigator is None:
        return None
    return {
        **{
            key: navigator[key]
            for key in (
                "task",
                "model",
                "reasoning_effort",
                "prompt_sha256",
                "max_keys",
            )
        },
        "end_reason": record["end_reason"],
        "turns": [
            {"answer": turn["answer"], "reason": turn.get("reason")}
            for turn in navigator["turns"]
        ],
        "usage": {
            key: navigator["usage"][key]
            for key in ("calls", "input_tokens", "output_tokens")
        },
    }


def _frozen_at(run_dir: Path) -> tuple[datetime.datetime, str]:
    """Returns the freeze time and where it came from.

    Runs frozen before the freeze time was recorded fall back to the
    modification time of the hash file, which a copy may not keep.
    """
    sidecar = run_dir / FROZEN_AT_NAME
    if sidecar.is_file():
        stamp = sidecar.read_text(encoding="utf-8").strip()
        return datetime.datetime.fromisoformat(stamp), "recorded"
    mtime = (run_dir / f"{VERDICTS_NAME}.sha256").stat().st_mtime
    return datetime.datetime.fromtimestamp(mtime, datetime.UTC), "file time"


def _title(name: str, page_path: str, w3c: bool) -> str:
    """Returns a W3C page's name, or a builder run's name made readable."""
    if w3c:
        return Path(page_path).stem.title()
    return name.replace("-", " ").capitalize()


def _walker(verdict: dict[str, Any]) -> dict[str, Any]:
    """Returns one walker's frozen verdicts, each finding marked VERIFIED."""
    return {
        "walker": verdict["walker"],
        "checked": verdict["checked"],
        "findings": [
            {
                **finding,
                "status": "VERIFIED",
                "best_practice": finding["rule"] == "order",
            }
            for finding in verdict["findings"]
        ],
    }


def _frame(run_id: str, observation: dict[str, Any], step: int) -> dict[str, Any]:
    """Returns one viewer frame: the state before the first key or after one."""
    focus = observation["focus"]
    return {
        "step": None if step < 0 else step,
        "key": observation.get("key"),
        "screenshot": f"captures/{run_id}/{observation['screenshot']}",
        "scroll_x": observation["scroll_x"],
        "scroll_y": observation["scroll_y"],
        "appeared": len(observation["appeared"]),
        "focus": {key: focus[key] for key in ("tag", "role", "name", "box")},
    }


def _review(run_dir: Path, run: str, digest: str) -> dict[str, Any]:
    """Returns one form (i) review run, each failed answer marked NEEDS REVIEW."""
    path = reviewer.review_path(run_dir, run)
    review = json.loads(path.read_text(encoding="utf-8"))
    if review["verdicts_sha256"] != digest:
        raise ExportError(f"{path.name} in {run_dir} was made on other verdicts")
    instances = []
    for instance in review["instances"]:
        answer = instance["answer"]
        instances.append(
            {
                "steps": instance["steps"],
                "opened": answer["opened"],
                **{
                    question: {
                        **answer[question],
                        "status": reviewer.NEEDS_REVIEW
                        if answer[question]["answer"] == "fail"
                        else None,
                    }
                    for question in _REVIEW_QUESTIONS
                },
            }
        )
    return {
        "run": run,
        "prompt_sha256": review["prompt_sha256"],
        "model": review["model"],
        "reasoning_effort": review["reasoning_effort"],
        "instances": instances,
    }


def _disagreements(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns the (Enter step, question) pairs the review runs answer apart."""
    answers: dict[tuple[int, str], set[str]] = {}
    for review in reviews:
        for instance in review["instances"]:
            for question in _REVIEW_QUESTIONS:
                key = (instance["steps"]["enter"], question)
                answers.setdefault(key, set()).add(instance[question]["answer"])
    return [
        {"enter": enter, "question": question}
        for (enter, question), seen in sorted(answers.items())
        if len(seen) > 1
    ]


def _packets(path: Path, run: str) -> dict[str, Any]:
    """Returns a packets file's run label, disclaimer and packets."""
    packets = json.loads(path.read_text(encoding="utf-8"))
    return {
        "run": run,
        "disclaimer": packets["disclaimer"],
        "packets": packets["packets"],
    }


def _summary(page: dict[str, Any]) -> dict[str, Any]:
    """Returns a page's index entry."""
    w1 = next(w for w in page["verdicts"]["walkers"] if w["walker"] == "W1")
    model = {
        (instance["steps"]["enter"], question)
        for review in page["reviews"]
        for instance in review["instances"]
        for question in _REVIEW_QUESTIONS
        if instance[question]["status"]
    }
    keys = ("id", "set", "label", "title", "page", "source_url", "notice")
    return {
        **{key: page[key] for key in keys},
        "superseded_by": page["superseded_by"],
        "task": page["navigator"]["task"] if page["navigator"] else None,
        "keys": len(page["keys"]),
        "walker_findings": len(w1["findings"]),
        "model_findings": len(model),
    }


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
