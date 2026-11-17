"""Deterministic Optuna proposals over the optimizer's finite candidate space."""

from collections.abc import Sequence

import optuna
from optuna.study import Study
from optuna.trial import FrozenTrial, Trial, TrialState

from mnq_research.config import StrategyConfig
from mnq_research.tpe_ranges import RangeCandidates


def _constraint(trial: FrozenTrial) -> tuple[float]:
    # Optuna can also invoke this callback for a skipped duplicate.
    return (0.0 if trial.user_attrs.get("qualified", False) else 1.0,)


class TpeSearch:
    def __init__(self, candidates: Sequence[StrategyConfig] | RangeCandidates, seed: int) -> None:
        self.numeric = candidates if isinstance(candidates, RangeCandidates) else None
        self.atr_values = (
            [] if self.numeric else sorted({candidate.atr_multiple for candidate in candidates})
        )
        self.width = len(self.atr_values)
        self.count = len(candidates)
        self.seen: set[int] = set()
        self.study: Study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=seed, constraints_func=_constraint),
        )

    def ask(self) -> tuple[int, Trial]:
        """Avoid simulating duplicates and guarantee progress near exhaustion."""
        if len(self.seen) >= self.count:
            raise ValueError("All TPE candidates have been evaluated.")
        for attempt in range(9):
            if attempt == 8:
                # After eight repeated suggestions, explicitly queue an unseen pair.
                unseen = next(index for index in range(self.count) if index not in self.seen)
                self.study.enqueue_trial(self._parameters(unseen))
            trial = self.study.ask()
            index = self._suggest(trial)
            if index is not None and index not in self.seen:
                return index, trial
            self.study.tell(trial, state=TrialState.PRUNED)
        raise RuntimeError("TPE did not return the queued candidate.")

    def _parameters(self, index: int) -> dict:
        if self.numeric is not None:
            fast, slow, atr = self.numeric.parameters(index)
            return {"fast_period": fast, "slow_period": slow, "atr_multiple": atr}
        pair, atr = divmod(index, self.width)
        return {"sma_pair": pair, "atr_multiple": self.atr_values[atr]}

    def _suggest(self, trial: Trial) -> int | None:
        if self.numeric is not None:
            ranges = self.numeric.ranges
            fast = trial.suggest_int("fast_period", ranges.fast_min, ranges.fast_max)
            slow = trial.suggest_int("slow_period", ranges.slow_min, ranges.slow_max)
            atr = trial.suggest_float(
                "atr_multiple",
                float(ranges.atr_min),
                float(ranges.atr_max),
                step=float(ranges.atr_step),
            )
            return self.numeric.index_for(fast, slow, atr)
        pair = trial.suggest_categorical("sma_pair", list(range(self.count // self.width)))
        atr = trial.suggest_categorical("atr_multiple", self.atr_values)
        return pair * self.width + self.atr_values.index(atr)

    def tell(self, index: int, trial: Trial, score: float | None) -> None:
        """Pass training results only, with the same qualification used for ranking."""
        trial.set_user_attr("qualified", score is not None)
        self.study.tell(trial, score if score is not None else 0.0)
        self.seen.add(index)

    def replay(self, rows: list[dict]) -> None:
        """Reconstruct sampler and RNG state from committed trial results."""
        for row in sorted(rows, key=lambda result: result["search_order"]):
            index, trial = self.ask()
            if index != row["candidate"]:
                raise ValueError("TPE replay differs from the saved study. Start a new study.")
            self.tell(index, trial, row["score"])
