import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from nomouse import live
from nomouse.credits import (
    CACHED_INPUT_PRICE,
    CALL_RESERVE_USD,
    INPUT_PRICE,
    OUTPUT_PRICE,
    TRUNCATED_LINE_USD,
    CapReachedError,
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


@pytest.mark.parametrize("cap", [20.01, float("inf"), float("nan"), -1.0])
def test_the_ledger_refuses_a_cap_outside_zero_to_twenty_dollars(
    tmp_path: Path, cap: float
) -> None:
    with pytest.raises(ValueError, match="between USD 0 and USD 20"):
        CreditLedger(tmp_path / "ledger.jsonl", cap_usd=cap)


def test_the_metered_transport_settles_answered_calls_and_keeps_the_rest_reserved(
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
    assert ledger.calls("live-7") == 1
    assert ledger.spent_usd("live-7") == pytest.approx(
        call_cost(USAGE) + 2 * CALL_RESERVE_USD
    )
    text = (tmp_path / "ledger.jsonl").read_text()
    assert {json.loads(line)["run"] for line in text.splitlines()} == {"live-7"}
    assert "LLM_secret" not in text
    assert "Authorization" not in text


def test_each_call_is_reserved_in_the_ledger_before_it_is_sent(
    tmp_path: Path,
) -> None:
    ledger = CreditLedger(tmp_path / "ledger.jsonl", cap_usd=20.0)
    spent_while_sending: list[float] = []

    def inner(url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
        spent_while_sending.append(CreditLedger(ledger.path).spent_usd())
        return 200, reply(USAGE)

    ledger.metered(inner, "live-1")("u", b"{}", {})

    assert spent_while_sending == [pytest.approx(CALL_RESERVE_USD)]
    assert ledger.spent_usd() == pytest.approx(call_cost(USAGE))


@pytest.mark.parametrize(
    "error", [TimeoutError(), ConnectionResetError(), RuntimeError("crash")]
)
def test_a_call_lost_to_a_timeout_or_crash_stays_counted_at_its_reserve(
    tmp_path: Path, error: Exception
) -> None:
    ledger = CreditLedger(tmp_path / "ledger.jsonl", cap_usd=20.0)

    def inner(url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
        raise error

    with pytest.raises(type(error)):
        ledger.metered(inner, "live-1")("u", b"{}", {})

    again = CreditLedger(ledger.path, cap_usd=20.0)
    assert again.spent_usd() == pytest.approx(CALL_RESERVE_USD)
    assert again.spent_usd("live-1") == pytest.approx(CALL_RESERVE_USD)
    assert again.calls("live-1") == 0


def test_the_call_reserve_is_ten_times_a_mean_navigator_call() -> None:
    # W3C Tickets, 2026-10-30: 41 navigator calls, 205,657 input and 10,982
    # output tokens.
    mean = call_cost({"input_tokens": 205_657, "output_tokens": 10_982}) / 41
    assert 10 * mean <= CALL_RESERVE_USD


def test_the_metered_transport_sends_nothing_once_the_cap_is_reached(
    tmp_path: Path,
) -> None:
    ledger = CreditLedger(tmp_path / "ledger.jsonl", cap_usd=0.01)
    sent: list[bytes] = []

    def inner(url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
        sent.append(body)
        return 200, reply({"input_tokens": 6_000, "output_tokens": 0})

    metered = ledger.metered(inner, "live-1")
    metered("u", b"first", {})
    metered("u", b"second", {})
    with pytest.raises(CapReachedError):
        metered("u", b"third", {})

    assert sent == [b"first", b"second"]
    assert ledger.spent_usd() == pytest.approx(0.015)


def test_a_truncated_last_line_counts_at_the_largest_run_reserve(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = CreditLedger(path, cap_usd=20.0)
    ledger.record("live-1", USAGE)
    with path.open("a", encoding="utf-8") as cut:
        cut.write('{"time": "2026-10-30T18:00:00Z", "run": "live-2", "us')

    assert ledger.spent_usd() == pytest.approx(call_cost(USAGE) + TRUNCATED_LINE_USD)
    assert TRUNCATED_LINE_USD >= live.TASK_RESERVE_USD >= live.RUN_RESERVE_USD


def test_a_record_after_a_truncated_line_starts_a_new_line(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"run": "live-1", "cost_us', encoding="utf-8")
    ledger = CreditLedger(path, cap_usd=20.0)

    ledger.record("live-2", USAGE)

    assert ledger.spent_usd() == pytest.approx(call_cost(USAGE) + TRUNCATED_LINE_USD)
    assert ledger.calls("live-2") == 1
    assert (
        path.read_text(encoding="utf-8").splitlines()[0] == '{"run": "live-1", "cost_us'
    )


@pytest.mark.parametrize("line", ['{"run": "live-1"}', "[1, 2]", '{"cost_usd": "x"}'])
def test_a_json_line_of_the_wrong_shape_still_cannot_be_read(
    tmp_path: Path, line: str
) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    with pytest.raises((ValueError, KeyError, TypeError)):
        CreditLedger(path, cap_usd=20.0).spent_usd()
