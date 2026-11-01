# Eval rules, fixed before the first model call

These rules were written in the private build log before any eval model call and are copied here for readers of the public repository, with build-log labels replaced by plain words. The original log entries are dated 2026-10-30 15:40 UTC (reporting bar) and 16:09:25 UTC (rules and arms); the first eval model call was at 16:09 UTC, after the code below was committed and pushed. The private log can be shown to judges on request.

The code the rules depend on is in this repository's history, committed before the first eval call:

| Commit | Time (UTC, 2026-10-30) | What |
|---|---|---|
| `ab6ffbd` | 15:56:11 | W3C keyboard and focus labels hashed (`eval/w3c-labels.json.sha256`) |
| `5741328` | 16:01:56 | bare-Spark arm, one video call per frozen walk |
| `19b2f57` | 16:02:24 | one packets file per review run |
| `ec5c013` | 16:08:54 | scoring of W0, W1, NoMouse and bare Spark against both label sets |
| `88cee41` | 16:08:55 | `nomouse eval` with the Markdown counts table (the code of the eval runs) |

No rule, label or prompt changed after the results. A technical-error re-run would be recorded in `eval/results.md`; no run was repeated for a better number.

## Reporting bar (fixed 15:40 UTC)

For the shrunk product (focus reviewer form (i) only, the Tab-order question removed after its gate failed):

> At least 3 model-only focus-management catches over W1 across the eval pages, with at most 1 model false flag on the correct focus-management controls; the order rows report W1 only.

"Met" only if met in both model runs (eval1 and eval2); otherwise reported per run. A false flag here is a correct focus-management control (3 builder pages) with a form (i) finding of its types.

Order findings: W1 order findings stay VERIFIED (a walker rule fired) but are always labelled "best practice", and their sentence must say that the Tab order differs from the visual layout and that W3C allows other meaningful orders, so a person should review it. A model sentence without that caveat falls back to the template.

## Pages and walks (26)

- The 6 W3C walks, Before and After of Home, Tickets and Survey (News excluded), walked at 15:56-15:57 UTC with verdicts frozen by the walk. Survey Before gave the same verdicts hash as its first walk (`60fab3b9...fee4`).
- The 20 builder walks (the `-v2` walks for order-filters-dialog and order-pricing-rowwise).
- Every page's verdicts hash is in `eval/results.md` and in `eval/frozen/`.

## Arms, on the same frozen walk per page

- **W0 and W1:** from the frozen verdicts.
- **NoMouse:** W1 plus `nomouse review <dir> --run eval1` and `--run eval2` (form (i); prompt hash `59707175...e686`).
- **Bare Spark:** `nomouse bare <dir> --run eval1` and `--run eval2`, on all 26 pages twice.
- **Bare Spark input:** an mp4 made by ffmpeg from the frozen walk's own screenshots (initial, then one per key), one second per frame, so the key sequence is the frozen one by construction; animation between keys is not shown. Both runs send the same bytes. Message: "list the keyboard and focus failures", then one format line. Strict schema: `failures[]` of `element`, `type` (the ten label types, a hint the bare prompt alone does not give), `second`, `description`. Prompt hash `80f2ad8399c4fc28b6d71005b8a4098d55866c15fb9485d648ec57e6244aaad3`. A reply that does not fit the schema counts as a technical error.

## Rows and matching

See the docstring of `src/nomouse/evaluation.py`.

- Each builder label is a row typed by its kind: order is focus-order; trap is keyboard-trap; a focus-management failure is focus-not-moved; a focus-management clean control is focus-not-moved or focus-not-returned. Carrier names are the accessible names the walk recorded on the carrier ids. W3C rows as written.
- Walker rules map to types; form (i) `inside: fail` is focus-not-moved and `returned: fail` is focus-not-returned on the trigger.
- **Loose:** a finding of one of the row's types on its page.
- **Strict:** loose and on the carrier: DOM id in the carrier ids or tag in the carrier tags (walker, reviewer), or element text matching a carrier name (bare Spark: case-folded words, role words such as button, link, field dropped, one word sequence inside the other). Rows without carriers of that kind match by type alone.

## Counts

- Barriers found = failure rows matched strictly (loose beside).
- NoMouse rows are VERIFIED when W1 matches strictly, NEEDS REVIEW when only the review does; bare Spark is always NEEDS REVIEW.
- False flags = clean rows matched loosely (strict beside).
- Model-only catches = failure rows NoMouse marks NEEDS REVIEW.
- Disagreements = rows whose strict or loose result differs between eval1 and eval2.
- Budget: stop and report if total eval spend would pass USD 30 (actual: about USD 0.88).
