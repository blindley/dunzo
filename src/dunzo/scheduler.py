"""Scheduling: PURE functions that decide when a card is due next.

No database, web or clock access: the caller passes `now` (unix epoch seconds, UTC) and
stores whatever comes back. A scheduler owns the shape of its own `state` dict (kept as JSON
in `card_state.state`) and is registered by name (the `card_state.scheduler` column).

The contract every scheduler follows:

    initial_state() -> state        the state used for a card's first review
    review(state, grade, now) -> Review(state=new_state, due=...)

`grade` is 1-4 (1=Again, 2=Hard, 3=Good, 4=Easy). Inputs are never mutated.
"""

import math
from dataclasses import dataclass
from typing import Any, Protocol

SECONDS_PER_DAY = 86400.0

State = dict[str, Any]


@dataclass(frozen=True)
class Review:
    """The outcome of one review: the card's new scheduler state and when it is next due."""

    state: State
    due: float  # unix epoch seconds, UTC


class Scheduler(Protocol):
    name: str

    def initial_state(self) -> State: ...

    def review(self, state: State, grade: int, now: float) -> Review: ...


def validate_grade(grade: Any) -> int:
    """Check a grade (an integer 1-4). Raises ValueError otherwise."""
    if isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 4:
        raise ValueError("Grade must be an integer from 1 (Again) to 4 (Easy).")
    return grade


# ------------------------------------------------------------------------------- SM-2

SM2_START_EASE = 2.5
SM2_MIN_EASE = 1.3
# Our 1-4 grades as SM-2's 0-5 quality scale. Quality below 3 is a failed recall.
_SM2_QUALITY = {1: 2, 2: 3, 3: 4, 4: 5}


class SM2:
    """The SuperMemo-2 algorithm.

    State: {"ease": float, "interval": int (days), "repetitions": int, "lapses": int}.

    - Failed recall (Again): repetitions reset to 0, the interval becomes 1 day and the ease
      is left alone (as in the original algorithm). The lapse is counted.
    - Successful recall: the interval is 1 day after the first success, 6 days after the
      second, then the previous interval times the ease (rounded, always at least a day
      longer than before). The ease then moves by the usual SM-2 formula for the quality
      (Hard lowers it, Good keeps it, Easy raises it) and never drops below 1.3.
    """

    name = "sm2"

    def initial_state(self) -> State:
        return {"ease": SM2_START_EASE, "interval": 0, "repetitions": 0, "lapses": 0}

    def review(self, state: State, grade: int, now: float) -> Review:
        grade = validate_grade(grade)
        ease, interval, repetitions, lapses = self._read(state)
        quality = _SM2_QUALITY[grade]

        if quality < 3:
            repetitions = 0
            interval = 1
            lapses += 1
        else:
            if repetitions == 0:
                interval = 1
            elif repetitions == 1:
                interval = 6
            else:
                interval = max(interval + 1, round(interval * ease))
            repetitions += 1
            miss = 5 - quality
            ease = max(SM2_MIN_EASE, ease + 0.1 - miss * (0.08 + miss * 0.02))

        new_state = {
            "ease": round(ease, 4),
            "interval": interval,
            "repetitions": repetitions,
            "lapses": lapses,
        }
        return Review(state=new_state, due=now + interval * SECONDS_PER_DAY)

    @staticmethod
    def _read(state: State) -> tuple[float, int, int, int]:
        try:
            ease = state["ease"]
            interval = state["interval"]
            repetitions = state["repetitions"]
            lapses = state.get("lapses", 0)
        except (KeyError, TypeError, AttributeError):
            raise ValueError("Not a valid sm2 state.") from None
        numbers_ok = (
            all(isinstance(v, int | float) and not isinstance(v, bool) for v in (ease, interval))
            and all(isinstance(v, int) and not isinstance(v, bool) for v in (repetitions, lapses))
            and math.isfinite(ease)
            and ease >= SM2_MIN_EASE
            and interval >= 0
            and repetitions >= 0
            and lapses >= 0
        )
        if not numbers_ok:
            raise ValueError("Not a valid sm2 state.")
        return float(ease), int(interval), repetitions, lapses


# ---------------------------------------------------------------------------- registry

SCHEDULERS: dict[str, Scheduler] = {SM2.name: SM2()}
DEFAULT_SCHEDULER = SM2.name


def get_scheduler(name: str) -> Scheduler:
    """Look up a scheduler by the name stored in `card_state.scheduler`."""
    try:
        return SCHEDULERS[name]
    except KeyError:
        raise ValueError(f"Unknown scheduler “{name}”.") from None
