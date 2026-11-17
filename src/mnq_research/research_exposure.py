"""Read the recorded uses of this archive across local studies."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def archive_exposures(
    repository: Path, dataset: Path, checksum: str, current: Path
) -> list[dict]:
    exposures = []
    for path in sorted((repository / "runs" / "optimization").glob("*/study.sqlite")):
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            spec = json.loads(db.execute("SELECT json FROM spec").fetchone()[0])
            if checksum not in spec.values() and Path(spec["dataset"]).name != dataset.name:
                continue
            for phase, count in db.execute("SELECT phase,count(*) FROM trials GROUP BY phase"):
                bounds = spec.get({"Training": "train", "Validation": "validation"}.get(phase, ""))
                if bounds:
                    exposures.append({
                        "study": path.parent.name, "use": phase, "bounds": bounds, "trials": count,
                    })
            holdout = db.execute("SELECT result FROM holdout").fetchone()
            if holdout:
                exposures.append({
                    "study": path.parent.name, "use": "Holdout unlocked",
                    "bounds": spec.get("holdout"), "trials": int(bool(holdout[0])),
                })
    for path in sorted((repository / "runs").glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("dataset", {}).get("study_checksum") != checksum:
            continue
        summary = json.loads(path.with_name("summary.json").read_text(encoding="utf-8"))
        exposures.append({
            "study": path.parent.name, "use": "Historical backtest",
            "bounds": [summary["first_bar"], summary["last_bar"]], "trials": 1,
        })
    for path in sorted((repository / "runs" / "cash-research").glob("*/summary.json")):
        if path.parent.resolve() == current.resolve():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        manifest_path = path.with_name("manifest.json")
        manifest = result.get("manifest")
        if manifest is None and manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not manifest or checksum not in {
            manifest.get("data_checksum"), manifest.get("dataset_checksum")
        }:
            continue
        for phase, bounds in manifest.get("windows", {}).items():
            exposures.append({
                "study": path.parent.name, "use": phase, "bounds": bounds,
            })
        for fold in result.get("folds", []):
            for phase, window in fold["windows"].items():
                exposures.append({
                    "study": f"{path.parent.name}/{fold['as_of']}",
                    "use": phase, "bounds": window["actual"],
                })
    return exposures
