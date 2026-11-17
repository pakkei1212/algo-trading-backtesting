"""Repeatable commands; the dashboard calls the same underlying functions."""

import argparse
import json
from pathlib import Path

from mnq_research.backtest import run_backtest
from mnq_research.config import load_config
from mnq_research.data import download_yahoo, import_csv, load_dataset, refresh_yahoo_archive


def main() -> None:
    parser = argparse.ArgumentParser(description="MNQ research (no live orders)")
    commands = parser.add_subparsers(dest="command", required=True)
    yahoo = commands.add_parser("download-yahoo", help="Download exploratory rolling MNQ history")
    yahoo.add_argument("output", type=Path)
    yahoo.add_argument("--interval", default="1h", choices=["5m", "15m", "30m", "1h"])
    yahoo.add_argument("--period", choices=["60d", "1mo", "3mo", "6mo", "1y", "2y"])
    refresh = commands.add_parser(
        "refresh-yahoo", help="Accumulate a fresh 60-day Yahoo MNQ 5-minute window"
    )
    refresh.add_argument("archive", type=Path, nargs="?", default=Path("data/yahoo-mnq-5m"))
    importer = commands.add_parser("import-csv", help="Import one dated MNQ contract")
    importer.add_argument("csv", type=Path)
    importer.add_argument("output", type=Path)
    importer.add_argument("--symbol", required=True)
    importer.add_argument("--source", required=True)
    importer.add_argument("--expiry", required=True)
    importer.add_argument("--interval", default="1h", choices=["5m", "15m", "30m", "1h"])
    mt5 = commands.add_parser("mt5", help="Local AMP MT5 discovery, audit and export")
    mt5_commands = mt5.add_subparsers(dest="mt5_command", required=True)
    discover_parser = mt5_commands.add_parser("discover")
    discover_parser.add_argument("--terminal-path", type=Path)
    audit_parser = mt5_commands.add_parser("audit")
    audit_parser.add_argument("--terminal-path", type=Path)
    audit_parser.add_argument("--symbols", required=True)
    audit_parser.add_argument("--start", required=True)
    audit_parser.add_argument("--end", required=True)
    audit_parser.add_argument("--archive", type=Path, default=Path("runs/amp-mt5"))
    export = mt5_commands.add_parser("export")
    export.add_argument("--audit", type=Path, required=True)
    export.add_argument("--out", type=Path, default=Path("data"))
    validate = commands.add_parser("validate-data")
    validate.add_argument("dataset", type=Path)
    run = commands.add_parser("backtest")
    run.add_argument("config", type=Path)
    run.add_argument("dataset", type=Path)
    run.add_argument("--output", type=Path, default=Path("runs"))
    args = parser.parse_args()
    try:
        if args.command == "mt5":
            from mnq_research.amp_mt5 import EXPIRY_DATES, audit, discover, publish, terminal

            if args.mt5_command == "export":
                print(publish(args.audit, args.out))
            else:
                with terminal(args.terminal_path) as (client, info):
                    if args.mt5_command == "discover":
                        print(json.dumps({**info, "symbols": discover(client)}, indent=2))
                    else:
                        symbols = args.symbols.split(",")
                        if any(symbol not in EXPIRY_DATES for symbol in symbols):
                            raise ValueError("Use a quarterly symbol with a reviewed CME expiry.")
                        failures = 0
                        for symbol in symbols:
                            try:
                                print(
                                    json.dumps(
                                        audit(
                                            client,
                                            info,
                                            symbol,
                                            args.start,
                                            args.end,
                                            args.archive / symbol,
                                        )
                                    )
                                )
                            except (ValueError, OSError) as error:
                                failures += 1
                                print(f"{symbol}: {error}")
                        if failures:
                            raise ValueError(f"{failures} AMP contract audits failed.")
        elif args.command == "download-yahoo":
            period = args.period or ("60d" if args.interval != "1h" else "1mo")
            print(download_yahoo(args.output, period, args.interval))
        elif args.command == "refresh-yahoo":
            archive, summary = refresh_yahoo_archive(args.archive)
            print(archive)
            print(json.dumps(summary, indent=2))
        elif args.command == "import-csv":
            print(
                import_csv(
                    args.csv,
                    args.output,
                    args.symbol,
                    args.source,
                    args.expiry,
                    args.interval,
                )
            )
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
