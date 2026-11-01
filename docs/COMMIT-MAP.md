# Commit hash map

Commit hashes cited in `README.md` and `eval/results.md` were recorded during the build, before the design and build-plan documents were removed from the history for publication. Removing them changed every later hash, and commits that only touched those documents are gone; for those, the right column names the kept commit with the same code. Commit dates and messages are unchanged.

| Cited | Public | Message |
|---|---|---|
| `0ceb649` | `0ceb649` | feat: add keyboard walk harness with CDP focus witness and W3C mirror |
| `b01dd03` | removed (docs only); same code as `0ceb649` | docs: add locked design and build plan for handoff |
| `b0fc09b` | `ed6dbd2` | fix: make walk records deterministic across runs and browser builds |
| `91d3b2a` | `1d8f834` | feat: record settle state, opened nodes and focus cover per key |
| `b9373aa` | `1180216` | feat: record keyboard targets and probe Escape after a trap |
| `3d4d9c6` | `04668d3` | fix: keep delegated click listeners from hiding unreachable targets |
| `fdc28ee` | `e3d0849` | feat: add the W0 walker with trap, unreachable and focus-visible rules |
| `bbf5632` | `ccefe0e` | feat: add the W1 walker and freeze verdicts with a SHA-256 hash |
| `b07c57e` | removed (docs only); same code as `ccefe0e` | docs: record T2 commits and line counts in the build plan |
| `7ba0b13` | removed (docs only); same code as `ccefe0e` | docs: record the Survey check outcome: W1 flags the em to n step |
| `a9641a0` | `a38e554` | feat: add a minimal Muse Spark client with per-call usage logging |
| `bdbb4b9` | `6b2438d` | feat: walk builder pages and add the failing menu-button page |
| `f463f4b` | `7b4f29d` | feat: add the G1 token-usage runner as `nomouse g1` |
| `b94d314` | removed (docs only); same code as `7b4f29d` | docs: record the G1 outcome: input flat, mean output 283 tokens |
| `627027a` | `35b76fa` | fix(spark): keep the key out of redirects and errors, reject bad replies |
| `12eb457` | `cbd953e` | fix(g1): check the window against the image cap and report API errors |
| `29ecff4` | removed (docs only); same code as `cbd953e` | docs: log the independent review of the G1 commits |
| `e85f60d` | `9f4ff60` | feat(builder): add the failing Tab order pages |
| `033767e` | `506703a` | feat(builder): add correct Tab order pages that are not top to bottom |
| `0741815` | `cae225e` | feat(builder): add failing focus-management pages |
| `ab8dbe8` | `c019b5a` | feat(builder): add correct focus-management pages |
| `e5606d9` | `9d052f0` | feat(builder): add trap pages and hashed ground-truth labels |
| `0f79519` | removed (docs only); same code as `9d052f0` | docs: record T6 builder pages, labels hash and W1 walk counts |
| `1860f3b` | `a787997` | fix(builder): make the filters dialog dismissible and the pricing order unambiguous |
| `5d363f0` | removed (docs only); same code as `a787997` | docs: record the builder label revision and v2 walks |
| `64dd50f` | `902570f` | feat(spark): ask for structured output with a strict JSON schema |
| `021f0d3` | `f1c3002` | feat(reviewer): add form (i) of the focus reviewer |
| `db1e172` | `6e2bed1` | feat(reviewer): add form (ii), Tab order against the visual layout |
| `6b1ae1e` | `ab03522` | feat(cli): add nomouse review with a --run label |
| `6a1744d` | `81e6607` | refactor(reviewer): bound form (i) requests by images alone |
| `9b3c81f` | removed (docs only); same code as `81e6607` | docs: record T7, the focus reviewer, and its token estimates |
| `b4d5ba5` | `1ec95c0` | fix(reviewer): keep paid calls in a partial log, record the prompt hash |
| `bc3c6b0` | removed (docs only); same code as `1ec95c0` | docs: record the T7 review, the live format check and the prompt hash |
| `bfc9325` | `a526214` | feat(scorer): score gate reviews against the hashed labels |
| `b126e76` | removed (docs only); same code as `a526214` | docs: record G3a (fail) and the focus-management gate (pass) |
| `6a79b74` | `0d48f57` | refactor(reviewer): drop the Tab-order form after G3a failed |
| `65c4f29` | `cb97d01` | feat(packets): write one packet per walker or reviewer finding |
| `4e88d5a` | `35b7c06` | feat(packets): write packet sentences with a Muse Spark text call |
| `57d32c9` | `ab32931` | feat(cli): add nomouse packets with a --template switch |
| `46b17f2` | removed (docs only); same code as `ab32931` | docs: record the shrink to form (i) and the packet writer (T9) |
| `efabd36` | removed (docs only); same code as `ab32931` | docs: pre-register the form (i) reporting bar before the eval |
| `f34320e` | `86319cd` | fix(packets): keep DOM ids out of the writer call and name unreachable targets |
| `d9b99a3` | `8357793` | fix(packets): reject sentences that state accessibility or standards status |
| `66bf32e` | `9c880a4` | feat(packets): word order packets as best practice for a person to review |
| `e803b47` | removed (docs only); same code as `9c880a4` | docs: record the T9 review, its fixes and the live writer check |
| `8b44aab` | `ab6ffbd` | docs(eval): hash the W3C keyboard and focus labels before any model run |
| `26d800b` | `5741328` | feat(bare): add the bare-Spark eval arm, one video call per frozen walk |
| `0f69e77` | `19b2f57` | feat(packets): keep one packets file per review run |
| `a3119dc` | `ec5c013` | feat(eval): score W0, W1, NoMouse and bare Spark against both label sets |
| `8430742` | `88cee41` | feat(cli): add nomouse eval with a Markdown counts table |
| `a370032` | removed (docs only); same code as `88cee41` | docs(eval): record the eval rules and arms before the live runs |
| `1fc3f8e` | `cb3fff1` | docs(eval): record the four-arm eval counts and the form (i) bar |
| `6e55ee4` | removed (docs only); same code as `cb3fff1` | docs: correct the T10 log time |
| `ad745c8` | `97d8f6f` | fix(eval): split names on Unicode letters and refuse a repeated run label |
| `b0baf19` | `321e2e1` | docs(eval): record the T10 audit, a corrected strict count and wording fixes |
| `354897f` | `d461617` | feat(export): build replay data from frozen runs |
| `4e77ae1` | `86cf5f2` | feat(cli): add nomouse export and nomouse serve-replay |
| `fde1bd3` | `fb26402` | feat(replay): add the page index and the eval counts page |
| `0d273a7` | `a816ac0` | feat(replay): add the per-page viewer with key-by-key stepping |
| `dd26959` | removed (docs only); same code as `a816ac0` | docs: record the T11a replay export and its smoke test |
| `2f766de` | `fa7f267` | feat(walkers): record the UTC freeze time in a sidecar beside the verdicts |
| `211322b` | `f51ac7d` | feat(replay): show recorded freeze times and audit-corrected counts |
| `f57a129` | `222e9bf` | feat(spark): ask the API not to store responses |
| `70f843c` | `f7c6e4d` | feat(credits): add the lifetime live credit ledger |
| `5bb885d` | `b324f9d` | feat(export): add one live run to an exported site |
| `001242f` | `f881233` | feat(live): add the live page allowlist and the live run pipeline |
| `a581291` | `c1f2d57` | feat(live): run one live run at a time under the lifetime credit cap |
| `f493634` | `23886bb` | feat(server): serve the live run API beside the replay |
| `9f5b41b` | `fb24833` | feat(cli): add nomouse serve-replay --live |
| `3c977a2` | `4ad8a60` | test(live): run the pipeline tests off the main thread |
| `1940781` | `efda878` | feat(replay): add the live run page, the privacy page and the AUP line |
| `b492cbd` | `f1fb613` | fix(live): say a run may cost up to the reserve when the cap is reached |
| `790be77` | `4a97bbf` | build: add a Dockerfile that prepares the replay and serves live mode |
| `4121992` | removed (docs only); same code as `4a97bbf` | docs: record T11b live mode, the real live run and the cap fallback |
| `1e904d7` | `210f4a1` | fix(server): time out stalled clients and stop listing directories |
| `ec1dcf4` | `72dc1d5` | fix(credits): refuse a NaN or negative cap and stop calls at the cap |
| `c145171` | `cec397e` | fix(live): read the cap after the previous run and bound stored runs |
| `2e6868d` | `10d5ef8` | docs: record the T11 security review and correct the privacy page |
| `041c908` | `84a33c5` | refactor(driver): record any key sequence through a key session |
| `17fa961` | `90668ca` | feat(navigator): let Muse Spark choose keys to carry out a typed task |
| `822082c` | `3b16f0e` | feat(cli): add nomouse navigate for typed keyboard tasks |
| `1341d99` | `bf41930` | feat(live): take an optional typed task for the keyboard navigator |
| `5c34958` | `cc25a07` | feat(replay): show navigator runs with their task and reasons |
| `b22a40f` | `a76792a` | refactor(export): title navigator runs in one place |
| `853c7ae` | `1b995e1` | docs: describe the keyboard navigator and export its reviews on the host |
| `54f74f8` | removed (docs only); same code as `1b995e1` | docs: record T12, the keyboard navigator and its live task runs |
| `354a0f1` | `2c4eeda` | fix(navigator): keep an incomplete record when a call fails mid-run |
| `32b6576` | `eb51007` | feat(cli): write navigator runs to runs/navigator by default |
| `b54890b` | `7022883` | fix(credits): count a cut-short ledger line instead of turning live mode off |
| `45f1e66` | `7c26a8f` | fix(credits): reserve each live call in the ledger before sending it |
| `afa3ca1` | `e415903` | fix(live): retry a failed poll with backoff instead of stopping |
| `e878088` | `a4512cf` | fix(guard): abort new pages and reloads instead of crashing the walk |
| `4a252c1` | removed (docs only); same code as `a4512cf` | docs: record the T12 review and the robustness fixes |
| `33284a0` | `9d501c7` | docs: rewrite the README for the shrunk product and its eval counts |
| `4c3f697` | removed (docs only); same code as `9d501c7` | docs: add the submission checklist and record T13 |
