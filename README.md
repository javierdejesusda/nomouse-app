# NoMouse

A walker-baselined measurement of WCAG 2.4.3 Focus Order, narrowed to focus management: when a menu, dialog or panel opens, does keyboard focus land inside it, and does it come back to the trigger on close? Every model verdict sits beside a frozen deterministic baseline.

Status as of 2026-10-30. Built for the Meta Global AI Developer Hackathon 2026 (Indie track).

**What it is:** observed keyboard-only test evidence. A driver presses keys, the browser reports where focus is after every key, two deterministic walkers judge what the DOM can settle, and only then does Muse Spark 1.3 look at the screenshots.

**What it is not:** not a conformance claim, not legal advice, not an accessibility audit. NoMouse never says a page is "compliant" or "non-compliant". Every packet reads: "Observed keyboard-only test evidence. Not a conformance claim and not legal advice." The harness enforces key-only actions on an allowlisted origin.

## How it works

```
allowlisted page (W3C demo mirror without News, or a builder-made page)
   |
   v
[1]  Key driver (deterministic): Tab through; Enter then Escape on each button; links never activated
     or, with a typed task only:
[1b] Keyboard navigator (Muse Spark 1.3): one key per turn from an 11-key whitelist, or done/stuck;
     sees the task and the last 5 screenshots, never the focus box; 40-key cap; types no text
   |  after every key: screenshot (1024x768) + CDP focus witness (box, role, accessible name)
   v
[2]  Walkers W0 and W1 --> verdicts frozen to verdicts.json and hashed (SHA-256) before any review call
   |
   v
[3]  Focus reviewer, form (i) (Muse Spark 1.3, before/after screenshots + focus boxes as text):
     after each Enter, did focus land inside what visibly opened? after Escape, did it return?
   |
   v
[4]  Packet writer (Muse Spark 1.3 text, template fallback): one sentence per finding + the fixed disclaimer
   |
   v
[5]  Scorer (deterministic, eval only): labels hashed before the first run
```

- **W0:** Tab walk, keyboard trap, unreachable target, focus not visible (pixel diff inside the focus box).
- **W1:** W0 plus a geometric order check (thresholds frozen before the eval: 24 px order, 8 px focus-ring margin), a cover check, and a focus-management check (after Enter, is `document.activeElement` inside the nodes that became visible; after Escape, is focus back on the trigger).
- **Statuses:** VERIFIED when a walker rule fired; NEEDS REVIEW when only the model saw it. Order findings come from W1 only and are always labelled "best practice": W3C says focus order need not follow the visual layout.
- **Shrink after the gates (2026-10-30):** the design also asked the model whether the Tab order follows the visual reading order (form ii). Gate G3a failed (it flagged 0 of 3 row-wise order failures W1 passes, and 2 of 5 correct pages), the focus-management gate passed (3 of 3 failures W1 misses, 0 of 3 correct controls flagged), and the product was shrunk to form (i). The model no longer judges Tab order.
- **Replay and live mode:** `nomouse export` builds a static replay of every frozen run (no key needed): the unmodified capture on the left, the focus box and Tab path in a separate panel on the right, walker and reviewer findings, packets, and the counts page. `nomouse serve-replay --live` adds live runs on a fixed page list: one run at a time, at most 40 keys, a lifetime credit cap of at most USD 20 read from logged usage, falling back to the replay when the cap is reached or no key is set.

## Meta models used

Muse Spark 1.3 only (`muse-spark-1.3`, Standard mode), on the Meta Model API Responses endpoint, reasoning effort `minimal` (the lowest level; `none` is refused), `store: false` on every request, structured output with strict JSON schemas. It is used for the navigator [1b], the focus reviewer [3], the packet writer [4], and the bare-Spark eval arm (one video call per page). No other Meta model is used.

## Eval results (counts, not rates)

