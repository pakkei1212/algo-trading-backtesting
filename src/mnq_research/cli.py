"""Repeatable commands; the dashboard calls the same underlying functions."""

import argparse
import json
from pathlib import Path

from mnq_research.backtest import run_backtest
from mnq_research.config import load_config
from mnq_research.data import download_yahoo, import_csv, load_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="MNQ research (no live orders)")
    commands = parser.add_subparsers(dest="command", required=True)
    yahoo = commands.add_parser("download-yahoo", help="Download exploratory rolling MNQ history")
    yahoo.add_argument("output", type=Path)
    yahoo.add_argument("--period", default="1mo", choices=["1mo", "3mo", "6mo", "1y", "2y"])
    importer = commands.add_parser("import-csv", help="Import one dated MNQ contract")
    importer.add_argument("csv", type=Path)
    importer.add_argument("output", type=Path)
    importer.add_argument("--symbol", required=True)
    importer.add_argument("--source", required=True)
    importer.add_argument("--expiry", required=True)
    validate = commands.add_parser("validate-data")
    validate.add_argument("dataset", type=Path)
    run = commands.add_parser("backtest")
    run.add_argument("config", type=Path)
    run.add_argument("dataset", type=Path)
    run.add_argument("--output", type=Path, default=Path("runs"))
    args = parser.parse_args()
    try:
        if args.command == "download-yahoo":
            print(download_yahoo(args.output, args.period))
        elif args.command == "import-csv":
            print(import_csv(args.csv, args.output, args.symbol, args.source, args.expiry))
        elif args.command == "validate-data":
            _, metadata = load_dataset(args.dataset)
            print(json.dumps(metadata, indent=2))
        elif args.command == "backtest":
            folder = run_backtest(load_config(args.config), args.dataset, args.output)
            print(folder)
            print((folder / "summary.json").read_text(encoding="utf-8"))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
