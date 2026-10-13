"""Cloud boundaries protect sessions without changing trading behavior."""

import io
from zipfile import ZipFile

import pytest
from test_research import market_bars, settings

from mnq_research.cloud import (
    BACKTEST_LOCK,
    MAX_UPLOAD_BYTES,
    CloudWorkspace,
    import_upload,
    run_archive,
    run_cloud_backtest,
)
from mnq_research.data import load_dataset


def csv_bytes() -> bytes:
    frame = market_bars([103, 102, 101, 102, 103, 110, 111, 112])
    frame.index.name = "timestamp"
    return frame.to_csv().encode()


def upload(workspace: CloudWorkspace, **changes):
    return import_upload(
        **{
            "content": csv_bytes(),
            "workspace": workspace,
            "symbol": "MNQH6",
            "source": "test only",
            "kind": "dated_contract",
            "expiry": "2026-03-20T14:30:00Z",
            **changes,
        }
    )


def test_session_isolation_cleanup_and_archive() -> None:
    first, second = CloudWorkspace(), CloudWorkspace()
    try:
        data = upload(first)
        assert not list((second.root / "data").iterdir())
        with pytest.raises(ValueError, match="this session"):
            run_cloud_backtest(settings(), data, second)
        run = run_cloud_backtest(settings(), data, first)
        assert not list((second.root / "runs").iterdir())
        with ZipFile(io.BytesIO(run_archive(run))) as archive:
            assert f"{run.name}/bars.parquet" in archive.namelist()
            assert f"{run.name}/summary.json" in archive.namelist()
            assert f"{run.name}/config.yaml" in archive.namelist()
        replacement = upload(first)
        assert not data.exists()
        assert replacement.exists() and (run / "bars.parquet").exists()
    finally:
        first.close()
        second.close()
    assert not first.root.exists() and not second.root.exists()


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"content": b"x" * (MAX_UPLOAD_BYTES + 1)}, "10 MB"),
        ({"source": ""}, "source"),
        ({"content": csv_bytes().replace(b"+00:00", b"")}, "timezone"),
        ({"expiry": "2026-01-01T00:00:00Z"}, "expiration"),
        ({"symbol": "../MNQZ6"}, "dated MNQ"),
        ({"content": csv_bytes().replace(b"volume", b"other")}, "CSV requires"),
        ({"kind": "rolling_proxy", "symbol": "MNQ=F"}, "Acknowledge"),
    ],
)
def test_rejects_invalid_upload_without_writing(changes, message) -> None:
    workspace = CloudWorkspace()
    try:
        with pytest.raises(ValueError, match=message):
            upload(workspace, **changes)
        assert not list((workspace.root / "data").iterdir())
    finally:
        workspace.close()


def test_bar_limit_and_rolling_metadata() -> None:
    workspace = CloudWorkspace()
    try:
        content = market_bars([100] * 10_001).rename_axis("timestamp").to_csv().encode()
        with pytest.raises(ValueError, match="10,000"):
            upload(workspace, content=content)
        folder = upload(workspace, kind="rolling_proxy", symbol="MNQ=F", acknowledge_proxy=True)
        _, metadata = load_dataset(folder)
        assert metadata["kind"] == "rolling_proxy" and metadata["source"] == "test only"
        assert metadata["timestamp_semantics"] == "bar_close"
    finally:
        workspace.close()


def test_busy_failure_release_and_five_run_retention() -> None:
    workspace = CloudWorkspace()
    try:
        data = upload(workspace)
        BACKTEST_LOCK.acquire()
        try:
            with pytest.raises(ValueError, match="retry"):
                run_cloud_backtest(settings(), data, workspace)
        finally:
            BACKTEST_LOCK.release()
        for number in range(6):

            def successful(config, dataset, root, number=number):
                folder = root / f"run-{number}"
                folder.mkdir()
                (folder / "summary.json").write_text("{}")
                return folder

            run_cloud_backtest(settings(), data, workspace, successful)
        assert len(list((workspace.root / "runs").iterdir())) == 5
        assert not (workspace.root / "runs" / "run-0").exists()

        def failed(config, dataset, root):
            (root / "partial").mkdir()
            raise RuntimeError("simulated failure")

        with pytest.raises(RuntimeError):
            run_cloud_backtest(settings(), data, workspace, failed)
        assert len(list((workspace.root / "runs").iterdir())) == 5
        assert BACKTEST_LOCK.acquire(blocking=False)
        BACKTEST_LOCK.release()
    finally:
        workspace.close()
