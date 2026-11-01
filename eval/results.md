# NoMouse eval results

Counts only. No W3C page, screenshot or annotation text is in this repository; W3C runs stay in the git-ignored `runs/`.

- Date: 2026-10-30. W3C walks 15:56-15:57 UTC (at `ab6ffbd`); builder walks 13:53-14:04 UTC. Model runs 16:09-16:30 UTC with the code at `88cee41` (rules in `eval/RULES.md`, code committed and pushed before the first call).
- Labels: `builder/labels.json` SHA-256 `90158dfd9de08d1f4c5ec0b44929e25c06ec218d0e8ea4beffc0e2fed23b1d61`; `eval/w3c-labels.json` SHA-256 `c6f38f15923d10b47e0861799cf1a4344742a0e32ec7ef4ba118f10b84d27266` (hashed 15:55:54 UTC, before any model call on a W3C page). Both re-checked by `nomouse eval`.
- Prompts: form (i) reviewer `59707175ebfe57396494168a43618ae0889a1adc6715949db5189c6b2626e686`; bare Spark `80f2ad8399c4fc28b6d71005b8a4098d55866c15fb9485d648ec57e6244aaad3`. Model `muse-spark-1.3`, reasoning effort `minimal`.
- Pages: 26 frozen walks: W3C Before and After home, tickets, survey (News excluded) and the 20 builder pages. 42 rows: 22 labelled barriers (11 builder, 11 W3C) and 20 clean controls (9 builder, 11 W3C After rows) on 12 clean pages.
- Arms, same frozen walk per page: W0; W1; NoMouse = W1 plus the form (i) reviewer (`--run eval1`, `--run eval2`); bare Spark = one video call per page with the prompt "list the keyboard and focus failures" (`--run eval1`, `--run eval2`). Order and trap rows are W0/W1 only by construction (the reviewer answers form (i) only).
- Bare-Spark input: an mp4 made from the frozen walk's own screenshots, one second per frame (video input is supported: https://dev.meta.ai/docs/video-understanding). Deviation from the design: not a second screen recording, so the key sequence is the frozen one by construction and animation between keys is not shown. The answer schema's type enum names ten failure categories, a hint the bare prompt alone does not give.
- Usage (eval arms, from `usage`): 90 calls, 355,988 input and 71,216 output tokens, about USD 0.75 (reviewer 2 x 19 calls, 95,834 input each; bare 2 x 26 calls, 82,160 input each). Packets (`nomouse packets --run eval1/eval2`, 52 run dirs): 40 writer calls, 21,571 input and 22,747 output tokens, about USD 0.12; 184 packets, 16 sentences rejected by the guard (template used). One unscored video probe: 2,628 input and 729 output. Total about USD 0.88, under the USD 30 cap.
- Run times (from the run log and file times): reviewer eval1 16:09:34-16:10:40, eval2 16:10:45-16:11:52; bare eval1 16:11:58-16:16:46, eval2 16:24:42-16:29:59 (nothing ran in between), the two re-runs 16:30:16-16:30:37 UTC. Rules, labels and code were pushed at 16:09:27, before the first call.
- Technical errors: 2 bare eval2 calls returned HTTP 502 ("upstream request failed": order-address-rowwise, order-settings-columns, in the eval2 bare pass, 16:24-16:30 UTC); no answer was written; each was re-run once at 16:30 UTC and those answers are the ones scored. No other re-run.
- Reproduce (no model call): `nomouse eval` with the 26 run dirs in this order (row order follows it): `runs/eval/w3c-*` sorted, then `runs/builder/*` sorted without the superseded `order-filters-dialog` and `order-pricing-rowwise` (their `-v2` dirs are used), `--runs eval1 eval2 --own-row before-survey-06 --own-row after-survey-06`. Re-run on 2026-10-30 by the audit below: the same tables byte for byte and the same `runs/eval/eval-report.json`.

## Scoring (fixed before the runs, `src/nomouse/evaluation.py`)

