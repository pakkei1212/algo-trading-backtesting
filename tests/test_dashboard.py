"""Verify user actions against a temporary workspace, not real research files."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from test_research import dataset, settings

from mnq_research.cloud import import_upload
from mnq_research.config import save_config


def test_builder_dashboard_edits_groups_saves_and_runs(tmp_path: Path) -> None:
    from test_builder import builder_settings

    source = Path(__file__).resolve().parents[1] / "app.py"
    (tmp_path / "app.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    save_config(builder_settings(), tmp_path / "configs" / "trend_pullback.yaml")
    dataset(tmp_path / "data", [101, 101, 99, 101, 110, 111, 112, 113])
    app = AppTest.from_file(str(tmp_path / "app.py"), default_timeout=60).run()
    assert not app.exception
    assert next(s.value for s in app.selectbox if s.label == "Strategy construction") == (
        "Condition builder"
    )
    assert any("Long market filter" in item.value for item in app.markdown)
    next(s for s in app.selectbox if s.label == "Combine conditions").set_value("any").run()
    next(n for n in app.number_input if n.label == "Number of conditions").set_value(2).run()
    assert not app.exception
    next(button for button in app.button if button.label == "Save configuration").click().run()
    assert not app.exception
    from mnq_research.config import load_config

    saved = load_config(tmp_path / "configs" / "my_strategy.yaml")
    assert saved.builder and saved.builder.long
    assert saved.builder.long.market_filter.mode == "any"
    assert len(saved.builder.long.market_filter.conditions) == 2
    next(button for button in app.button if button.label == "Run backtest").click().run()
    assert not app.exception
    assert list((tmp_path / "runs").glob("*/summary.json"))
    next(button for button in app.button if button.label == "Next trade").click().run()
    assert not app.exception
    assert any("setup triggered after 1 observed bars" in item.value for item in app.markdown)
    # Changing a group never submits another backtest.
    count = len(list((tmp_path / "runs").glob("*/summary.json")))
    next(s for s in app.selectbox if s.label == "Combine conditions").set_value("all").run()
    assert not app.exception
    assert len(list((tmp_path / "runs").glob("*/summary.json"))) == count


def test_builder_cloud_yaml_and_reports_use_session_workspace(tmp_path, monkeypatch) -> None:
    from test_builder import builder_settings

    monkeypatch.setenv("MNQ_DEPLOYMENT_MODE", "cloud")
    source = Path(__file__).resolve().parents[1] / "app.py"
    (tmp_path / "app.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    save_config(builder_settings(), tmp_path / "configs" / "trend_pullback.yaml")
    app = AppTest.from_file(str(tmp_path / "app.py"), default_timeout=60).run()
    workspace = app.session_state["cloud_workspace"]
    try:
        from test_cloud import csv_bytes

        import_upload(
            csv_bytes(), workspace, "MNQH6", "test", "dated_contract", "2026-03-20T14:30:00Z"
        )
        app.run()
        next(button for button in app.button if button.label == "Save configuration").click().run()
        assert not app.exception
        assert (workspace.root / "configs" / "my_strategy.yaml").exists()
        assert not (tmp_path / "configs" / "my_strategy.yaml").exists()
        assert app.session_state["reviewed_config"]["schema_version"] == 2
        next(button for button in app.button if button.label == "Run backtest").click().run()
        assert not app.exception
        assert list((workspace.root / "runs").glob("*/summary.json"))
    finally:
        workspace.close()


def test_dashboard_can_run_and_save(tmp_path: Path) -> None:
    app_source = Path(__file__).resolve().parents[1] / "app.py"
    (tmp_path / "app.py").write_text(app_source.read_text(encoding="utf-8"), encoding="utf-8")
    save_config(settings(), tmp_path / "configs" / "baseline.yaml")
    dataset(tmp_path / "data", [103, 102, 101, 102, 103, 110, 111, 112])
    app = AppTest.from_file(str(tmp_path / "app.py"), default_timeout=60).run()
    assert not app.exception
    next(button for button in app.button if button.label == "Run backtest").click().run()
    assert not app.exception
    assert list((tmp_path / "runs").glob("*/summary.json"))
    next(button for button in app.button if button.label == "Save configuration").click().run()
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


def test_public_cloud_does_not_fetch_or_expose_yahoo_history(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MNQ_DEPLOYMENT_MODE", "cloud")
    source = Path(__file__).resolve().parents[1] / "app.py"
    (tmp_path / "app.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    save_config(settings(), tmp_path / "configs" / "baseline.yaml")
    def forbidden_fetch(*_args, **_kwargs):
        pytest.fail("Public cloud mode must not download Yahoo market data")

    monkeypatch.setattr("mnq_research.data.fetch_yahoo", forbidden_fetch)
    app = AppTest.from_file(str(tmp_path / "app.py"), default_timeout=60).run()
    assert not app.exception
    workspace = app.session_state["cloud_workspace"]
    try:
        assert not list((workspace.root / "data").glob("*/metadata.json"))
        assert not any("Yahoo" in button.label for button in app.button)
    finally:
        workspace.close()
    workspace.close()
