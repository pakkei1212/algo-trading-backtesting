"""Verify user actions against a temporary workspace, not real research files."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from test_research import dataset, market_bars, settings

from mnq_research.cloud import import_upload
from mnq_research.config import save_config


def test_dashboard_can_run_and_save(tmp_path: Path) -> None:
    app_source = Path(__file__).resolve().parents[1] / "app.py"
    (tmp_path / "app.py").write_text(app_source.read_text(encoding="utf-8"), encoding="utf-8")
    save_config(settings(), tmp_path / "configs" / "baseline.yaml")
    dataset(tmp_path / "data", [103, 102, 101, 102, 103, 110, 111, 112])
    app = AppTest.from_file(str(tmp_path / "app.py"), default_timeout=60).run()
    assert not app.exception
    app.button[1].click().run()
    assert not app.exception
    assert list((tmp_path / "runs").glob("*/summary.json"))
    app.button[0].click().run()
    assert not app.exception
    assert (tmp_path / "configs" / "my_strategy.yaml").exists()

    run_folder = next((tmp_path / "runs").glob("*/summary.json")).parent
    run_count = len(list((tmp_path / "runs").glob("*/summary.json")))
    next(button for button in app.button if button.label == "Next trade").click().run()
    assert not app.exception
    assert app.session_state[f"selected:{run_folder.name}"]
    assert app.session_state[f"command:{run_folder.name}"]["kind"] == "trade"
    help_rows = [item for item in app.markdown if item.proto.help]
    assert len(help_rows) == 20
    assert next(item.proto.help for item in help_rows if item.value == "Profit factor") == (
        "Positive net P&L / absolute losses; — if no losses."
    )
    assert not any(item.label.endswith(" definitions") for item in app.expander)
    next(
        button for button in app.button if button.label == "Use settings for a new run"
    ).click().run()
    assert not app.exception
    assert app.session_state["draft_run"] == run_folder.name
    assert app.session_state["draft_config"]["fast_period"] == 2
    assert len(list((tmp_path / "runs").glob("*/summary.json"))) == run_count


def test_cloud_dashboard_isolates_sessions_and_clears_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MNQ_DEPLOYMENT_MODE", "cloud")

    def unavailable():
        raise OSError("provider unavailable in test")

    monkeypatch.setattr("mnq_research.cloud_ui.rolling_history", unavailable)
    source = Path(__file__).resolve().parents[1] / "app.py"
    (tmp_path / "app.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    save_config(settings(), tmp_path / "configs" / "baseline.yaml")
    # Desktop history must not appear in the hosted session's selectors.
    dataset(tmp_path / "data", [103, 102, 101, 102, 103, 110, 111, 112])
    first = AppTest.from_file(str(tmp_path / "app.py"), default_timeout=60).run()
    second = AppTest.from_file(str(tmp_path / "app.py"), default_timeout=60).run()
    assert not first.exception and not second.exception
    first_workspace = first.session_state["cloud_workspace"]
    second_workspace = second.session_state["cloud_workspace"]
    assert first_workspace.root != second_workspace.root
    assert not list((first_workspace.root / "data").iterdir())
    from test_cloud import csv_bytes

    import_upload(
        csv_bytes(), first_workspace, "MNQH6", "test only", "dated_contract", "2026-03-20T14:30:00Z"
    )
    first.run()
    next(button for button in first.button if button.label == "Run backtest").click().run()
    assert not first.exception
    assert list((first_workspace.root / "runs").glob("*/summary.json"))
    second.run()
    assert not second.exception
    assert not list((second_workspace.root / "runs").iterdir())
    assert not list((second_workspace.root / "data").iterdir())
    next(button for button in first.button if button.label == "Clear my session").click().run()
    assert not first.exception
    assert not first_workspace.root.exists()
    first.session_state["cloud_workspace"].close()
    second_workspace.close()


@pytest.mark.parametrize("first_failure", [False, True])
def test_cloud_preload_once_retry_and_preserve_upload(tmp_path, monkeypatch, first_failure) -> None:
    monkeypatch.setenv("MNQ_DEPLOYMENT_MODE", "cloud")
    source = Path(__file__).resolve().parents[1] / "app.py"
    (tmp_path / "app.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    save_config(settings(), tmp_path / "configs" / "baseline.yaml")
    calls = []

    def provider():
        calls.append(True)
        if first_failure and len(calls) == 1:
            raise OSError("temporary provider failure")
        return market_bars([100] * 8), {
            "kind": "rolling_proxy",
            "symbol": "MNQ=F",
            "source": "test provider",
        }

    monkeypatch.setattr("mnq_research.cloud_ui.rolling_history", provider)
    app = AppTest.from_file(str(tmp_path / "app.py"), default_timeout=60).run()
    assert not app.exception
    app.run()
    assert len(calls) == 1
    if first_failure:
        next(b for b in app.button if b.label == "Retry loading rolling MNQ data").click().run()
        assert len(calls) == 2 and not app.exception
    workspace = app.session_state["cloud_workspace"]
    assert len(list((workspace.root / "data").glob("*/metadata.json"))) == 1
    from test_cloud import csv_bytes

    uploaded = import_upload(
        csv_bytes(), workspace, "MNQH6", "user data", "dated_contract", "2026-03-20T14:30:00Z"
    )
    app.run()
    assert not app.exception and uploaded.exists()
    assert len(calls) == (2 if first_failure else 1)
    workspace.close()
