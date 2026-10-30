# NoMouse eval results (T10)

Counts only. No W3C page, screenshot or annotation text is in this repository; W3C runs stay in the git-ignored `runs/`.

- Date: 2026-10-30. W3C walks 15:56-15:57 UTC (at 8b44aab); builder walks from T6 (13:53-14:04 UTC). Model runs 16:09-16:30 UTC with the code at a370032 (rules committed and pushed before the first call).
- Labels: `builder/labels.json` SHA-256 `90158dfd9de08d1f4c5ec0b44929e25c06ec218d0e8ea4beffc0e2fed23b1d61`; `eval/w3c-labels.json` SHA-256 `c6f38f15923d10b47e0861799cf1a4344742a0e32ec7ef4ba118f10b84d27266` (hashed 15:55:54 UTC, before any model call on a W3C page). Both re-checked by `nomouse eval`.
- Prompts: form (i) reviewer `59707175ebfe57396494168a43618ae0889a1adc6715949db5189c6b2626e686`; bare Spark `80f2ad8399c4fc28b6d71005b8a4098d55866c15fb9485d648ec57e6244aaad3`. Model `muse-spark-1.3`, reasoning effort `minimal`.
- Pages: 26 frozen walks: W3C Before and After home, tickets, survey (News excluded) and the 20 builder pages. 42 rows: 22 labelled barriers (11 builder, 11 W3C) and 20 clean controls (9 builder, 11 W3C After rows) on 12 clean pages.
- Arms, same frozen walk per page: W0; W1; NoMouse = W1 plus the form (i) reviewer (`--run eval1`, `--run eval2`); bare Spark = one video call per page with the prompt "list the keyboard and focus failures" (`--run eval1`, `--run eval2`). Order and trap rows are W0/W1 only by construction (the reviewer answers form (i) only).
- Bare-Spark input: an mp4 made from the frozen walk's own screenshots, one second per frame (video input is supported: https://dev.meta.ai/docs/video-understanding). Deviation from the design: not a second screen recording, so the key sequence is the frozen one by construction and animation between keys is not shown. The answer schema's type enum names ten failure categories, a hint the bare prompt alone does not give.
- Usage (eval arms, from `usage`): 90 calls, 355,988 input and 71,216 output tokens, about USD 0.75 (reviewer 2 x 19 calls, 95,834 input each; bare 2 x 26 calls, 82,160 input each). Packets (`nomouse packets --run eval1/eval2`, 52 run dirs): 40 writer calls, 21,571 input and 22,747 output tokens, about USD 0.12; 184 packets, 16 sentences rejected by the guard (template used). One unscored video probe: 2,628 input and 729 output. Total about USD 0.88, under the USD 30 cap.
- Technical errors: 2 bare eval2 calls returned HTTP 502 ("upstream request failed": order-address-rowwise, order-settings-columns, between 16:17 and 16:30 UTC); no answer was written; each was re-run once at 16:30 UTC and those answers are the ones scored. No other re-run.

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

- Reporting bar (pre-registered, L18: at least 3 model-only focus-management catches over W1, at most 1 model false flag on the correct focus-management controls; met only if met in both runs): **met**. eval1 and eval2 each: 3 model-only catches (dialog-fades-in, drawer-slides-in, reply-focus-search), 0 of 3 clean focus-management controls flagged by the reviewer. The two NoMouse runs agree on every row. These are the same three builder pages the focus-management gate used (T8), now reviewed twice more; no W3C page has a focus-management label, so no model-only catch is external.
- Survey (Note 06), on its own row: W1 VERIFIED (the `em` to `n` step), so NoMouse reports it VERIFIED in both runs; W0 misses it (no order rule); bare Spark: no match in eval1, loose in eval2 (a focus-order item not on the Name or eMail fields). On the repaired After survey, W1 and bare eval1 have a loose focus-order flag (not on the carrier).
- False flags: NoMouse adds no false-flag row over W1 (9 of 20 loose, 8 strict, all from W1: 4 correct-order pages, the disclosure control, and Template Note 08 on all three After pages, where W1's focus-not-visible fires on the After skip link, an `A`, which the pre-set carrier tag counts). The reviewer's only finding on a clean page was `returned: fail` on order-filters-dialog (an order page; shown in "clean pages with any finding", 9 vs 8 of 12).
- Bare Spark finds the most labelled barriers (15 of 22 strict in both runs) but flags 16 and 15 of 20 clean controls (loose; 8 and 9 strict) and lists something on 11 and 12 of 12 clean pages. On W3C it gives the same kinds of answer on Before and After pages (no skip link, quick menu not keyboard operable on all six pages), so its W3C catches come with W3C false flags on 11 and 10 of the 11 repaired rows; the no-skip-link rows match by type alone. Its two runs disagree on 15 rows.
- Reference point, not a like-for-like comparison: arXiv 2609.09379 reports 56% precision for its model. Here, counts only: bare Spark flagged 16 and 15 of 20 clean controls; NoMouse's reviewer flagged 0 of 3 clean focus-management controls.
- Rows a Tab, Enter and Escape walk cannot show (W3C Template Note 03 and Survey Note 05, `walk_exercised: false`) are counted like every other row.
