"""Command line interface: the `nomouse` subcommands."""

import argparse
import contextlib
import datetime
import functools
import ipaddress
import json
import os
import sys
import urllib.error
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Browser, sync_playwright

from nomouse import (
    bare,
    credits,
    evaluation,
    export,
    g1,
    live,
    navigator,
    packets,
    reviewer,
    scorer,
    spark,
)
from nomouse.driver import (
    DEFAULT_MAX_KEYS,
    INCOMPLETE_RECORD_NAME,
    RECORD_NAME,
    new_walk_context,
    walk,
)
from nomouse.mirror import WALK_PAGES, fetch_mirror, mirror_root
from nomouse.server import make_server, serve_directory
from nomouse.walkers import VERDICTS_NAME, freeze

_DEFAULT_CACHE = Path(".cache")
_DEFAULT_RUNS = Path("runs")
_BUILDER_PREFIX = "builder/"
BUILDER_ROOT = Path(__file__).resolve().parents[2] / "builder"
_DEFAULT_LABELS = BUILDER_ROOT / "labels.json"
_DEFAULT_W3C_LABELS = BUILDER_ROOT.parent / "eval" / "w3c-labels.json"


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the command line interface.

    Args:
        argv: Arguments without the program name; defaults to `sys.argv[1:]`.

    Returns:
        The process exit code.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "mirror":
        return _mirror(args.cache)
    if args.command == "g1":
        return _g1(parser, args)
    if args.command == "review":
        return _review(parser, args)
    if args.command == "bare":
        return _bare(parser, args)
    if args.command == "packets":
        return _packets(parser, args)
    if args.command == "score":
        return _score(parser, args)
    if args.command == "eval":
        return _eval(parser, args)
    if args.command == "export":
        return _export(parser, args)
    if args.command == "serve-replay":
        return _serve_replay(parser, args)
    if args.command == "navigate":
        return _navigate(parser, args)
    return _walk(parser, args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nomouse", description="Keyboard-only focus walks of web pages."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    mirror = commands.add_parser(
        "mirror", help="download and unpack the W3C demo (News excluded)"
    )
    mirror.add_argument("--cache", type=Path, default=_DEFAULT_CACHE)
    walk_command = commands.add_parser(
        "walk", help="walk one page with the keyboard and freeze walker verdicts"
    )
    target_help = (
        "a loopback URL, a builder page (builder/<file>.html), or a mirror"
        f" page: {', '.join(WALK_PAGES)}"
    )
    walk_command.add_argument("target", help=target_help)
    walk_command.add_argument("--cache", type=Path, default=_DEFAULT_CACHE)
    walk_command.add_argument(
        "--out", type=Path, help="run directory (default: runs/<UTC time>)"
    )
    walk_command.add_argument("--max-keys", type=int, default=DEFAULT_MAX_KEYS)
    navigate_command = commands.add_parser(
        "navigate",
        help="keyboard navigator: Muse Spark presses keys for a typed task, then"
        " walker verdicts are frozen (live calls)",
    )
    navigate_command.add_argument("target", help=target_help)
    navigate_command.add_argument(
        "--task",
        required=True,
        help=f"what to do on the page, at most {navigator.MAX_TASK_CHARS} characters",
    )
    navigate_command.add_argument("--cache", type=Path, default=_DEFAULT_CACHE)
    navigate_command.add_argument(
        "--out",
        type=Path,
        help="run directory (default: runs/navigator/<UTC time>, which the"
        " replay export reads)",
    )
    gate = commands.add_parser(
        "g1", help="gate G1: Muse Spark token usage over one walk (live calls)"
    )
    gate.add_argument("run_dir", type=Path, help="run directory of a finished walk")
    gate.add_argument(
        "--log", type=Path, help="usage log (default: <run_dir>/g1-usage.jsonl)"
    )
    gate.add_argument("--calls", type=int, default=g1.DEFAULT_CALLS)
    gate.add_argument("--window", type=int, default=g1.DEFAULT_WINDOW)
    review = commands.add_parser(
        "review",
        help="focus reviewer: Muse Spark form (i) on a frozen walk",
    )
    review.add_argument("run_dir", type=Path, help="run directory of a frozen walk")
    review.add_argument(
        "--run",
        help="run label: writes review-<run>.json instead of review.json",
    )
    bare_command = commands.add_parser(
        "bare",
        help="bare-Spark eval arm: the walk's video with the bare prompt",
    )
    bare_command.add_argument(
        "run_dir", type=Path, help="run directory of a frozen walk"
    )
    bare_command.add_argument(
        "--run", required=True, help="run label: writes bare-<run>.json"
    )
    packets_command = commands.add_parser(
        "packets", help="packet writer: one packet per finding (Muse Spark text)"
    )
    packets_command.add_argument(
        "run_dir", type=Path, help="run directory of a frozen walk"
    )
    packets_command.add_argument(
        "--run", help="review run label: reads review-<run>.json"
    )
    packets_command.add_argument(
        "--template", action="store_true", help="template sentences, no model call"
    )
    score_command = commands.add_parser(
        "score", help="score reviews against the hashed builder labels (no model)"
    )
    score_command.add_argument(
        "run_dirs", type=Path, nargs="+", help="reviewed walk run directories"
    )
    score_command.add_argument(
        "--run", help="run label of the reviews (review-<run>.json)"
    )
    score_command.add_argument("--labels", type=Path, default=_DEFAULT_LABELS)
    score_command.add_argument("--out", type=Path, help="write the report as JSON")
    eval_command = commands.add_parser(
        "eval", help="four-arm eval: W0, W1, NoMouse, bare Spark (no model call)"
    )
    eval_command.add_argument(
        "run_dirs", type=Path, nargs="+", help="frozen walk run directories"
    )
    eval_command.add_argument(
        "--runs", nargs="+", required=True, help="model arm run labels"
    )
    eval_command.add_argument("--builder-labels", type=Path, default=_DEFAULT_LABELS)
    eval_command.add_argument("--w3c-labels", type=Path, default=_DEFAULT_W3C_LABELS)
    eval_command.add_argument(
        "--own-row", action="append", default=[], help="row id shown on its own"
    )
    eval_command.add_argument("--out", type=Path, help="write the report as JSON")
    export_command = commands.add_parser(
        "export", help="build the static replay site from frozen runs (no model)"
    )
    export_command.add_argument(
        "out_dir", type=Path, help="new or empty directory for the site"
    )
    export_command.add_argument("--runs-dir", type=Path, default=_DEFAULT_RUNS)
    export_command.add_argument(
        "--runs",
        nargs="+",
        default=list(export.DEFAULT_REVIEW_RUNS),
        help="review run labels shown with their packets",
    )
    export_command.add_argument(
        "--audit",
        type=Path,
        help="audit-corrected counts to show beside the recorded ones",
    )
    serve_command = commands.add_parser(
        "serve-replay", help="serve an exported replay site on 127.0.0.1"
    )
    serve_command.add_argument("site_dir", type=Path, help="an exported site")
    serve_command.add_argument("--port", type=int, default=8000)
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument(
        "--live",
        action="store_true",
        help="also take live runs on the allowlisted pages (model calls)",
    )
    serve_command.add_argument(
        "--state-dir",
        type=Path,
        default=Path("live-state"),
        help="live runs and the lifetime credit ledger (keep it across restarts)",
    )
    serve_command.add_argument("--cache", type=Path, default=_DEFAULT_CACHE)
    serve_command.add_argument(
        "--cap-usd",
        type=float,
        default=credits.MAX_CAP_USD,
        help="lifetime live credit cap, at most USD 20",
    )
    serve_command.add_argument(
        "--trust-proxy",
        action="store_true",
        help="rate-limit by the last X-Forwarded-For address (behind a proxy)",
    )
    return parser


