# Outside pass: W3C APG reference widgets

A pass outside the frozen eval, on pages I did not build, run on 2026-11-01. It is reported separately from `eval/results.md` and is not added to its counts.

- **Pages:** five W3C WAI-ARIA Authoring Practices example pages (https://www.w3.org/WAI/ARIA/apg/patterns/): modal dialog, menu button (actions), menu button (links), disclosure FAQ, disclosure navigation. They are fetched at run time and served unmodified on loopback, never committed. They are W3C content under its own licence and not hackathon work.
- **Labels:** `labels.json`, SHA-256 `dafee9be393c7aa274bd041f8bcf328b24ef184e5c07f33e9cfbcf75d994f3f1`, committed and pushed in `c854aec` (18:09 UTC) before any model call. Every labelled trigger is a reference implementation, so each is a clean control. One walk of the dialog page ran before the labels, to check that the pages can be walked; walks make no model call.
- **Arms:** W1 from the frozen walk, and the focus reviewer form (i) twice (`outside1`, `outside2`) with the same prompt as the eval (prompt hash `59707175...e686`). 10 calls, about USD 0.28.
- **Scoring:** `score.py` matches each Enter/Escape instance and each W1 finding to the labelled triggers by accessible name. The run JSON (walks, verdicts, reviews; no screenshots) is in `runs/`. Re-run everything with `eval/outside/run.sh`.

| Page | Labelled triggers opened | W1 false flags | Reviewer false flags, run 1 | Reviewer false flags, run 2 | Other controls opened | W1 findings on them | Reviewer findings on them (run 1, run 2) |
|---|---|---|---|---|---|---|---|
| modal dialog | 1 of 1 | 0 | 0 | 0 | 4 | 1 | 0, 0 |
| menu button (actions) | 1 of 1 | 0 | 0 | 0 | 4 | 1 | 0, 1 |
| menu button (links) | 1 of 1 | 0 | 0 | 0 | 4 | 1 | 0, 0 |
| disclosure (FAQ) | 4 of 4 | 4 | 0 | 0 | 4 | 1 | 0, 0 |
| disclosure navigation menu | 3 of 3 | 3 | 0 | 0 | 4 | 1 | 0, 0 |
| **Total** | **10 of 10** | **7** | **0** | **0** | **20** | **5** | **0, 1** |

What this shows, in counts:

- **On the 10 labelled reference triggers:** W1 flags 7, and the reviewer flags 0 in both runs. All 7 W1 flags are disclosures: after Enter, focus correctly stays on the toggle, and W1's rule ("focus is outside the elements that appeared") fires anyway. This is the same walker weakness as the builder page `disclosure-stays-on-toggle`, now on outside pages.
- **The reviewer disagrees with W1 on all 7.** NoMouse still reports the W1 findings as VERIFIED beside the reviewer's pass; it does not hide a walker finding. A person sees the disagreement on the page.
- **Controls outside the examples** (the site's SkipTo menu, the "code is not production-ready" notice and the CodePen buttons) are not labelled. W1 has 5 focus-management findings on them. The reviewer has 1, in run 2 only: on the SkipTo button it said focus moved into a menu that "is outside the page content". SkipTo is W3C's own working widget, so I count that as a model false flag.
- **What this does not show:** no outside page here has a focus-management failure, so this pass measures false flags only, not catches. The model-only catches in `eval/results.md` are still all on builder pages.
