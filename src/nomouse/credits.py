"""Lifetime credit cap for live runs, computed from logged API usage.

Prices are Muse Spark's Standard mode prices from
https://dev.meta.ai/docs/pricing-rate-limits (read 2026-10-30). Every
answered call is appended to a JSON lines ledger on disk, so the spend
survives restarts; the ledger holds usage only, never prompts, images or
headers.
"""

import datetime
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nomouse.spark import Transport

INPUT_PRICE = 1.25e-6
"""USD per uncached input token."""
CACHED_INPUT_PRICE = 0.15e-6
"""USD per cached input token."""
OUTPUT_PRICE = 4.25e-6
"""USD per output token; reasoning tokens bill as output."""
MAX_CAP_USD = 20.0
"""The live credit cap may never exceed this, for the life of the page."""


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
    """The live spend so far, persisted as one JSON line per answered call."""

    def __init__(self, path: Path, cap_usd: float = MAX_CAP_USD) -> None:
        """Initializes the ledger.

        Args:
            path: The JSON lines file; created on the first record.
            cap_usd: The lifetime cap in USD.

        Raises:
            ValueError: The cap is above USD 20.
        """
        if cap_usd > MAX_CAP_USD:
            raise ValueError(f"the live cap must be at most USD {MAX_CAP_USD:.0f}")
        self.path = path
        self.cap_usd = cap_usd
        self._lock = threading.Lock()

    def spent_usd(self, run: str | None = None) -> float:
        """Returns the USD spent so far, summed from the ledger.

        Args:
            run: When given, only that live run's calls are summed.

        Raises:
            ValueError: A ledger line is not valid JSON.
        """
        return sum(float(line["cost_usd"]) for line in self._lines(run))

    def calls(self, run: str) -> int:
        """Returns how many answered calls a live run made.

        Args:
            run: The live run.

        Raises:
            ValueError: A ledger line is not valid JSON.
        """
        return len(self._lines(run))

    def _lines(self, run: str | None) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.is_file():
                return []
            text = self.path.read_text(encoding="utf-8")
        lines = [json.loads(line) for line in text.splitlines()]
        return [line for line in lines if run is None or line["run"] == run]

    def can_afford(self, reserve_usd: float) -> bool:
        """Returns whether a run that may cost `reserve_usd` stays within the cap.

        Args:
            reserve_usd: The most one run is expected to cost.
        """
        return self.spent_usd() + reserve_usd <= self.cap_usd

    def record(self, run: str, usage: Mapping[str, Any]) -> None:
        """Appends one answered call to the ledger.

        Args:
            run: The live run the call belongs to.
            usage: The call's `usage` object.
        """
        line = {
            "time": datetime.datetime.now(datetime.UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            "run": run,
            "usage": dict(usage),
            "cost_usd": call_cost(usage),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as ledger:
                ledger.write(json.dumps(line) + "\n")

    def metered(self, transport: Transport, run: str) -> Transport:
        """Wraps a transport so every answered call is recorded.

        Args:
            transport: The transport that sends the requests.
            run: The live run the calls belong to.

        Returns:
            A transport with the same replies that records the `usage` of
            every HTTP 200 reply that carries one.
        """

        def send(
            url: str, body: bytes, headers: Mapping[str, str]
        ) -> tuple[int, bytes]:
            status, raw = transport(url, body, headers)
            if status == 200:
                try:
                    usage = json.loads(raw)["usage"]
                    call_cost(usage)
                except (ValueError, KeyError, TypeError):
                    return status, raw
                self.record(run, usage)
            return status, raw

        return send