def _mirror(cache: Path) -> int:
    root = fetch_mirror(cache)
    print(f"W3C demo mirror at {root}")
    for page in WALK_PAGES:
        print(f"  {page}")
    return 0


def _g1(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if not 1 <= args.window <= min(args.calls, spark.MAX_IMAGES):
        parser.error(
            f"--window must be between 1 and --calls, at most {spark.MAX_IMAGES}"
        )
    log: Path = args.log or args.run_dir / "g1-usage.jsonl"
    if log.exists():
        parser.error(f"{log} already exists; choose another --log")
    if not (args.run_dir / RECORD_NAME).is_file():
        parser.error(f"no {RECORD_NAME} in {args.run_dir}; walk a page first")
    try:
        replies = g1.run_g1(
            args.run_dir,
            log,
            calls=args.calls,
            window=args.window,
            transport=spark.urllib_transport,
        )
    except spark.SparkError as error:
        logged = (
            len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
        )
        print(
            f"nomouse g1: call failed: {error}; {logged} calls logged in {log}",
            file=sys.stderr,
        )
        return 1
    except ValueError as error:
        parser.error(str(error))
    print("call  input  output  reasoning  latency_s")
    for number, reply in enumerate(replies, start=1):
        usage = reply.usage
        reasoning = usage.get("output_tokens_details", {}).get("reasoning_tokens")
        print(
            f"{number:4}  {usage['input_tokens']:5}  {usage['output_tokens']:6}"
            f"  {reasoning!s:>9}  {reply.latency_s:9.2f}"
        )
    summary = g1.summarize([reply.usage for reply in replies], args.window)
    for label, flat in (
        (f"calls {args.window}-{args.calls} (window full)", summary.full_window),
        (f"all {args.calls} calls", summary.all_calls),
    ):
        print(
            f"{label}: mean input {flat.mean:.0f}, range {flat.low}-{flat.high},"
            f" max deviation {flat.max_deviation_pct:.1f}%"
        )
    print(f"mean output {summary.mean_output:.1f} tokens per call")
    if summary.mean_output > g1.FALLBACK_OUTPUT:
        print(
            "mean output above 3,000: cap walks at 25 keys and run builder pages once"
        )
    print(f"usage log: {log}")
    print("G1 PASS" if summary.passed else "G1 FAIL")
    return 0 if summary.passed else 1


def _review(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        result = reviewer.review(
            args.run_dir, run=args.run, transport=spark.urllib_transport
        )
    except FileExistsError as error:
        parser.error(f"{error}; give another --run label")
    except (spark.SparkError, reviewer.AnswerError) as error:
        partial = reviewer.partial_path(reviewer.review_path(args.run_dir, args.run))
        print(
            f"nomouse review: {error}; no review written,"
            f" completed calls are in {partial}",
            file=sys.stderr,
        )
        return 1
    except (ValueError, reviewer.ReviewError) as error:
        parser.error(str(error))
    findings = sum(instance["finding"] for instance in result["instances"])
    usage = result["usage"]
    print(f"verdicts sha256 {result['verdicts_sha256']}")
    print(
        f"{len(result['instances'])} instances, {findings} NEEDS REVIEW findings;"
        f" {usage['calls']} calls, {usage['input_tokens']} input and"
        f" {usage['output_tokens']} output tokens"
    )
    print(f"review: {reviewer.review_path(args.run_dir, args.run)}")
    return 0


def _bare(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        result = bare.bare_review(
            args.run_dir, run=args.run, transport=spark.urllib_transport
        )
    except reviewer.AnswerError as error:
        print(
            f"nomouse bare: {error}; no answer written, the reply is in"
            f" {bare.partial_path(args.run_dir, args.run)}",
            file=sys.stderr,
        )
        return 1
    except spark.SparkError as error:
        print(f"nomouse bare: call failed: {error}", file=sys.stderr)
        return 1
    except (FileExistsError, ValueError, reviewer.ReviewError) as error:
        parser.error(str(error))
    usage = result["usage"]
    print(
        f"{len(result['failures'])} failures listed;"
        f" {usage['input_tokens']} input and {usage['output_tokens']} output tokens"
    )
    print(f"answer: {bare.bare_path(args.run_dir, args.run)}")
    return 0


def _packets(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        result = packets.write_packets(
            args.run_dir,
            run=args.run,
            template=args.template,
            transport=spark.urllib_transport,
        )
    except (FileExistsError, ValueError, packets.PacketError) as error:
        parser.error(str(error))
    written = result["packets"]
    verified = sum(p["status"] == packets.VERIFIED for p in written)
    from_model = sum(p["sentence_source"] == "spark" for p in written)
    writer = result["writer"]
    print(
        f"{len(written)} packets: {verified} VERIFIED,"
        f" {len(written) - verified} NEEDS REVIEW"
    )
    print(
        f"sentences: {writer['mode']} ({from_model} from the model,"
        f" {len(written) - from_model} from the template)"
    )
    if writer["error"]:
        print(f"template fallback: {writer['error']}")
    if writer["usage"]:
        usage = writer["usage"]
        print(
            f"{usage['input_tokens']} input and {usage['output_tokens']} output tokens"
        )
    print(f"packets: {packets.packets_path(args.run_dir, args.run)}")
    return 0


def _score(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    out: Path | None = args.out
    if out is not None and out.exists():
        parser.error(f"{out} already exists; choose another --out")
    try:
        report = scorer.score(args.labels, args.run_dirs, run=args.run)
    except (OSError, scorer.ScoreError) as error:
        parser.error(str(error))
    if out is not None:
        with out.open("x", encoding="utf-8") as file:
            file.write(json.dumps(report, indent=2) + "\n")
    print(f"labels sha256 {report['labels_sha256']}")
    for digest in report["prompt_sha256"]:
        print(f"prompt sha256 {digest}")
    for name, gate in report["gates"].items():
        failures, clean = len(gate["failures"]), len(gate["clean"])
        print(
            f"{name}: strict {len(gate['caught_strict'])} of {failures},"
            f" loose {len(gate['caught_loose'])} of {failures};"
            f" false flags {len(gate['false_flags'])} of {clean}: {gate['verdict']}"
        )
    return 0


def _eval(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    out: Path | None = args.out
    if out is not None and out.exists():
        parser.error(f"{out} already exists; choose another --out")
    try:
        report = evaluation.evaluate(
            args.builder_labels, args.w3c_labels, args.run_dirs, runs_labels=args.runs
        )
        table = evaluation.counts_table(report, args.own_row)
    except (OSError, ValueError, evaluation.EvalError) as error:
        parser.error(str(error))
    if out is not None:
        with out.open("x", encoding="utf-8") as file:
            file.write(json.dumps(report, indent=2) + "\n")
    print(f"labels sha256 builder {report['labels_sha256']['builder']}")
    print(f"labels sha256 w3c {report['labels_sha256']['w3c']}")
    for kind, digests in report["prompt_sha256"].items():
        print(f"{kind} prompt sha256 {', '.join(digests)}")
    total = report["usage"]["total"]
    print(
        f"{total['calls']} calls, {total['input_tokens']} input and"
        f" {total['output_tokens']} output tokens, about USD {total['cost_usd']:.2f}"
    )
    if report["missing"]:
        print(f"INCOMPLETE: no run for {', '.join(report['missing'])}")
    print()
    print(table, end="")
    return 0


def _export(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        index = export.export_site(
            args.runs_dir, args.out_dir, review_runs=args.runs, audit=args.audit
        )
    except (OSError, ValueError, export.ExportError) as error:
        parser.error(str(error))
    pages = index["pages"]
    w3c = sum(page["set"] == "w3c" for page in pages)
    table = "eval table included" if index["eval"] else "no eval report"
    print(
        f"{len(pages)} pages ({w3c} W3C, {len(pages) - w3c} builder-made),"
        f" {table}: {args.out_dir}"
    )
    return 0


def _serve_replay(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if not (args.site_dir / "data" / "index.json").is_file():
        parser.error(f"no exported replay in {args.site_dir}; run `nomouse export`")
    service = _live_service(parser, args) if args.live else None
    server = make_server(
        args.site_dir,
        args.port,
        host=args.host,
        live=service,
        trust_proxy=args.trust_proxy,
    )
    if service is not None:
        message = service.status()["message"]
        print(message or "live mode on")
    print(f"replay at http://{args.host}:{server.server_address[1]}/ (Ctrl+C stops)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _live_service(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> live.LiveService:
    root = mirror_root(args.cache)
    if not root.is_dir():
        parser.error(f"no mirror in {args.cache}; run `nomouse mirror` first")
    try:
        ledger = credits.CreditLedger(args.state_dir / "live-usage.jsonl", args.cap_usd)
    except ValueError as error:
        parser.error(str(error))
    return live.LiveService(
        site=args.site_dir,
        state=args.state_dir,
        pages=live.live_pages(BUILDER_ROOT),
        pipeline=functools.partial(
            live.walk_and_review, roots={"w3c": root, "builder": BUILDER_ROOT}
        ),
        ledger=ledger,
        limiter=live.RateLimiter(max_runs=3, window_s=3600),
        transport=spark.urllib_transport,
        key_available=bool(os.environ.get(spark.KEY_VARIABLE, "").strip()),
    )


def _walk(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.max_keys < 1:
        parser.error("--max-keys must be at least 1")
    with contextlib.ExitStack() as stack:
        url, out = _open_target(parser, args, stack, _DEFAULT_RUNS)
        browser = _browser(stack)
        record = walk(new_walk_context(browser), url, out, max_keys=args.max_keys)
    print(f"{len(record.keys)} keys, ended by {record.end_reason}: {out / RECORD_NAME}")
    print(f"verdicts frozen: sha256 {freeze(record, out)}")
    return 0


def _navigate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        task = navigator.clean_task(args.task)
    except ValueError as error:
        parser.error(str(error))
    with contextlib.ExitStack() as stack:
        url, out = _open_target(parser, args, stack, _DEFAULT_RUNS / "navigator")
        browser = _browser(stack)
        try:
            record = navigator.navigate(
                new_walk_context(browser),
                url,
                out,
                task,
                transport=spark.urllib_transport,
            )
        except (
            spark.SparkError,
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
        ) as error:
            kept = out / INCOMPLETE_RECORD_NAME
            keys = len(json.loads(kept.read_text(encoding="utf-8"))["keys"])
            print(f"nomouse navigate: call failed: {error}", file=sys.stderr)
            print(
                f"incomplete record of {keys} key{'' if keys == 1 else 's'}: {kept}",
                file=sys.stderr,
            )
            return 1
    assert record.navigator is not None
    usage = record.navigator["usage"]
    ended = (
        f"navigator answered {record.end_reason}"
        if record.end_reason in ("done", "stuck")
        else f"ended by {record.end_reason}"
    )
    print(f"{len(record.keys)} keys, {ended}: {out / RECORD_NAME}")
    print(
        f"{usage['calls']} calls, {usage['input_tokens']} input and"
        f" {usage['output_tokens']} output tokens"
    )
    print(f"verdicts frozen: sha256 {freeze(record, out)}")
    return 0


def _open_target(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    stack: contextlib.ExitStack,
    runs: Path,
) -> tuple[str, Path]:
    """Serves a walk target if needed and returns its URL and run directory.

    The run directory is `--out`, or a new one named by the UTC time in
    `runs`.
    """
    target: str = args.target
    if target in WALK_PAGES:
        root = mirror_root(args.cache)
        if not root.is_dir():
            parser.error(f"no mirror in {args.cache}; run `nomouse mirror` first")
        url = stack.enter_context(serve_directory(root)) + target
    elif target.startswith(_BUILDER_PREFIX):
        page = _builder_page(target.removeprefix(_BUILDER_PREFIX))
        if page is None:
            parser.error(f"no builder page {target} in {BUILDER_ROOT}")
        url = stack.enter_context(serve_directory(BUILDER_ROOT)) + page
    elif _is_loopback_url(target):
        url = target
    else:
        parser.error(
            "target must be a loopback URL, builder/<file>.html or one of: "
            + ", ".join(WALK_PAGES)
        )
    out: Path = args.out or runs / _timestamp()
    if (out / VERDICTS_NAME).exists():
        parser.error(f"{out} already holds frozen verdicts; choose another --out")
    return url, out


def _browser(stack: contextlib.ExitStack) -> Browser:
    """Launches Chromium, closed with the stack."""
    playwright = stack.enter_context(sync_playwright())
    browser = playwright.chromium.launch()
    stack.callback(browser.close)
    return browser


def _builder_page(name: str) -> str | None:
    """Returns the page's path below the builder directory, if it is a file there."""
    page = (BUILDER_ROOT / name).resolve()
    if not page.is_file() or not page.is_relative_to(BUILDER_ROOT):
        return None
    return page.relative_to(BUILDER_ROOT).as_posix()


def _is_loopback_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        return False


def _timestamp() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
