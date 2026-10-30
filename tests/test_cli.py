import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from nomouse.cli import main
from nomouse.mirror import WALK_PAGES

PAGES_DIR = Path(__file__).parent / "pages"


def make_mirror(cache: Path) -> Path:
    root = cache / "bad"
    for page in WALK_PAGES:
        target = root / page
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((PAGES_DIR / "focus.html").read_text(encoding="utf-8"))
    return root


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "nomouse", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_walk_refuses_a_url_that_is_not_on_loopback(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["walk", "https://example.com/", "--out", str(tmp_path / "run")])

    assert caught.value.code == 2
    assert "loopback" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


def test_walk_refuses_the_news_pages(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    make_mirror(tmp_path)

    with pytest.raises(SystemExit) as caught:
        main(["walk", "before/news.html", "--cache", str(tmp_path)])

    assert caught.value.code == 2
    assert "before/survey.html" in capsys.readouterr().err


def test_walk_of_a_mirror_page_needs_the_mirror_first(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["walk", "before/survey.html", "--cache", str(tmp_path)])

    assert caught.value.code == 2
    assert "nomouse mirror" in capsys.readouterr().err


def test_walk_refuses_a_key_cap_below_one(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["walk", "http://127.0.0.1:9/", "--max-keys", "0"])

    assert caught.value.code == 2


def test_mirror_reuses_the_cache_and_lists_the_walk_pages(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = make_mirror(tmp_path)

    assert main(["mirror", "--cache", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert str(root) in output
    for page in WALK_PAGES:
        assert page in output
    assert "news" not in output


def test_walk_writes_a_record_for_a_loopback_url(site: str, tmp_path: Path) -> None:
    out = tmp_path / "run"

    result = run_cli("walk", site + "focus.html", "--out", str(out))

    assert result.returncode == 0, result.stderr
    record = json.loads((out / "walk.json").read_text(encoding="utf-8"))
    assert record["url"] == "/focus.html"
    assert record["end_reason"] == "cycle"
    assert str(out / "walk.json") in result.stdout


def test_walk_freezes_walker_verdicts_and_prints_their_hash(
    site: str, tmp_path: Path
) -> None:
    out = tmp_path / "run"

    result = run_cli("walk", site + "focus.html", "--out", str(out))

    assert result.returncode == 0, result.stderr
    digest = hashlib.sha256((out / "verdicts.json").read_bytes()).hexdigest()
    assert f"verdicts frozen: sha256 {digest}" in result.stdout
    assert (out / "verdicts.json.sha256").is_file()


def test_walk_refuses_a_run_directory_with_frozen_verdicts(
    site: str, tmp_path: Path
) -> None:
    out = tmp_path / "run"
    run_cli("walk", site + "focus.html", "--out", str(out))
    frozen = (out / "verdicts.json").read_bytes()
    record = (out / "walk.json").read_bytes()

    result = run_cli("walk", site + "blank.html", "--out", str(out))

    assert result.returncode == 2
    assert "already holds frozen verdicts" in result.stderr
    assert (out / "verdicts.json").read_bytes() == frozen
    assert (out / "walk.json").read_bytes() == record


def test_walk_serves_and_walks_a_mirror_page(tmp_path: Path) -> None:
    make_mirror(tmp_path / "cache")
    out = tmp_path / "run"

    result = run_cli(
        "walk",
        "before/survey.html",
        "--cache",
        str(tmp_path / "cache"),
        "--out",
        str(out),
    )

    assert result.returncode == 0, result.stderr
    record = json.loads((out / "walk.json").read_text(encoding="utf-8"))
    assert record["url"] == "/before/survey.html"
    assert record["keys"][:3] == ["Tab", "Enter", "Escape"]


def test_walk_serves_and_walks_a_builder_page(tmp_path: Path) -> None:
    out = tmp_path / "run"

    result = run_cli("walk", "builder/menu-focus-stays.html", "--out", str(out))

    assert result.returncode == 0, result.stderr
    record = json.loads((out / "walk.json").read_text(encoding="utf-8"))
    assert record["url"] == "/menu-focus-stays.html"
    assert "Enter" in record["keys"]
    assert (out / "verdicts.json").is_file()


@pytest.mark.parametrize(
    "target", ["builder/missing.html", "builder/../pyproject.toml", "builder/"]
)
def test_walk_refuses_a_builder_target_that_is_not_a_builder_page(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, target: str
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["walk", target, "--out", str(tmp_path / "run")])

    assert caught.value.code == 2
    assert "no builder page" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()
