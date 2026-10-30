import hashlib
import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from test_navigator import Navigator

from nomouse import spark
from nomouse.cli import main


def in_thread(target: Callable[[], int]) -> int:
    """Runs the CLI off the main thread, where the session's Playwright runs."""
    results: list[int] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(target())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return results[0]


@pytest.mark.parametrize("task", ["", "   ", "x" * 201, "open\x07it"])
def test_navigate_refuses_a_task_that_is_not_a_short_line(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, task: str
) -> None:
    out = tmp_path / "run"

    with pytest.raises(SystemExit) as caught:
        main(["navigate", "http://127.0.0.1:9/", "--task", task, "--out", str(out)])

    assert caught.value.code == 2
    assert "task" in capsys.readouterr().err
    assert not out.exists()


def test_navigate_refuses_a_url_that_is_not_on_loopback(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["navigate", "https://example.com/", "--task", "go"])

    assert caught.value.code == 2
    assert "loopback" in capsys.readouterr().err


def test_navigate_drives_the_page_and_freezes_the_walker_verdicts(
    site: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "run"
    transport = Navigator(["Tab", "Enter", "Escape", "done"])
    monkeypatch.setattr(spark, "urllib_transport", transport)

    code = in_thread(
        lambda: main(
            [
                "navigate",
                site + "dialog.html",
                "--task",
                "open the settings dialog and close it",
                "--out",
                str(out),
            ]
        )
    )

    assert code == 0
    record = json.loads((out / "walk.json").read_text(encoding="utf-8"))
    assert record["keys"] == ["Tab", "Enter", "Escape"]
    assert record["navigator"]["task"] == "open the settings dialog and close it"
    digest = hashlib.sha256((out / "verdicts.json").read_bytes()).hexdigest()
    printed = capsys.readouterr().out
    assert "3 keys, navigator answered done" in printed
    assert "4 calls, 20000 input and 360 output tokens" in printed
    assert f"verdicts frozen: sha256 {digest}" in printed
    assert len(transport.requests) == 4


def test_navigate_reports_a_failed_call_without_freezing(
    site: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "run"
    monkeypatch.setattr(spark, "urllib_transport", Navigator([], status=502))

    code = in_thread(
        lambda: main(
            ["navigate", site + "focus.html", "--task", "go", "--out", str(out)]
        )
    )

    assert code == 1
    assert "call failed" in capsys.readouterr().err
    assert not (out / "verdicts.json").exists()
