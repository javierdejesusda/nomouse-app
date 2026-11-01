"""Scores the outside pass (APG reference widgets) against labels.json.

Usage: python eval/outside/score.py <run dir per page, in labels order>...

Every labelled trigger is a clean control, so any W1 finding or form (i)
reviewer finding on it is a false flag. Controls outside the labelled
example (site header, footer, code-sample buttons) are counted separately.
No model call; reads walk.json, verdicts.json and review-<run>.json.
"""

import json
import sys
from pathlib import Path
from typing import Any

LABELS = Path(__file__).with_name("labels.json")
RUNS = ("outside1", "outside2")
HEADER = (
    "Page",
    "Labelled triggers opened",
    "W1 false flags",
    "Reviewer false flags, run 1",
    "Reviewer false flags, run 2",
    "Other controls opened",
    "W1 findings on them",
    "Reviewer findings on them (run 1, run 2)",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _names(walk: dict[str, Any]) -> dict[int, str]:
    """Maps each step index to the accessible name focus had there."""
    names = {}
    for position, step in enumerate([walk["initial"], *walk["steps"]]):
        focus = step.get("focus") or {}
        names[step.get("index", position)] = (focus.get("name") or "").strip()
    return names


def score_page(label: dict[str, Any], run_dir: Path) -> list[str]:
    """Returns the table cells for one page."""
    names = _names(_load(run_dir / "walk.json"))
    triggers = set(label["triggers"])
    verdicts = _load(run_dir / "verdicts.json")["verdicts"]
    w1 = next(v for v in verdicts if v["walker"] == "W1")["findings"]
    w1_on = [f for f in w1 if f["element"]["name"].strip() in triggers]
    w1_other = [
        f
        for f in w1
        if f["element"]["name"].strip() not in triggers
        and f["rule"] == "focus-management"
    ]
    opened: dict[bool, set[int]] = {True: set(), False: set()}
    flags: dict[bool, list[int]] = {True: [], False: []}
    for run in RUNS:
        counts = {True: 0, False: 0}
        for instance in _load(run_dir / f"review-{run}.json")["instances"]:
            step = instance["steps"]["trigger"]
            labelled = names.get(step, "") in triggers
            opened[labelled].add(step)
            counts[labelled] += bool(instance["finding"])
        for labelled, count in counts.items():
            flags[labelled].append(count)
    return [
        label["pattern"],
        f"{len(opened[True])} of {len(triggers)}",
        str(len(w1_on)),
        str(flags[True][0]),
        str(flags[True][1]),
        str(len(opened[False])),
        str(len(w1_other)),
        f"{flags[False][0]}, {flags[False][1]}",
    ]


def main(run_dirs: list[str]) -> None:
    """Prints the outside-pass table for the run dirs, in labels order."""
    labels = _load(LABELS)["pages"]
    if len(run_dirs) != len(labels):
        sys.exit(f"give {len(labels)} run dirs, in labels order")
    print("| " + " | ".join(HEADER) + " |")
    print("|---" * len(HEADER) + "|")
    for label, run_dir in zip(labels, run_dirs, strict=True):
        print("| " + " | ".join(score_page(label, Path(run_dir))) + " |")


if __name__ == "__main__":
    main(sys.argv[1:])
