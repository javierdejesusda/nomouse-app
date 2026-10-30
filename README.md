# NoMouse

Keyboard-only focus walks of web pages: a driver presses Tab, Enter and Escape, records where the browser puts focus after every key, freezes deterministic walker verdicts with a hash, and only then asks a model (Muse Spark 1.3 on the Meta Model API) to review what opened. The harness enforces key-only actions on an allowlisted origin: a walk cannot leave the page's own origin or send any request other than GET.

The replay of every frozen run needs no API key. Live mode walks only pages from a fixed list (the W3C Before and After Demonstration pages without News, and the builder-made pages), one run at a time, at most 40 keys, under a lifetime credit cap of at most USD 20. See the site's Privacy page for what a live run sends to the Meta Model API.

With a typed task (live mode, or `nomouse navigate <page> --task "open the settings dialog and close it"`, written to `runs/navigator/<UTC>` so the replay export picks it up), a keyboard navigator chooses the keys instead of the driver: each turn Muse Spark sees the task, the keys pressed so far and the last 5 screenshots (never the browser's focus box, role or name) and answers one key from Tab, Shift+Tab, Enter, Space, Escape, the arrow keys, Home and End, or done or stuck. It types no text, so a task that needs a field filled ends stuck or at the 40-key cap. The task is capped at 200 characters and cannot change the keys, the cap or the page; a link to another document is aborted by the guard and the page stays. The run is then frozen, reviewed and replayed like a driver walk.

## Run with Docker

```sh
docker build -t nomouse .
docker run --rm -p 8000:8000 -e META_API_KEY -v nomouse-data:/data nomouse
```

Then open http://localhost:8000/. On first start the container downloads the W3C demo from its official zip (it is never baked into the image or committed), walks the 6 W3C pages and the 20 builder pages with no model call, exports the replay, and serves it with live mode on `$PORT` (default 8000).

- `META_API_KEY` is read from the environment only (`-e META_API_KEY` passes your shell's value without writing it anywhere). Without it, live mode is off and says so; the replay still works.
- Keep the `/data` volume: it holds the live credit ledger (`/data/state/live-usage.jsonl`). A fresh volume starts the lifetime cap from zero.
- Behind a reverse proxy, add `-e NOMOUSE_TRUST_PROXY=1` so each client is rate-limited by the proxy's `X-Forwarded-For` address (3 live runs per address per hour). Do not set it without a proxy, or clients can pick their own address.
- To show the recorded eval runs (reviews, packets and the counts page), mount a runs directory read-only: `-e NOMOUSE_RUNS=/runs -v "$PWD/runs:/runs:ro"`. Navigator runs in `runs/navigator/` are shown too, with their reviews labelled `live`.

Live runs added after a start are listed by link only and are not kept in the replay across restarts; their run directories stay in `/data/state/runs`.
