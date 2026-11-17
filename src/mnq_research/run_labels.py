"""Human-readable labels for saved backtest runs."""

import json
from datetime import datetime
from pathlib import Path

import yaml


def _date(value: object) -> datetime | None:
    """Parse a saved ISO timestamp, returning None for missing legacy fields."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _date_range(first: object, last: object) -> str:
    start, end = _date(first), _date(last)
    if start is None or end is None:
        return "dates unavailable"
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}, {end.year}"
    if start.year == end.year:
        return f"{start.strftime('%b')} {start.day}–{end.strftime('%b')} {end.day}, {end.year}"
    first_label = f"{start.strftime('%b')} {start.day}, {start.year}"
    last_label = f"{end.strftime('%b')} {end.day}, {end.year}"
    return f"{first_label}–{last_label}"


def _strategy_name(config: dict) -> str:
    """Prefer an understandable strategy family and include its key periods."""
    if config.get("entry_rule") == "sma_cross":
        return f"SMA crossover {config.get('fast_period', '?')}/{config.get('slow_period', '?')}"
    name = str(config.get("name") or "Custom strategy")
    for prefix in ("MNQ hourly ", "MNQ "):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return name.split(" — ", 1)[0].strip() or "Custom strategy"


def _timeframe(value: object) -> str:
    """Expand common bar intervals so run names read naturally."""
    labels = {"5m": "5 min", "15m": "15 min", "30m": "30 min", "1h": "1 hour"}
    interval = str(value or "timeframe unknown")
    return labels.get(interval, interval)


def format_run_label(summary_path: Path) -> str:
    """Build a friendly selector label while retaining a short unique run ID."""
    run_id = summary_path.parent.name
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary = {}
    try:
        config_text = (summary_path.parent / "config.yaml").read_text(encoding="utf-8")
        config = yaml.safe_load(config_text) or {}
    except (OSError, yaml.YAMLError):
        config = {}

    trades = int(summary.get("closed_trades", 0))
    trade_label = f"{trades} trade" if trades == 1 else f"{trades} trades"
    status = "Halted" if summary.get("halted") else "Completed"
    symbol = summary.get("symbol") or "MNQ"
    interval = summary.get("bar_interval") or "timeframe unknown"
    suffix = run_id.rsplit("-", 1)[-1]
    has_hex_suffix = len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix.lower())
    short_id = suffix if has_hex_suffix else run_id
    label = (
        f"{_strategy_name(config)} · {symbol} · {_timeframe(interval)} · "
        f"{_date_range(summary.get('first_bar'), summary.get('last_bar'))} · "
        f"{trade_label} · {status} · {short_id}"
    )
    return label