Strict: a finding of one of the row's failure types on the row's carrier (walker and reviewer: DOM id or tag; bare Spark: its element text matches a carrier's visible name); rows without carriers of that kind match by type alone. Loose: a finding of one of the row's types anywhere on the page. Found = failure rows matched strictly; false flags = clean rows matched loosely (strict beside). NoMouse rows are VERIFIED when W1 matches, NEEDS REVIEW when only the reviewer does; bare Spark is NEEDS REVIEW.

## Counts

| Arm | Barriers found (strict) | VERIFIED | NEEDS REVIEW | Found (loose) | False flags on clean controls (loose) | False flags (strict) | Clean pages with any finding |
|---|---|---|---|---|---|---|---|
| W0 | 4 of 22 | 4 | 0 | 4 | 3 of 20 | 3 | 3 of 12 |
| W1 | 9 of 22 | 9 | 0 | 9 | 9 of 20 | 8 | 8 of 12 |
| NoMouse eval1 | 12 of 22 | 9 | 3 | 12 | 9 of 20 | 8 | 9 of 12 |
| NoMouse eval2 | 12 of 22 | 9 | 3 | 12 | 9 of 20 | 8 | 9 of 12 |
| bare eval1 | 15 of 22 | 0 | 15 | 18 | 16 of 20 | 8 | 11 of 12 |
| bare eval2 | 15 of 22 | 0 | 15 | 20 | 15 of 20 | 9 | 12 of 12 |

| Arm | Builder found | W3C found | Builder false flags | W3C false flags |
|---|---|---|---|---|
| W0 | 1 of 11 | 3 of 11 | 0 of 9 | 3 of 11 |
| W1 | 5 of 11 | 4 of 11 | 5 of 9 | 4 of 11 |
| NoMouse eval1 | 8 of 11 | 4 of 11 | 5 of 9 | 4 of 11 |
| NoMouse eval2 | 8 of 11 | 4 of 11 | 5 of 9 | 4 of 11 |
| bare eval1 | 7 of 11 | 8 of 11 | 5 of 9 | 11 of 11 |
| bare eval2 | 9 of 11 | 6 of 11 | 5 of 9 | 10 of 11 |

Reporting bar (form (i)): eval1 3 model-only, 0 control flags (met); eval2 3 model-only, 0 control flags (met). Overall: met.
Model-only catches over W1, eval1: dialog-fades-in, drawer-slides-in, reply-focus-search.
Model-only catches over W1, eval2: dialog-fades-in, drawer-slides-in, reply-focus-search.
Disagreements between runs, NoMouse: none.
Disagreements between runs, bare: after-survey-06, before-home-template-08, before-survey-template-08, before-survey-06, before-tickets-template-08, drawer-slides-in, order-footer-rowwise, order-glossary-columns, order-login-reversed, order-pricing-rowwise, order-rtl-nav, order-settings-columns, order-toolbar-reversed, trap-card-fields, trap-editor-escape.

### before-survey-06

| Arm | Result |
|---|---|
| W0 | - |
| W1 | VERIFIED |
| NoMouse eval1 | VERIFIED |
| NoMouse eval2 | VERIFIED |
| bare eval1 | - |
| bare eval2 | loose |

### after-survey-06

| Arm | Result |
|---|---|
| W0 | - |
| W1 | loose |
| NoMouse eval1 | loose |
| NoMouse eval2 | loose |
| bare eval1 | loose |
| bare eval2 | - |

| Row | Set | Status | W0 | W1 | NoMouse eval1 | NoMouse eval2 | bare eval1 | bare eval2 |
|---|---|---|---|---|---|---|---|---|
| after-home-template-08 | w3c | clean | VERIFIED | VERIFIED | VERIFIED | VERIFIED | loose | loose |
| after-home-template-05 | w3c | clean | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| after-home-template-03 | w3c | clean | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| after-survey-template-08 | w3c | clean | VERIFIED | VERIFIED | VERIFIED | VERIFIED | loose | loose |
| after-survey-template-05 | w3c | clean | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| after-survey-template-03 | w3c | clean | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| after-survey-06 | w3c | clean | - | loose | loose | loose | loose | - |
| after-survey-05 | w3c | clean | - | - | - | - | loose | loose |
| after-tickets-template-08 | w3c | clean | VERIFIED | VERIFIED | VERIFIED | VERIFIED | NEEDS REVIEW | NEEDS REVIEW |
| after-tickets-template-05 | w3c | clean | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| after-tickets-template-03 | w3c | clean | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| before-home-template-08 | w3c | failure | VERIFIED | VERIFIED | VERIFIED | VERIFIED | NEEDS REVIEW | loose |
| before-home-template-05 | w3c | failure | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| before-home-template-03 | w3c | failure | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| before-survey-template-08 | w3c | failure | VERIFIED | VERIFIED | VERIFIED | VERIFIED | loose | - |
| before-survey-template-05 | w3c | failure | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| before-survey-template-03 | w3c | failure | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| before-survey-06 | w3c | failure | - | VERIFIED | VERIFIED | VERIFIED | - | loose |
| before-survey-05 | w3c | failure | - | - | - | - | loose | loose |
| before-tickets-template-08 | w3c | failure | VERIFIED | VERIFIED | VERIFIED | VERIFIED | NEEDS REVIEW | loose |
| before-tickets-template-05 | w3c | failure | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| before-tickets-template-03 | w3c | failure | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| dialog-fades-in | builder | failure | - | - | NEEDS REVIEW | NEEDS REVIEW | NEEDS REVIEW | NEEDS REVIEW |
| dialog-focus-returns | builder | clean | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| dialog-focus-stays | builder | failure | - | VERIFIED | VERIFIED | VERIFIED | NEEDS REVIEW | NEEDS REVIEW |
| disclosure-stays-on-toggle | builder | clean | - | VERIFIED | VERIFIED | VERIFIED | - | - |
| drawer-slides-in | builder | failure | - | - | NEEDS REVIEW | NEEDS REVIEW | NEEDS REVIEW | loose |
| menu-focus-moves | builder | clean | - | - | - | - | - | - |
| menu-focus-stays | builder | failure | - | VERIFIED | VERIFIED | VERIFIED | NEEDS REVIEW | NEEDS REVIEW |
| order-address-rowwise | builder | failure | - | - | - | - | NEEDS REVIEW | NEEDS REVIEW |
| order-filters-dialog | builder | clean | - | - | - | - | loose | loose |
| order-footer-rowwise | builder | failure | - | - | - | - | - | NEEDS REVIEW |
| order-glossary-columns | builder | clean | - | VERIFIED | VERIFIED | VERIFIED | loose | - |
| order-login-reversed | builder | failure | - | VERIFIED | VERIFIED | VERIFIED | NEEDS REVIEW | - |
| order-pricing-rowwise | builder | failure | - | - | - | - | - | NEEDS REVIEW |
| order-rtl-nav | builder | clean | - | VERIFIED | VERIFIED | VERIFIED | loose | - |
| order-settings-columns | builder | clean | - | VERIFIED | VERIFIED | VERIFIED | - | loose |
| order-sidebar-after-main | builder | clean | - | VERIFIED | VERIFIED | VERIFIED | loose | loose |
| order-toolbar-reversed | builder | failure | - | VERIFIED | VERIFIED | VERIFIED | loose | NEEDS REVIEW |
| reply-focus-search | builder | failure | - | - | NEEDS REVIEW | NEEDS REVIEW | NEEDS REVIEW | NEEDS REVIEW |
| trap-card-fields | builder | failure | VERIFIED | VERIFIED | VERIFIED | VERIFIED | - | NEEDS REVIEW |
| trap-editor-escape | builder | clean | - | - | - | - | - | NEEDS REVIEW |

Cell key: VERIFIED or NEEDS REVIEW = strict match; loose = type match on the page, not on the carrier; - = no match.

## Reading the counts

- Reporting bar (pre-registered at 15:40 UTC, see `eval/RULES.md`: at least 3 model-only focus-management catches over W1, at most 1 model false flag on the correct focus-management controls; met only if met in both runs): **met**. eval1 and eval2 each: 3 model-only catches (dialog-fades-in, drawer-slides-in, reply-focus-search), 0 of 3 clean focus-management controls flagged by the reviewer. The two NoMouse runs agree on every row. These are the same three builder pages (builder-made and labelled by the builder) that the focus-management gate used, now reviewed twice more with the same form (i) prompt text and schema (unchanged since `f1c3002`, before the gate); no W3C page has a focus-management label, so none of the three catches is on a W3C page and none is external.
- Survey (Note 06), on its own row: W1 VERIFIED (the `em` to `n` step), so NoMouse reports it VERIFIED in both runs; W0 misses it (no order rule); bare Spark: no match in eval1, loose in eval2 (a focus-order item not on the Name or eMail fields). On the repaired After survey, W1 and bare eval1 have a loose focus-order flag (not on the carrier).
- False flags: NoMouse adds no false-flag row over W1 (9 of 20 loose, 8 strict, all from W1: 4 correct-order pages, the disclosure control, and Template Note 08 on all three After pages). The Note 08 flags are a real walker false positive, but not on the menu links Note 08 is about: W0's and W1's focus-not-visible fires on the After pages' second skip link (to the navigation), which is drawn in the same box as the first skip link, so no pixel changes around it when focus moves there, although the link is plainly shown. They count as loose false flags by the rule (a finding of the row's type on the page) and as strict ones only because the carrier tag `A` set before the runs takes any link. The reviewer's only finding on a clean page was `returned: fail` on order-filters-dialog (an order page; shown in "clean pages with any finding", 9 vs 8 of 12).
- Bare Spark finds the most labelled barriers (15 of 22 strict in both runs) but flags 16 and 15 of 20 clean controls (loose; 8 and 9 strict) and lists something on 11 and 12 of 12 clean pages. On W3C it gives the same kinds of answer on Before and After pages (no skip link, quick menu not keyboard operable on all six pages), so its W3C catches come with W3C false flags on 11 and 10 of the 11 repaired rows; the no-skip-link rows match by type alone. Its two runs disagree on 15 rows.
- Reference point only: arXiv 2609.09379 reports 56% precision for its model. This eval computes no precision and the populations differ, so the figure is not compared with anything here. The counts to read beside it are the false-flag column above (clean controls flagged, loose, of 20): W0 3, W1 9, NoMouse 9 and 9, bare Spark 16 and 15.
- Rows a Tab, Enter and Escape walk cannot show (W3C Template Note 03 and Survey Note 05, `walk_exercised: false`) are counted like every other row.

