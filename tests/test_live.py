import json
import shutil
import threading
import urllib.error
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from nomouse.credits import TRUNCATED_LINE_USD, CreditLedger, call_cost
from nomouse.export import export_site
from nomouse.live import (
    LIVE_MAX_KEYS,
    RUN_RESERVE_USD,
    TASK_RESERVE_USD,
    LivePage,
    LiveService,
    RateLimiter,
    live_pages,
    walk_and_review,
)
from nomouse.navigator import NAVIGATOR_PROMPT
from nomouse.packets import WRITER_PROMPT
from nomouse.reviewer import FORM_I_PROMPT
from nomouse.spark import SparkError, Transport

BUILDER = Path(__file__).resolve().parents[1] / "builder"
USAGE = {"input_tokens": 4_000, "output_tokens": 200, "total_tokens": 4_200}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def api_reply(url: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
    reply = {
        "model": "muse-spark-1.3",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}
        ],
        "usage": USAGE,
    }
    return 200, json.dumps(reply).encode()


def copy_menu(
    fixture_runs: Path, tasks: list[str | None] | None = None
) -> Callable[..., None]:
    """Returns a pipeline that 'walks' by copying the fixture menu run.

    Each run's task is appended to `tasks` when given.
    """

    def pipeline(
        page: LivePage,
        run_dir: Path,
        transport: Transport,
        progress: Callable[[str], None],
        task: str | None,
    ) -> None:
        if tasks is not None:
            tasks.append(task)
        progress("walking")
        shutil.copytree(fixture_runs / "builder" / "menu", run_dir)
        (run_dir / "review-eval1.json").rename(run_dir / "review-live.json")
        progress("reviewing")
        transport("https://api.meta.ai/v1/responses", b"{}", {})

    return pipeline


def fail_with(error: Exception) -> Callable[..., None]:
    def pipeline(*args: Any) -> None:
        raise error

    return pipeline


@pytest.fixture
def make_service(
    fixture_runs: Path, tmp_path: Path
) -> Callable[..., tuple[LiveService, Clock]]:
    site = tmp_path / "site"
    export_site(fixture_runs, site)

    def make(
        pipeline: Callable[..., None] | None = None,
        *,
        key_available: bool = True,
        cap_usd: float = 20.0,
        limiter: RateLimiter | None = None,
        ledger: CreditLedger | None = None,
        max_stored_runs: int = 200,
        transport: Transport = api_reply,
    ) -> tuple[LiveService, Clock]:
        clock = Clock()
        service = LiveService(
            site=site,
            state=tmp_path / "state",
            pages=live_pages(BUILDER),
            pipeline=pipeline or copy_menu(fixture_runs),
            ledger=ledger
            or CreditLedger(tmp_path / "state" / "live-usage.jsonl", cap_usd),
            limiter=limiter or RateLimiter(max_runs=3, window_s=3600, clock=clock),
            transport=transport,
            key_available=key_available,
            clock=clock,
            max_stored_runs=max_stored_runs,
        )
        return service, clock

    return make


def test_live_pages_are_the_w3c_pages_without_news_and_the_builder_pages() -> None:
    pages = live_pages(BUILDER)

    ids = [page.id for page in pages]
    assert ids[:6] == [
        "before/home.html",
        "before/tickets.html",
        "before/survey.html",
        "after/home.html",
        "after/tickets.html",
        "after/survey.html",
    ]
    assert not any("news" in page_id for page_id in ids)
    builder = [page for page in pages if page.set == "builder"]
    assert len(builder) == 20
    assert {page.id for page in builder} == {
        f"builder/{path.name}" for path in BUILDER.glob("*.html")
    }
    home = pages[0]
    assert (home.set, home.title, home.replay_id) == (
        "w3c",
        "W3C Before: Home",
        "w3c-before-home",
    )
    menu = next(p for p in pages if p.id == "builder/menu-focus-stays.html")
    assert (menu.title, menu.replay_id) == (
        "Menu focus stays (builder-made)",
        "menu-focus-stays",
    )


