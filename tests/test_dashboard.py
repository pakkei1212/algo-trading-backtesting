"""Verify user actions against a temporary workspace, not real research files."""

from pathlib import Path

from streamlit.testing.v1 import AppTest
from test_research import dataset, settings

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
    next(
        button for button in app.button if button.label == "Use settings for a new run"
    ).click().run()
    assert not app.exception
    assert app.session_state["draft_run"] == run_folder.name
    assert app.session_state["draft_config"]["fast_period"] == 2
    assert len(list((tmp_path / "runs").glob("*/summary.json"))) == run_count