## Audit (2026-10-30, after the runs; recorded counts unchanged)

- An independent script (not importing `src/nomouse/evaluation.py`) re-read the 26 frozen verdicts and walks, the 104 review and bare answers and both label files, and recomputed every count, cell, model-only catch, disagreement and usage total above: no mismatch. Label hashes match their `.sha256` files and the hashes recorded here; every answer names its walk's verdicts hash, the prompt hashes above, `muse-spark-1.3` and `minimal`; both bare runs sent the same `walk.mp4` per page (hash and frame count checked).
- Order checked: labels hashed 15:55:54 and committed 15:56:11, W3C walks 15:56:31-15:57:21, scoring code and rules pushed 16:09:27, first eval call 16:09:34 UTC.
- Corrected count, beside the recorded one (rule not changed retroactively): the carrier tag `A` on the three Template Note 08 rows was meant for the unreachable menu links, which the walker records with no id and no name. Applied to a named link that is none of the row's carrier names (the After skip link above), it makes the finding strict. Requiring a named walker finding to match a carrier name, as bare Spark's must, gives false flags (strict) W0 0 (recorded 3), W1 5 (8), NoMouse 5 and 5 (8 and 8); bare Spark, every found count, every loose count and the reporting bar are unchanged. The rule worked against the walker arms (and NoMouse with them), not for them.
- Rules that help an arm, already disclosed and not changed: the bare-Spark schema's type enum names the ten failure types; a row with no carrier names (Template Note 05) is matched by type alone for bare Spark, while the walkers have no skip-link rule; carrier names are matched loosely (whole words, either way round).
- Code fix with no effect on these runs (`97d8f6f`): visible names were split on ASCII letters and digits only, so the Hebrew carrier names of order-rtl-nav could never match, against the documented rule (letters and digits). Re-scored with the fix, the report is identical (bare Spark named no order-rtl-nav carrier).