def test_status_lists_the_pages_and_the_caps(
    make_service: Callable[..., tuple[LiveService, Clock]],
) -> None:
    service, _ = make_service()

    status = service.status()

    assert status["live"] is True
    assert status["message"] is None
    assert status["busy"] is False
    assert status["max_keys"] == LIVE_MAX_KEYS == 40
    assert status["model"] == "muse-spark-1.3"
    assert status["reasoning_effort"] == "minimal"
    assert status["cap_usd"] == 20.0
    assert status["spent_usd"] == 0.0
    assert status["max_task_chars"] == 200
    assert len(status["pages"]) == 26
    assert status["pages"][0] == {
        "id": "before/home.html",
        "title": "W3C Before: Home",
        "set": "w3c",
        "replay_id": None,
    }
    survey = next(p for p in status["pages"] if p["id"] == "before/survey.html")
    assert survey["replay_id"] == "w3c-before-survey"


def test_a_live_run_walks_reviews_and_lands_in_the_viewer(
    make_service: Callable[..., tuple[LiveService, Clock]], tmp_path: Path
) -> None:
    service, _ = make_service()

    code, reply = service.start("203.0.113.5", "builder/menu-focus-stays.html")
    service.join()

    assert code == 202
    run_id = reply["run"]
    assert run_id.startswith("live-")
    state = service.run(run_id)
    assert state is not None
    assert state["state"] == "done"
    assert state["page_id"] == run_id
    assert state["cost_usd"] == pytest.approx(call_cost(USAGE))
    page = json.loads(
        (tmp_path / "site" / "data" / "pages" / f"{run_id}.json").read_text()
    )
    assert page["live"]["page"] == "builder/menu-focus-stays.html"
    assert page["live"]["calls"] == 1
    assert page["live"]["max_keys"] == 40
    assert page["live"]["model"] == "muse-spark-1.3"
    assert page["live"]["cost_usd"] == pytest.approx(call_cost(USAGE))
    assert [review["run"] for review in page["reviews"]] == ["live"]
    assert service.status()["spent_usd"] == pytest.approx(call_cost(USAGE))


def test_only_listed_pages_can_be_run(
    make_service: Callable[..., tuple[LiveService, Clock]],
) -> None:
    calls: list[object] = []
    service, _ = make_service(lambda *args: calls.append(args))

    for page in (
        "https://example.com/",
        "builder/../pyproject.toml",
        "before/news.html",
        None,
    ):
        code, reply = service.start("203.0.113.5", page)
        assert code == 400
        assert "listed pages" in reply["message"]

    assert calls == []


def test_one_run_at_a_time(
    make_service: Callable[..., tuple[LiveService, Clock]], fixture_runs: Path
) -> None:
    release = threading.Event()
    walk = copy_menu(fixture_runs)

    def slow(*args: Any) -> None:
        release.wait(5)
        walk(*args)

    service, _ = make_service(slow)

    first, _ = service.start("203.0.113.5", "builder/menu-focus-stays.html")
    second, reply = service.start("198.51.100.7", "before/home.html")
    assert service.status()["busy"] is True
    release.set()
    service.join()

    assert (first, second) == (202, 409)
    assert "one at a time" in reply["message"]
    assert reply["replay_id"] is None


def test_each_client_is_rate_limited(
    make_service: Callable[..., tuple[LiveService, Clock]],
) -> None:
    service, clock = make_service()

    codes = []
    for _ in range(4):
        code, refused = service.start("203.0.113.5", "builder/menu-focus-stays.html")
        codes.append(code)
        service.join()
    other, _ = service.start("198.51.100.7", "builder/menu-focus-stays.html")
    service.join()
    clock.now += 3601
    later, _ = service.start("203.0.113.5", "builder/menu-focus-stays.html")
    service.join()

    assert codes == [202, 202, 202, 429]
    assert refused["message"].startswith(
        "This address has started 3 live runs in the last 60 minutes"
    )
    assert (other, later) == (202, 202)


