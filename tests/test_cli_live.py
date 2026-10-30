from pathlib import Path
from typing import Any

import pytest

from nomouse import cli
from nomouse.export import export_site


class FakeServer:
    server_address = ("0.0.0.0", 8080)

    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.closed = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[FakeServer]:
    servers: list[FakeServer] = []

    def make_server(root: Path, port: int, **kwargs: Any) -> FakeServer:
        servers.append(FakeServer({"root": root, "port": port, **kwargs}))
        return servers[-1]

    monkeypatch.setattr(cli, "make_server", make_server)
    return servers


@pytest.fixture
def site(fixture_runs: Path, tmp_path: Path) -> Path:
    export_site(fixture_runs, tmp_path / "site")
    return tmp_path / "site"


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    (tmp_path / "cache" / "bad" / "before").mkdir(parents=True)
    return tmp_path / "cache"


def serve_live(site: Path, tmp_path: Path, cache: Path, *extra: str) -> int:
    return cli.main(
        [
            "serve-replay",
            str(site),
            "--live",
            "--state-dir",
            str(tmp_path / "state"),
            "--cache",
            str(cache),
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            *extra,
        ]
    )


def test_serve_replay_live_wires_the_live_service(
    site: Path,
    tmp_path: Path,
    cache: Path,
    served: list[FakeServer],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("META_API_KEY", "LLM_test_value")

    code = serve_live(site, tmp_path, cache, "--trust-proxy")

    assert code == 0
    (server,) = served
    assert server.closed
    assert (server.kwargs["host"], server.kwargs["port"]) == ("0.0.0.0", 8080)
    assert server.kwargs["trust_proxy"] is True
    status = server.kwargs["live"].status()
    assert status["live"] is True
    assert status["cap_usd"] == 20.0
    assert len(status["pages"]) == 26
    out = capsys.readouterr().out
    assert "live mode on" in out
    assert "LLM_test_value" not in out


def test_without_a_key_live_mode_is_off_and_says_so(
    site: Path,
    tmp_path: Path,
    cache: Path,
    served: list[FakeServer],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("META_API_KEY", raising=False)

    serve_live(site, tmp_path, cache, "--cap-usd", "5")

    status = served[0].kwargs["live"].status()
    assert status["live"] is False
    assert status["cap_usd"] == 5.0
    assert "Live mode is off: no Meta Model API key" in capsys.readouterr().out


def test_serve_replay_without_live_serves_files_only(
    site: Path, served: list[FakeServer]
) -> None:
    cli.main(["serve-replay", str(site)])

    assert served[0].kwargs["live"] is None
    assert served[0].kwargs["host"] == "127.0.0.1"


@pytest.mark.parametrize(
    ("extra", "message"),
    [(["--cap-usd", "20.5"], "and USD 20"), (["--cache", "nowhere"], "mirror")],
)
def test_serve_replay_live_refuses_a_high_cap_or_no_mirror(
    site: Path,
    tmp_path: Path,
    cache: Path,
    served: list[FakeServer],
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    with pytest.raises(SystemExit):
        serve_live(site, tmp_path, cache, *extra)

    assert message in capsys.readouterr().err
    assert served == []