From `eval/results.md`, runs of 2026-10-30 16:09-16:30 UTC with the code at a370032. Builder labels `builder/labels.json` SHA-256 `90158dfd9de08d1f4c5ec0b44929e25c06ec218d0e8ea4beffc0e2fed23b1d61`; W3C labels `eval/w3c-labels.json` SHA-256 `c6f38f15923d10b47e0861799cf1a4344742a0e32ec7ef4ba118f10b84d27266`, both hashed before any model call. Survey Before walker verdicts SHA-256 `60fab3b9ad53c533f3153c5f7564515231379df52fef5f3254780eed4263fee4` (every page's verdicts hash is in `eval/results.md` and on its replay page). Model arms ran twice.

- Pages: 26 frozen walks: W3C Before and After home, tickets and survey (News excluded), and 20 builder-made pages (8 focus management, 10 order, 2 trap). 22 labelled barriers (11 builder, 11 W3C) and 20 clean controls.
- Arms on the same frozen walk: W0; W1; NoMouse (W1 plus the form (i) reviewer); bare Spark (one video call per page with the prompt "list the keyboard and focus failures"; the video is made from the frozen walk's screenshots, one per second).

| Arm | Barriers found (strict, of 22) | VERIFIED | NEEDS REVIEW | False flags on clean controls (loose, of 20) | False flags (strict) | False flags (strict), audit-corrected |
|---|---|---|---|---|---|---|
| W0 | 4 | 4 | 0 | 3 | 3 | 0 |
| W1 | 9 | 9 | 0 | 9 | 8 | 5 |
| NoMouse eval1 | 12 | 9 | 3 | 9 | 8 | 5 |
| NoMouse eval2 | 12 | 9 | 3 | 9 | 8 | 5 |
| bare eval1 | 15 | 0 | 15 | 16 | 8 | 8 (no correction) |
| bare eval2 | 15 | 0 | 15 | 15 | 9 | 9 (no correction) |

- **Model-only catches over W1:** 3 in each run (dialog-fades-in, drawer-slides-in, reply-focus-search). These are builder pages, written and labelled by the builder, and the same three pages the focus-management gate used. None is on a W3C page; no W3C page has a focus-management label.
- **Clean controls:** NoMouse adds no false flag over W1. Most ground truth is builder pages, so this is the column to read: a model that flags everything fails here (bare Spark flags 16 and 15 of 20).
- **W3C Survey, Note 06** (the Name and eMail Tab order): W1 VERIFIED, so NoMouse reports it VERIFIED in both runs; W0 misses it; bare Spark no match (eval1) and a loose match elsewhere on the page (eval2).
- **Audit-corrected column:** an independent recount after the runs matched every number. It found that the carrier tag for W3C Template Note 08 also took the After pages' second skip link, a real walker false positive; requiring a carrier name gives the corrected strict counts. The recorded counts are unchanged and the rule worked against the walker arms, not for NoMouse.
- **Reference point only:** arXiv 2609.09379 reports 56% precision for its model. This eval computes no precision and the populations differ, so nothing here is compared with it.
- **Cost:** about USD 0.88 for the whole eval (90 eval calls plus 40 packet-writer calls).

**Navigator runs** (2026-10-30, live, CLI): 4 typed tasks. "open the Edit address dialog and close it" (builder) done correctly in 6 keys; "show the answer to the first delivery question" (builder) done in 4; "use the Sort menu to sort the photos by name" (builder, not achievable: there is no such item) ended stuck, correctly; "choose two tickets and continue" on W3C Tickets Before **failed**: no ticket chosen, stuck at the 40-key cap. USD 0.43 in all.

## Prior art

NoMouse is not the first keyboard or focus-order checker. What is new is narrow: a public, frozen walker baseline beside every model verdict, with clean controls, on focus management after open and close.

- arXiv 2609.09379 (https://arxiv.org/html/2609.09379): one vision-language agent per WCAG criterion, including a Focus Order worker; the closest work.
- Deque axe DevTools keyboard IGT (https://docs.deque.com/devtools-for-web/4/en/devtools-igt-keyboard): auto-tabber, trap detection, AI evaluation of tab stops.
- Evinced (https://www.evinced.com/blog/automatic-detection-of-invalid-focus-indication/): computer vision for focus indication; a tab-order validation is also reported (not verified by us).
- BrowserStack Spectra, rule `meaningful-sequence-focus-order` (mechanism not verified by us).
- Lost in Navigation, ICST 2026 (https://conf.researchr.org/details/icst-2026/icst-2026-research/40/Lost-in-Navigation-Detecting-Keyboard-Navigation-Accessibility-Issues-in-Web-Pages) and A11yLTLNav (https://arxiv.org/abs/2609.17959): research detectors for keyboard navigation failures.
- Accessibility Insights for Web Tab stops, Firefox "Show Tabbing Order", ARC Toolkit's tab order path: the free manual checks NoMouse competes with.
- a11y-toolkit (https://glama.ai/mcp/servers/hfk8vfwmd9), AudioEye rule `Element_FocusOrder_MisMatch`.
- In-event: Access Walk (https://github.com/adrianotrenahi/meta-accessibility-auditor), as of 2026-10-30: Muse Spark driver, analyst and reviewer connections plus SAM 3.1 on keyboard and screen-reader task barriers; fifteen journey criteria, including C06 "Is focus visible at the tested keyboard stops?"; its README says "Full Tab order, visible focus and native screen-reader operation remain unestablished". NoMouse targets 2.4.3 focus management only.

## Setup

Python 3.12 via [uv](https://docs.astral.sh/uv/), Playwright Chromium.

```sh
uv sync
uv run playwright install chromium
uv run nomouse mirror                      # fetch the W3C demo from its official zip into .cache/ (News excluded)
```

Model calls need `META_API_KEY` in the environment. It is never printed, logged or written to disk.

## Commands

```sh
uv run nomouse walk before/survey.html --out runs/eval/w3c-before-survey     # no model call
uv run nomouse walk builder/dialog-fades-in.html --out runs/builder/dialog-fades-in
uv run nomouse review runs/builder/dialog-fades-in --run eval1               # form (i) reviewer
uv run nomouse packets runs/builder/dialog-fades-in --run eval1              # add --template for no model call
uv run nomouse navigate builder/dialog-focus-returns.html --task "open the Edit address dialog and close it"
uv run nomouse eval <run dirs> --runs eval1 eval2                            # scoring, no model call
uv run nomouse export site --runs eval1 eval2 live --audit eval/audit-corrections.json
uv run nomouse serve-replay site --port 8000                                 # add --live for live runs
```

`nomouse walk` alone needs no key and spends nothing: it walks, runs W0 and W1, and writes `verdicts.json` with its SHA-256. That is the mode for CI. No CI integration is packaged yet.

Checks: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`.

## Run with Docker

```sh
docker build -t nomouse .
docker run --rm -p 8000:8000 -e META_API_KEY -v nomouse-data:/data nomouse
```

Then open http://localhost:8000/. On first start the container downloads the W3C demo from its official zip (never baked into the image or committed), walks the 6 W3C pages and the 20 builder pages with no model call, exports the replay, and serves it with live mode on `$PORT` (default 8000).

- `META_API_KEY` is read from the environment only. Without it, live mode is off and says so; the replay still works.
- Keep the `/data` volume: it holds the live credit ledger (`/data/state/live-usage.jsonl`). A fresh volume starts the lifetime cap from zero.
- Behind a reverse proxy, add `-e NOMOUSE_TRUST_PROXY=1` so each client is rate-limited by the proxy's `X-Forwarded-For` address (3 live runs per address per hour). Do not set it without a proxy.
- To show recorded runs (reviews, packets and the counts page), mount a runs directory read-only: `-e NOMOUSE_RUNS=/runs -v "$PWD/runs:/runs:ro"`.

## Deploy on Fly.io

`fly.toml` runs the same image on one always-on machine (2 GB, for Chromium) with a `/data` volume, and sets `NOMOUSE_TRUST_PROXY=1`: Fly's edge proxy appends the client address to `X-Forwarded-For` and nothing else sits in front, so the last entry is the client. On any host without a proxy, leave it unset.

```sh
fly launch --no-deploy --copy-config      # pick a free app name; keep fly.toml
fly volumes create nomouse_data --size 1 --region cdg
fly secrets set META_API_KEY=...          # never in fly.toml or the image
tar czf runs.tgz runs/builder runs/eval runs/navigator
fly deploy
fly ssh sftp shell                        # put runs.tgz /data/runs.tgz
fly ssh console -C "sh -c 'mkdir -p /data/runs && tar xzf /data/runs.tgz -C /data && rm /data/runs.tgz'"
fly machine restart
```

The upload carries the frozen walks, reviews, packets and bare-Spark results into `/data/runs`, so the hosted replay shows the recorded eval (the start script skips any page that already has `verdicts.json`). Without it the host re-walks every page and the replay has no model findings. To carry over the local live spend instead of starting the USD 20 cap from zero, also put `live-state/live-usage.jsonl` at `/data/state/live-usage.jsonl`.

Live runs added after a start are listed by link only and are not kept in the replay across restarts; their run directories stay in `/data/state/runs`.

## Privacy and acceptable use

A live run sends screenshots of an allowlisted page, focus boxes, roles and names, and fixed prompts to the Meta Model API (Standard tier, `store: false`). The replay's Privacy page (`src/nomouse/replay/privacy.html`) says what is sent and kept. Use of the live page must comply with the Meta Model API Acceptable Use Policy: https://dev.meta.ai/legal/acceptable-use-policy.

## Licensing

- Code and builder pages (`builder/`): MIT, see `LICENSE`. Every builder page says "builder-made" on screen.
- W3C Before and After Demonstration (https://www.w3.org/WAI/demos/bad/): licensed third-party input, fetched from https://www.w3.org/WAI/demos/bad/bad.zip at setup, never committed, shown unmodified with its URL and the notice "Copyright © 2012 W3C (MIT, ERCIM, Keio), All Rights Reserved". The News pages are excluded everywhere. The W3C pages are not claimed as hackathon work.

## Built during the Hackathon

All code in this repository is new work written from Oct 30 to Nov 4 2026, inside the event's build period. No prior code is reused. Contributors: javierdejesusda (solo).

## Pre-user evidence

No outside user has tried NoMouse. The eval ground truth is mostly builder pages written and labelled by the builder; the only external 2.4.3 instance (W3C Survey Note 06) is caught by the walker, not the model.
