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

LABELS = Path(__file__).with_name("labels.json")
RUNS = ("outside1", "outside2")


def _names(walk: dict) -> dict[int, str]:
    steps = [walk["initial"], *walk["steps"]] if "initial" in walk else walk["steps"]
    names = {}
    for position, step in enumerate(steps):
        focus = step.get("focus") or {}
        names[step.get("index", position)] = (focus.get("name") or "").strip()
    return names


def main(run_dirs: list[str]) -> None:
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["pages"]
    if len(run_dirs) != len(labels):
        sys.exit(f"give {len(labels)} run dirs, in labels order")
    print("| Page | Labelled triggers opened | W1 false flags | Reviewer false flags, run 1 | Reviewer false flags, run 2 | Other controls opened | W1 findings on them | Reviewer findings on them (run 1, run 2) |")
    print("|---|---|---|---|---|---|---|---|")
    totals = [0] * 7
    for label, run_dir in zip(labels, map(Path, run_dirs), strict=True):
        walk = json.loads((run_dir / "walk.json").read_text(encoding="utf-8"))
        verdicts = json.loads((run_dir / "verdicts.json").read_text(encoding="utf-8"))
        names = _names(walk)
        triggers = set(label["triggers"])
        w1 = next(v for v in verdicts["verdicts"] if v["walker"] == "W1")
        w1_on = [f for f in w1["findings"] if f["element"]["name"].strip() in triggers]
        w1_other = [
            f
            for f in w1["findings"]
            if f["element"]["name"].strip() not in triggers and f["rule"] == "focus-management"
        ]
        opened_on, opened_other, flags_on, flags_other = set(), set(), [], []
        for run in RUNS:
            review = json.loads((run_dir / f"review-{run}.json").read_text(encoding="utf-8"))
            on = other = 0
            for instance in review["instances"]:
                name = names.get(instance["steps"]["trigger"], "")
                bucket = opened_on if name in triggers else opened_other
                bucket.add(instance["steps"]["trigger"])
                if instance["finding"]:
                    if name in triggers:
                        on += 1
                    else:
                        other += 1
            flags_on.append(on)
            flags_other.append(other)
        row = [
            len(opened_on),
            len(w1_on),
            flags_on[0],
            flags_on[1],
            len(opened_other),
            len(w1_other),
        ]
        for i, value in enumerate(row):
            totals[i] += value
        totals[6] += sum(flags_other)
        print(
            f"| {label['pattern']} | {len(opened_on)} of {len(triggers)} | {len(w1_on)} | {flags_on[0]} | {flags_on[1]} |"
            f" {len(opened_other)} | {len(w1_other)} | {flags_other[0]}, {flags_other[1]} |"
        )
        for f in w1_on:
            print(f"<!-- W1 on label: {f['rule']} step {f['step']} {f['element']['name']!r}: {f['detail']} -->")
    print(f"<!-- totals: {totals} -->")


if __name__ == "__main__":
    main(sys.argv[1:])
