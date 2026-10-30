import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from nomouse.credits import (
    CACHED_INPUT_PRICE,
    INPUT_PRICE,
    OUTPUT_PRICE,
    CreditLedger,
    call_cost,
)

USAGE = {
    "input_tokens": 10_000,
    "input_tokens_details": {"cached_tokens": 2_000},
    "output_tokens": 1_000,
    "output_tokens_details": {"reasoning_tokens": 400},
    "total_tokens": 11_000,
}


def reply(usage: Mapping[str, object]) -> bytes:
    return json.dumps(
        {
            "model": "muse-spark-1.3",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}
            ],
            "usage": usage,
        }
    ).encode()


def test_prices_are_the_documented_standard_mode_prices() -> None:
    assert (INPUT_PRICE, CACHED_INPUT_PRICE, OUTPUT_PRICE) == (
        1.25e-6,
        0.15e-6,
        4.25e-6,
    )


def test_a_call_costs_cached_input_at_the_cached_price() -> None:
    expected = 8_000 * 1.25e-6 + 2_000 * 0.15e-6 + 1_000 * 4.25e-6

    assert call_cost(USAGE) == pytest.approx(expected)
    assert call_cost({"input_tokens": 400, "output_tokens": 90}) == pytest.approx(
        400 * 1.25e-6 + 90 * 4.25e-6
    )


def test_the_ledger_persists_spend_on_disk_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "state" / "live-usage.jsonl"
    ledger = CreditLedger(path, cap_usd=20.0)
    assert ledger.spent_usd() == 0.0

    ledger.record("live-1", USAGE)
    ledger.record("live-2", USAGE)

    again = CreditLedger(path, cap_usd=20.0)
    assert again.spent_usd() == pytest.approx(2 * call_cost(USAGE))
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["run"] for line in lines] == ["live-1", "live-2"]
    assert lines[0]["usage"] == USAGE
    assert lines[0]["cost_usd"] == pytest.approx(call_cost(USAGE))
    assert lines[0]["time"].endswith("Z")
    ledger.record("live-2", {"input_tokens": 1000, "output_tokens": 0})
    assert again.spent_usd("live-2") == pytest.approx(call_cost(USAGE) + 0.00125)
    assert again.calls("live-2") == 2


def test_the_ledger_refuses_a_cap_above_twenty_dollars(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at most USD 20"):
        CreditLedger(tmp_path / "ledger.jsonl", cap_usd=20.01)


def test_can_afford_keeps_a_reserve_below_the_cap(tmp_path: Path) -> None:
    ledger = CreditLedger(tmp_path / "ledger.jsonl", cap_usd=0.05)
    assert ledger.can_afford(0.04)

    ledger.record("live-1", {"input_tokens": 20_000, "output_tokens": 0})

    assert ledger.spent_usd() == pytest.approx(0.025)
    assert not ledger.can_afford(0.03)
    assert ledger.can_afford(0.02)


def test_the_metered_transport_records_usage_of_answered_calls_only(
    tmp_path: Path,
) -> None:
    ledger = CreditLedger(tmp_path / "ledger.jsonl", cap_usd=20.0)
    replies = iter([(200, reply(USAGE)), (502, b"bad gateway"), (200, b"not json")])
    seen: list[tuple[str, bytes, Mapping[str, str]]] = []

    def inner(url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
        seen.append((url, body, headers))
        return next(replies)

    metered = ledger.metered(inner, "live-7")
    headers = {"Authorization": "Bearer LLM_secret", "Content-Type": "x"}
    results = [metered("https://api.meta.ai/v1/responses", b"{}", headers)]
    results += [metered("u", b"{}", {}) for _ in range(2)]

    assert [status for status, _ in results] == [200, 502, 200]
    assert seen[0] == ("https://api.meta.ai/v1/responses", b"{}", headers)
    text = (tmp_path / "ledger.jsonl").read_text()
    assert len(text.splitlines()) == 1
    assert json.loads(text)["run"] == "live-7"
    assert "LLM_secret" not in text
    assert "Authorization" not in text