def test_an_exhausted_cap_falls_back_to_replay_without_a_call(
    make_service: Callable[..., tuple[LiveService, Clock]], tmp_path: Path
) -> None:
    calls: list[object] = []
    service, _ = make_service(lambda *args: calls.append(args), cap_usd=1.0)
    ledger = CreditLedger(tmp_path / "state" / "live-usage.jsonl", 1.0)
    ledger.record("earlier", {"input_tokens": 700_000, "output_tokens": 0})

    status = service.status()
    code, reply = service.start("203.0.113.5", "before/home.html")

    assert status["live"] is False
    assert status["message"].startswith(
        "Live mode is off: the lifetime live credit cap of USD 1.00 is used up"
    )
    assert "spent; a run may cost up to USD 0.25)" in status["message"]
    assert "replay" in status["message"]
    assert code == 503
    assert reply["message"] == status["message"]
    assert calls == []
    assert RUN_RESERVE_USD > 1.0 - 0.875


class StaleLedger(CreditLedger):
    """A ledger whose next read is taken just before a running run spends."""

    def __init__(self, path: Path, cap_usd: float) -> None:
        super().__init__(path, cap_usd)
        self.before_answer: Callable[[], None] | None = None

    def spent_usd(self, run: str | None = None) -> float:
        spent = super().spent_usd(run)
        if (hook := self.before_answer) is not None:
            self.before_answer = None
            hook()
        return spent


def test_a_start_checks_the_cap_after_the_previous_run_has_spent(
    make_service: Callable[..., tuple[LiveService, Clock]],
    fixture_runs: Path,
    tmp_path: Path,
) -> None:
    release = threading.Event()
    walks: list[str] = []
    walk = copy_menu(fixture_runs)

    def spend_after_release(
        page: LivePage,
        run_dir: Path,
        transport: Transport,
        progress: Any,
        task: str | None,
    ) -> None:
        walks.append(page.id)
        release.wait(5)
        if len(walks) == 1:
            walk(page, run_dir, transport, progress, task)

    ledger = StaleLedger(tmp_path / "state" / "live-usage.jsonl", 1.0)
    big = {"input_tokens": 640_000, "output_tokens": 0}

    def costly(url: str, body: bytes, headers: Mapping[str, str]) -> Any:
        return 200, json.dumps({"usage": big}).encode()

    service, _ = make_service(spend_after_release, ledger=ledger, transport=costly)
    first, _ = service.start("203.0.113.5", "builder/menu-focus-stays.html")

    def finish_first_run() -> None:
        release.set()
        service.join()

    ledger.before_answer = finish_first_run
    second, _ = service.start("198.51.100.7", "before/home.html")
    ledger.before_answer = None
    release.set()
    service.join()

    assert first == 202
    assert second in (409, 503)
    assert walks == ["builder/menu-focus-stays.html"]
    assert ledger.spent_usd() == pytest.approx(0.8)


def test_live_mode_turns_off_when_the_stored_runs_reach_their_limit(
    make_service: Callable[..., tuple[LiveService, Clock]], tmp_path: Path
) -> None:
    for name in ("live-a", "live-b"):
        (tmp_path / "state" / "runs" / name).mkdir(parents=True)
    calls: list[object] = []
    service, _ = make_service(lambda *a: calls.append(a), max_stored_runs=2)

    code, reply = service.start("203.0.113.5", "before/home.html")

    assert code == 503
    assert reply["message"].startswith(
        "Live mode is off: this server keeps at most 2 live runs"
    )
    assert service.status()["live"] is False
    assert calls == []


