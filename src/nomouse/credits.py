"""Lifetime credit cap for live runs, computed from logged API usage.

Prices are Muse Spark's Standard mode prices from
https://dev.meta.ai/docs/pricing-rate-limits (read 2026-10-30). Every
answered call is appended to a JSON lines ledger on disk, so the spend
survives restarts; the ledger holds usage only, never prompts, images or
headers.
"""

import datetime
import json
import os
import threading
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO

from nomouse.spark import Transport

INPUT_PRICE = 1.25e-6
"""USD per uncached input token."""
CACHED_INPUT_PRICE = 0.15e-6
"""USD per cached input token."""
OUTPUT_PRICE = 4.25e-6
"""USD per output token; reasoning tokens bill as output."""
MAX_CAP_USD = 20.0
"""The live credit cap may never exceed this, for the life of the page."""
CALL_RESERVE_USD = 0.10
"""What a sent call counts for until its usage is recorded: about ten
times the mean navigator call of a 40-key run (USD 0.0074, 2026-10-30)."""
TRUNCATED_LINE_USD = 0.75
"""What a ledger line that is not JSON counts for: a write cut short by a
crash, so its cost is unknown. Not less than the most a live run is
expected to cost (`live.TASK_RESERVE_USD`), so the spend is never
under-counted and live mode need not wait for a repair by hand."""


class CapReachedError(Exception):
    """A model call was refused: the spend has reached the cap."""


def call_cost(usage: Mapping[str, Any]) -> float:
    """Returns the USD cost of one call from its `usage` object.

    Args:
        usage: The Responses API `usage` object of one call.

    Returns:
        The cost at the Standard mode prices.
    """
    details = usage.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens", 0))
    uncached = int(usage["input_tokens"]) - cached
    return (
        uncached * INPUT_PRICE
        + cached * CACHED_INPUT_PRICE
        + int(usage["output_tokens"]) * OUTPUT_PRICE
    )


class CreditLedger:
    """The live spend so far, persisted as JSON lines.

    Each metered call appends a reservation before it is sent and, once
    answered with usage, a line with its cost that settles it. A call whose
    usage never arrives (an error status, a timeout, a crash) stays counted
    at its reservation, so the spend is never under-counted.
    """

    def __init__(self, path: Path, cap_usd: float = MAX_CAP_USD) -> None:
        """Initializes the ledger.

        Args:
            path: The JSON lines file; created on the first record.
            cap_usd: The lifetime cap in USD.

        Raises:
            ValueError: The cap is not between USD 0 and USD 20.
        """
        # Written so that NaN, which compares false, is refused too.
        if not 0 <= cap_usd <= MAX_CAP_USD:
            raise ValueError(
                f"the live cap must be between USD 0 and USD {MAX_CAP_USD:.0f}"
            )
        self.path = path
        self.cap_usd = cap_usd
        self._lock = threading.Lock()

    def spent_usd(self, run: str | None = None) -> float:
        """Returns the USD spent so far, summed from the ledger.

        Answered calls count at their cost, calls still reserved at
        `CALL_RESERVE_USD`, and a line that is not JSON at
        `TRUNCATED_LINE_USD`, in the total only.

        Args:
            run: When given, only that live run's calls are summed.

        Raises:
            ValueError: A JSON ledger line has no number `cost_usd` or
                `reserve_usd`.
            KeyError: A JSON ledger line has neither.
            TypeError: A JSON ledger line is not an object.
        """
        lines = self._lines(run)
        settled = {line.get("call") for line in lines if line and "cost_usd" in line}
        total = 0.0
        for line in lines:
            if line is None:
                total += TRUNCATED_LINE_USD
            elif "reserve_usd" in line:
                if line["call"] not in settled:
                    total += float(line["reserve_usd"])
            else:
                total += float(line["cost_usd"])
        return total

    def calls(self, run: str) -> int:
        """Returns how many answered calls a live run made.

        Args:
            run: The live run.

        Raises:
            KeyError: A JSON ledger line has no `run`.
            TypeError: A JSON ledger line is not an object.
        """
        return sum(1 for line in self._lines(run) if line and "cost_usd" in line)

    def _lines(self, run: str | None) -> list[dict[str, Any] | None]:
        """Returns the ledger lines of a run, or all, with None for non-JSON."""
        with self._lock:
            if not self.path.is_file():
                return []
            text = self.path.read_text(encoding="utf-8")
        lines = [_parse(line) for line in text.splitlines()]
        if run is None:
            return lines
        return [line for line in lines if line is not None and line["run"] == run]

    def record(
        self, run: str, usage: Mapping[str, Any], call: str | None = None
    ) -> None:
        """Appends one answered call to the ledger.

        Args:
            run: The live run the call belongs to.
            usage: The call's `usage` object.
            call: The reservation the call settles, if it has one.
        """
        settles = {} if call is None else {"call": call}
        cost = call_cost(usage)
        self._append({"run": run, **settles, "usage": dict(usage), "cost_usd": cost})

    def reserve(self, run: str) -> str:
        """Appends a reservation for a call about to be sent.

        Args:
            run: The live run the call belongs to.

        Returns:
            The call's id, for `record`.
        """
        call = uuid.uuid4().hex
        self._append({"run": run, "call": call, "reserve_usd": CALL_RESERVE_USD})
        return call

    def _append(self, fields: Mapping[str, Any]) -> None:
        now = datetime.datetime.now(datetime.UTC)
        line = {"time": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), **fields}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a+b") as ledger:
                # A line cut short by a crash stays on its own line.
                cut = ledger.tell() > 0 and _last_byte(ledger) != b"\n"
                ledger.write((("\n" if cut else "") + json.dumps(line) + "\n").encode())

    def metered(self, transport: Transport, run: str) -> Transport:
        """Wraps a transport so every call is reserved, then recorded.

        Args:
            transport: The transport that sends the requests.
            run: The live run the calls belong to.

        Returns:
            A transport with the same replies that reserves each call in
            the ledger before sending it and records the `usage` of every
            HTTP 200 reply that carries one; any other call stays counted
            at its reservation. It refuses to send once the spend has
            reached the cap, so a run that costs more than expected
            overshoots by one call at most.

        Raises:
            CapReachedError: From the returned transport, when the spend has
                reached the cap.
        """

        def send(
            url: str, body: bytes, headers: Mapping[str, str]
        ) -> tuple[int, bytes]:
            if self.spent_usd() >= self.cap_usd:
                raise CapReachedError("the live credit cap is reached")
            call = self.reserve(run)
            status, raw = transport(url, body, headers)
            if status == 200:
                try:
                    usage = json.loads(raw)["usage"]
                    call_cost(usage)
                except (ValueError, KeyError, TypeError):
                    return status, raw
                self.record(run, usage, call)
            return status, raw

        return send


def _parse(line: str) -> dict[str, Any] | None:
    """Returns a ledger line's data, or None when it is not JSON."""
    try:
        data: dict[str, Any] = json.loads(line)
    except ValueError:
        return None
    return data


def _last_byte(ledger: BinaryIO) -> bytes:
    ledger.seek(-1, os.SEEK_END)
    return ledger.read(1)
