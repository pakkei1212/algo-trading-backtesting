"""Small rule plugins: only completed bars are supplied to these functions."""

from collections.abc import Callable


def sma_cross(previous_fast: float, previous_slow: float, fast: float, slow: float) -> int:
    if previous_fast <= previous_slow and fast > slow:
        return 1
    if previous_fast >= previous_slow and fast < slow:
        return -1
    return 0


ENTRY_RULES: dict[str, Callable[[float, float, float, float], int]] = {"sma_cross": sma_cross}