def test_an_unreadable_ledger_keeps_live_mode_off(
    make_service: Callable[..., tuple[LiveService, Clock]], tmp_path: Path
) -> None:
    service, _ = make_service()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "live-usage.jsonl").write_text(
        '{"cost_usd": 0.1}\n{"run": "x"}\n'
    )

    code, reply = service.start("203.0.113.5", "before/home.html")

    assert code == 503
    assert "ledger cannot be read" in reply["message"]


def test_a_truncated_last_ledger_line_counts_and_leaves_live_mode_on(
    make_service: Callable[..., tuple[LiveService, Clock]], tmp_path: Path
) -> None:
    calls: list[object] = []
    service, _ = make_service(lambda *a: calls.append(a))
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "live-usage.jsonl").write_text('{"cost_usd": 0.1}\n{"co')

    code, _ = service.start("203.0.113.5", "before/home.html")
    service.join()

    assert code == 202
    assert len(calls) == 1
    assert service.status()["spent_usd"] == pytest.approx(0.1 + TRUNCATED_LINE_USD)


def test_no_key_falls_back_to_replay(
    make_service: Callable[..., tuple[LiveService, Clock]],
) -> None:
    service, _ = make_service(key_available=False)

    code, reply = service.start("203.0.113.5", "before/home.html")

    assert code == 503
    assert "no Meta Model API key" in reply["message"]
    assert service.status()["live"] is False


@pytest.mark.parametrize(
    "error",
    [
        SparkError(502, "upstream: secret detail"),
        urllib.error.URLError("no route"),
        TimeoutError("timed out"),
    ],
)
def test_an_unreachable_api_fails_the_run_and_pauses_live_mode(
    make_service: Callable[..., tuple[LiveService, Clock]], error: Exception
) -> None:
    service, clock = make_service(fail_with(error))

    _, reply = service.start("203.0.113.5", "builder/menu-focus-stays.html")
    service.join()

    state = service.run(reply["run"])
    assert state is not None
    assert state["state"] == "failed"
    assert "could not reach the Meta Model API" in state["message"]
    assert "secret detail" not in json.dumps(state)
    assert state["replay_id"] is None
    status = service.status()
    assert status["live"] is False
    assert "paused" in status["message"]
    clock.now += 601
    assert service.status()["live"] is True


def test_another_failure_says_the_run_could_not_finish(
    make_service: Callable[..., tuple[LiveService, Clock]],
) -> None:
    service, _ = make_service(fail_with(RuntimeError("browser crashed at /secret")))

    _, reply = service.start("203.0.113.5", "builder/menu-focus-stays.html")
    service.join()

    state = service.run(reply["run"])
    assert state is not None
    assert state["state"] == "failed"
    assert state["message"].startswith("The live run could not finish")
    assert "/secret" not in state["message"]
    assert service.status()["live"] is True


def test_an_unknown_run_is_none(
    make_service: Callable[..., tuple[LiveService, Clock]],
) -> None:
    service, _ = make_service()

    assert service.run("live-nope") is None


def test_the_replay_link_follows_a_revised_page_to_its_v2_run(
    fixture_runs: Path, tmp_path: Path
) -> None:
    site = tmp_path / "site"
    export_site(fixture_runs, site)
    page = LivePage("builder/menu-v1.html", "Menu v1", "builder", "menu-v1")
    service = LiveService(
        site=site,
        state=tmp_path / "state",
        pages=[page],
        pipeline=copy_menu(fixture_runs),
        ledger=CreditLedger(tmp_path / "ledger.jsonl"),
        limiter=RateLimiter(max_runs=1, window_s=1),
        transport=api_reply,
        key_available=True,
    )

    assert service.status()["pages"][0]["replay_id"] == "menu-v1-v2"


def in_thread(target: Callable[[], None]) -> None:
    """Runs a pipeline off the main thread, as the live service does.

    The session's Playwright already runs on the main thread, and the sync
    API allows one per thread.
    """
    errors: list[BaseException] = []

    def run() -> None:
        try:
            target()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]


