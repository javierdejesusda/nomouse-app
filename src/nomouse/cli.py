"""Command line interface: `nomouse mirror` and `nomouse walk`."""

import argparse
import contextlib
import datetime
import ipaddress
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from nomouse.driver import DEFAULT_MAX_KEYS, RECORD_NAME, new_walk_context, walk
from nomouse.mirror import WALK_PAGES, fetch_mirror, mirror_root
from nomouse.server import serve_directory

_DEFAULT_CACHE = Path(".cache")
_DEFAULT_RUNS = Path("runs")


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
    walk_command = commands.add_parser("walk", help="walk one page with the keyboard")
    walk_command.add_argument(
        "target",
        help=f"a loopback URL, or a mirror page: {', '.join(WALK_PAGES)}",
    )
    walk_command.add_argument("--cache", type=Path, default=_DEFAULT_CACHE)
    walk_command.add_argument(
        "--out", type=Path, help="run directory (default: runs/<UTC time>)"
    )
    walk_command.add_argument("--max-keys", type=int, default=DEFAULT_MAX_KEYS)
    return parser


def _mirror(cache: Path) -> int:
    root = fetch_mirror(cache)
    print(f"W3C demo mirror at {root}")
    for page in WALK_PAGES:
        print(f"  {page}")
    return 0


def _walk(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.max_keys < 1:
        parser.error("--max-keys must be at least 1")
    target: str = args.target
    with contextlib.ExitStack() as stack:
        if target in WALK_PAGES:
            root = mirror_root(args.cache)
            if not root.is_dir():
                parser.error(f"no mirror in {args.cache}; run `nomouse mirror` first")
            url = stack.enter_context(serve_directory(root)) + target
        elif _is_loopback_url(target):
            url = target
        else:
            parser.error(
                "target must be a loopback URL or one of: " + ", ".join(WALK_PAGES)
            )
        out: Path = args.out or _DEFAULT_RUNS / _timestamp()
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch()
        stack.callback(browser.close)
        record = walk(new_walk_context(browser), url, out, max_keys=args.max_keys)
    print(f"{len(record.keys)} keys, ended by {record.end_reason}: {out / RECORD_NAME}")
    return 0


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
