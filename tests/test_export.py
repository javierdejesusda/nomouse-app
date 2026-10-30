import datetime
import hashlib
import json
import os
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from nomouse.cli import main
from nomouse.export import W3C_NOTICE, ExportError, export_site
from nomouse.packets import DISCLAIMER
from nomouse.server import make_server


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_export_writes_an_index_of_w3c_and_builder_pages(
    fixture_runs: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"

    index = export_site(fixture_runs, out)

    assert index == load(out / "data" / "index.json")
    pages = {page["id"]: page for page in index["pages"]}
    assert list(pages) == ["w3c-before-survey", "menu", "menu-v1", "menu-v1-v2"]
    assert pages["menu-v1-v2"]["title"] == "Menu v1 v2"
    survey = pages["w3c-before-survey"]
    assert survey["set"] == "w3c"
    assert survey["label"] == "W3C Before"
    assert survey["title"] == "Survey"
    assert survey["source_url"] == "https://www.w3.org/WAI/demos/bad/before/survey.html"
    assert survey["notice"] == W3C_NOTICE
    assert W3C_NOTICE == "Copyright © 2012 W3C (MIT, ERCIM, Keio), All Rights Reserved"
    assert survey["walker_findings"] == 1
    assert survey["model_findings"] == 0
    menu = pages["menu"]
    assert menu["set"] == "builder"
    assert menu["label"] == "builder-made"
    assert menu["source_url"] is None
    assert menu["notice"] is None
    assert menu["model_findings"] == 1
    assert menu["superseded_by"] is None
    assert pages["menu-v1"]["superseded_by"] == "menu-v1-v2"
    assert index["eval"] is True
    for asset in ("index.html", "eval.html", "app.css"):
        assert (out / asset).is_file()
    assert (out / ".gitignore").read_text() == "*\n"


def test_each_page_keeps_its_captures_unmodified_with_the_focus_per_key(
    fixture_runs: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"

    export_site(fixture_runs, out)

    page = load(out / "data" / "pages" / "w3c-before-survey.json")
    frames = page["frames"]
    assert [frame["key"] for frame in frames] == [None, "Tab", "Tab"]
    assert [frame["step"] for frame in frames] == [None, 0, 1]
    assert frames[0]["screenshot"] == "captures/w3c-before-survey/initial.png"
    assert frames[0]["focus"]["box"] is None
    assert frames[2]["focus"]["name"] == "eMail"
    assert frames[2]["focus"]["box"] == {"x": 100, "y": 200, "width": 80, "height": 20}
    source = fixture_runs / "eval" / "w3c-before-survey"
    for frame, name in zip(frames, ("initial", "step-000", "step-001"), strict=True):
        copied = out / frame["screenshot"]
        assert copied.read_bytes() == (source / f"{name}.png").read_bytes()
    menu = load(out / "data" / "pages" / "menu.json")
    assert [frame["appeared"] for frame in menu["frames"]] == [0, 0, 2, 0]


def test_walker_verdicts_are_verified_with_their_hash_and_freeze_time(
    fixture_runs: Path, tmp_path: Path
) -> None:
    source = fixture_runs / "eval" / "w3c-before-survey"
    hash_file = source / "verdicts.json.sha256"
    os.utime(hash_file, (1_793_375_810, 1_793_375_810))
    out = tmp_path / "site"

    export_site(fixture_runs, out)

    verdicts = load(out / "data" / "pages" / "w3c-before-survey.json")["verdicts"]
    expected = hashlib.sha256((source / "verdicts.json").read_bytes()).hexdigest()
    assert verdicts["sha256"] == expected
    assert verdicts["frozen_at"] == datetime.datetime.fromtimestamp(
        1_793_375_810, datetime.UTC
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
    assert verdicts["thresholds"] == {"order_threshold_px": 24}
    walkers = {w["walker"]: w for w in verdicts["walkers"]}
    assert walkers["W0"]["findings"] == []
    assert walkers["W1"]["checked"] == {"order": 1}
    (finding,) = walkers["W1"]["findings"]
    assert finding["status"] == "VERIFIED"
    assert finding["best_practice"] is True
    assert finding["rule"] == "order"
    assert finding["step"] == 1
    assert finding["detail"] == "jumps up 100 px from INPUT#n"


def test_reviews_of_both_runs_are_shown_with_their_disagreements(
    fixture_runs: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"

    export_site(fixture_runs, out)

    page = load(out / "data" / "pages" / "menu.json")
    reviews = {review["run"]: review for review in page["reviews"]}
    assert sorted(reviews) == ["eval1", "eval2"]
    assert reviews["eval1"]["prompt_sha256"] == "p" * 64
    assert reviews["eval1"]["model"] == "muse-spark-1.3"
    (instance,) = reviews["eval1"]["instances"]
    assert instance["steps"] == {"trigger": 0, "enter": 1, "escape": 2}
    assert instance["opened"] == "yes"
    assert instance["inside"] == {
        "answer": "fail",
        "reason": "Inside fail.",
        "status": "NEEDS REVIEW",
    }
    assert instance["returned"]["status"] is None
    assert reviews["eval2"]["instances"][0]["inside"]["status"] is None
    assert page["disagreements"] == [{"enter": 1, "question": "inside"}]
    assert load(out / "data" / "pages" / "w3c-before-survey.json")["reviews"] == []


def test_packets_are_shown_per_run_with_the_fixed_disclaimer(
    fixture_runs: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"

    export_site(fixture_runs, out)

    page = load(out / "data" / "pages" / "menu.json")
    packets, empty = page["packets"]
    assert empty == {"run": "eval2", "disclaimer": DISCLAIMER, "packets": []}
    assert packets["run"] == "eval1"
    assert packets["disclaimer"] == DISCLAIMER
    (packet,) = packets["packets"]
    assert packet["status"] == "NEEDS REVIEW"
    assert packet["sentence"].startswith("A model saw focus stay on Menu")


def test_the_eval_page_data_holds_counts_only(
    fixture_runs: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"

    export_site(fixture_runs, out)

    data = load(out / "data" / "eval.json")
    assert sorted(data) == [
        "arms",
        "bar",
        "disagreements",
        "labels_sha256",
        "model_only",
        "runs",
    ]
    assert list(data["arms"]) == ["W1", "NoMouse eval1"]
    assert data["arms"]["NoMouse eval1"]["found"] == 12
    assert data["bar"]["met"] is True
    assert "W3C annotation text" not in (out / "data" / "eval.json").read_text()


def test_an_export_without_an_eval_report_says_so(
    fixture_runs: Path, tmp_path: Path
) -> None:
    (fixture_runs / "eval" / "eval-report.json").unlink()

    index = export_site(fixture_runs, tmp_path / "site")

    assert index["eval"] is False
    assert not (tmp_path / "site" / "data" / "eval.json").exists()


def test_export_refuses_verdicts_that_do_not_match_their_hash(
    fixture_runs: Path, tmp_path: Path
) -> None:
    verdicts = fixture_runs / "builder" / "menu" / "verdicts.json"
    verdicts.write_bytes(verdicts.read_bytes().replace(b"W1", b"W2"))

    with pytest.raises(ExportError, match="does not match its hash"):
        export_site(fixture_runs, tmp_path / "site")


def test_export_refuses_a_review_made_on_other_verdicts(
    fixture_runs: Path, tmp_path: Path
) -> None:
    review = fixture_runs / "builder" / "menu" / "review-eval2.json"
    data = load(review)
    data["verdicts_sha256"] = "0" * 64
    review.write_text(json.dumps(data))

    with pytest.raises(ExportError, match="review-eval2.json in .* was made on other"):
        export_site(fixture_runs, tmp_path / "site")


def test_export_refuses_a_directory_that_is_not_empty(
    fixture_runs: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"
    out.mkdir()
    (out / "keep.txt").write_text("mine")

    with pytest.raises(ExportError, match="not empty"):
        export_site(fixture_runs, out)

    assert (out / "keep.txt").read_text() == "mine"


def test_cli_export_reports_the_pages_written(
    fixture_runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "site"

    code = main(["export", str(out), "--runs-dir", str(fixture_runs)])

    assert code == 0
    assert capsys.readouterr().out == (
        f"4 pages (1 W3C, 3 builder-made), eval table included: {out}\n"
    )
    assert (out / "data" / "index.json").is_file()


def test_cli_export_refuses_a_directory_that_is_not_empty(
    fixture_runs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "x").write_text("x")

    with pytest.raises(SystemExit) as caught:
        main(["export", str(tmp_path / "site"), "--runs-dir", str(fixture_runs)])

    assert caught.value.code == 2
    assert "not empty" in capsys.readouterr().err


def test_the_replay_server_serves_an_export_on_loopback_only(
    fixture_runs: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"
    export_site(fixture_runs, out)
    server = make_server(out, port=0)
    host, port = server.server_address[:2]
    try:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with urllib.request.urlopen(f"http://{host}:{port}/data/index.json") as reply:
            served = json.loads(reply.read())
        post = urllib.request.Request(f"http://{host}:{port}/", method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(post)
    finally:
        server.shutdown()
        server.server_close()

    assert host == "127.0.0.1"
    assert served == load(out / "data" / "index.json")
    assert caught.value.code == 501


def test_cli_serve_replay_refuses_a_directory_without_an_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["serve-replay", str(tmp_path)])

    assert caught.value.code == 2
    assert "no exported replay" in capsys.readouterr().err


def test_export_refuses_two_runs_with_one_name(
    fixture_runs: Path, tmp_path: Path
) -> None:
    shutil.copytree(fixture_runs / "builder" / "menu", fixture_runs / "eval" / "menu")

    with pytest.raises(ExportError, match="two runs named menu"):
        export_site(fixture_runs, tmp_path / "site")

    assert not (tmp_path / "site").exists()