class ModelTransport:
    """Answers the navigator, the reviewer and the packet writer like the API."""

    def __init__(self, keys: list[str] | None = None) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.keys = keys or []

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        request = json.loads(body)
        self.bodies.append(request)
        text = request["input"][0]["content"][0]["text"]
        if text.startswith(NAVIGATOR_PROMPT):
            key = self.keys.pop(0) if self.keys else "stuck"
            answer: dict[str, Any] = {"answer": key, "reason": "Seen."}
        elif text.startswith(FORM_I_PROMPT):
            asked = json.loads(text.removeprefix(FORM_I_PROMPT))["instances"]
            answer = {
                "instances": [
                    {
                        "step": item["step"],
                        "opened": "yes",
                        "inside": {"answer": "pass", "reason": "On Close."},
                        "returned": {"answer": "pass", "reason": "Back on Open."},
                    }
                    for item in asked
                ]
            }
        else:
            assert text.startswith(WRITER_PROMPT)
            findings = json.loads(text.removeprefix(WRITER_PROMPT))["findings"]
            answer = {
                "sentences": [
                    {"id": f["id"], "sentence": "Observed."} for f in findings
                ]
            }
        reply = {
            "model": "muse-spark-1.3",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(answer)}],
                }
            ],
            "usage": USAGE,
        }
        return 200, json.dumps(reply).encode()


def test_the_pipeline_walks_freezes_reviews_and_writes_packets(
    tmp_path: Path,
) -> None:
    pages = Path(__file__).parent / "pages"
    page = LivePage("builder/dialog.html", "Dialog", "builder", "dialog")
    transport = ModelTransport()
    stages: list[str] = []
    run_dir = tmp_path / "live-1"

    in_thread(
        lambda: walk_and_review(
            page, run_dir, transport, stages.append, None, roots={"builder": pages}
        )
    )

    assert stages == ["walking", "reviewing", "writing packets"]
    record = json.loads((run_dir / "walk.json").read_text())
    assert record["url"] == "/dialog.html"
    assert record["keys"][:3] == ["Tab", "Enter", "Escape"]
    assert (run_dir / "verdicts.json.sha256").is_file()
    assert (run_dir / "verdicts.json.frozen_at").is_file()
    review = json.loads((run_dir / "review-live.json").read_text())
    assert review["model"] == "muse-spark-1.3"
    assert review["reasoning_effort"] == "minimal"
    assert len(review["instances"]) >= 1
    assert (run_dir / "packets-live.json").is_file()
    assert transport.bodies
    for body in transport.bodies:
        assert body["model"] == "muse-spark-1.3"
        assert body["reasoning"] == {"effort": "minimal"}


def test_the_pipeline_stops_the_walk_at_forty_keys(tmp_path: Path) -> None:
    root = tmp_path / "pages"
    root.mkdir()
    links = "".join(f'<a href="#l{n}">Link {n}</a>\n' for n in range(60))
    (root / "many.html").write_text(
        f'<!doctype html><html lang="en"><title>Many</title><body>{links}</body></html>'
    )
    page = LivePage("builder/many.html", "Many", "builder", "many")
    run_dir = tmp_path / "live-1"

    in_thread(
        lambda: walk_and_review(
            page,
            run_dir,
            ModelTransport(),
            lambda _: None,
            None,
            roots={"builder": root},
        )
    )

    record = json.loads((run_dir / "walk.json").read_text())
    assert len(record["keys"]) == 40
    assert record["end_reason"] == "max_keys"


