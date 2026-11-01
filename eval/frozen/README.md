# Frozen eval runs

The 26 run directories behind `eval/results.md`, copied unchanged from the eval of 2026-10-30. Each holds:

- `walk.json`: the key sequence and, after every key, where the browser said focus was (role, accessible name, box). No screenshots: those stay out of the repository, and the W3C pages' screenshots are third-party content.
- `verdicts.json` and `verdicts.json.sha256`: the W0 and W1 verdicts, frozen and hashed before any model call.
- `review-eval1.json`, `review-eval2.json`: the focus reviewer's answers (builder focus-management pages only), with the prompt hash and per-call usage.
- `bare-eval1.json`, `bare-eval2.json`: bare Spark's answers to "list the keyboard and focus failures".
- `packets-eval1.json`, `packets-eval2.json`: the packets written from them.

`eval-report.json` is the report recorded on 2026-10-30. Recompute it with no API key and no model call:

```sh
uv run nomouse eval $(ls -d eval/frozen/w3c-*/ | sort) \
  $(ls -d eval/frozen/*/ | grep -v w3c- | sort) \
  --runs eval1 eval2 --own-row before-survey-06 --own-row after-survey-06 --out report.json
```

The counts and every row match `eval-report.json` (paths aside). A fresh `uv run nomouse walk builder/<page>.html` reproduces each builder page's `verdicts.json` hash.