def test_a_typed_task_has_the_navigator_choose_the_keys(tmp_path: Path) -> None:
    pages = Path(__file__).parent / "pages"
    page = LivePage("builder/dialog.html", "Dialog", "builder", "dialog")
    transport = ModelTransport(["Tab", "Enter", "Escape", "done"])
    stages: list[str] = []
    run_dir = tmp_path / "live-1"

    in_thread(
        lambda: walk_and_review(
            page,
            run_dir,
            transport,
            stages.append,
            "open the settings and close them",
            roots={"builder": pages},
        )
    )

    assert stages == ["navigating", "reviewing", "writing packets"]
    record = json.loads((run_dir / "walk.json").read_text())
    assert record["keys"] == ["Tab", "Enter", "Escape"]
    assert record["end_reason"] == "done"
    assert record["navigator"]["task"] == "open the settings and close them"
    assert record["navigator"]["max_keys"] == 40
    verdicts = json.loads((run_dir / "verdicts.json").read_text())
    assert verdicts["keys"] == ["Tab", "Enter", "Escape"]
    review = json.loads((run_dir / "review-live.json").read_text())
    assert [i["steps"] for i in review["instances"]] == [
        {"trigger": 0, "enter": 1, "escape": 2}
    ]
    assert (run_dir / "packets-live.json").is_file()
    prompts = [b["input"][0]["content"][0]["text"] for b in transport.bodies]
    assert sum(p.startswith(NAVIGATOR_PROMPT) for p in prompts) == 4
    assert not any(
        "open the settings" in p for p in prompts if not p.startswith(NAVIGATOR_PROMPT)
    )


def test_a_start_passes_the_cleaned_task_to_the_pipeline(
    make_service: Callable[..., tuple[LiveService, Clock]],
    fixture_runs: Path,
    tmp_path: Path,
) -> None:
    tasks: list[str | None] = []
    service, _ = make_service(copy_menu(fixture_runs, tasks))

    code, reply = service.start(
        "203.0.113.5", "builder/menu-focus-stays.html", "  open\nthe menu  "
    )
    service.join()

    assert code == 202
    assert tasks == ["open the menu"]
    state = service.run(reply["run"])
    assert state is not None
    assert state["task"] == "open the menu"
    page = json.loads(
        (tmp_path / "site" / "data" / "pages" / f"{reply['run']}.json").read_text()
    )
    assert page["live"]["task"] == "open the menu"


def test_a_start_without_a_task_walks_with_the_driver(
    make_service: Callable[..., tuple[LiveService, Clock]], fixture_runs: Path
) -> None:
    tasks: list[str | None] = []
    service, _ = make_service(copy_menu(fixture_runs, tasks))

    code, reply = service.start("203.0.113.5", "builder/menu-focus-stays.html")
    service.join()

    assert code == 202
    assert tasks == [None]
    state = service.run(reply["run"])
    assert state is not None
    assert state["task"] is None


@pytest.mark.parametrize("task", [3, ["open"], "", "x" * 201, "open\x00it"])
def test_a_task_that_is_not_a_short_line_is_refused_before_any_run(
    make_service: Callable[..., tuple[LiveService, Clock]], task: object
) -> None:
    calls: list[object] = []
    service, _ = make_service(lambda *args: calls.append(args))

    code, reply = service.start("203.0.113.5", "builder/menu-focus-stays.html", task)

    assert code == 400
    assert reply["error"] == "bad_task"
    assert "200 characters" in reply["message"]
    assert calls == []
    assert service.status()["busy"] is False


def test_a_task_run_needs_the_larger_reserve(
    make_service: Callable[..., tuple[LiveService, Clock]], tmp_path: Path
) -> None:
    ledger = CreditLedger(tmp_path / "ledger.jsonl", 1.0)
    ledger.record("earlier", {"input_tokens": 400_000, "output_tokens": 0})
    assert RUN_RESERVE_USD < 0.5 < TASK_RESERVE_USD
    service, _ = make_service(ledger=ledger)

    code, reply = service.start(
        "203.0.113.5", "builder/menu-focus-stays.html", "open the menu"
    )

    assert code == 503
    assert reply["error"] == "live_off"
    assert f"USD {TASK_RESERVE_USD:.2f}" in reply["message"]
    code, _ = service.start("203.0.113.5", "builder/menu-focus-stays.html")
    service.join()
    assert code == 202
