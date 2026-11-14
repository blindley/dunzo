import ast
import copy
from pathlib import Path

import pytest

from dunzo import scheduler
from dunzo.scheduler import SM2, Review, get_scheduler, validate_grade

NOW = 1_000_000.0
DAY = scheduler.SECONDS_PER_DAY


def run(grades, s=None, now=NOW):
    """Review a fresh card once per grade (one review per day); return every Review."""
    s = s or SM2()
    state, results = s.initial_state(), []
    for g in grades:
        r = s.review(state, g, now)
        results.append(r)
        state, now = r.state, r.due
    return results


# ------------------------------------------------------------------------------ basics


def test_initial_state():
    assert SM2().initial_state() == {"ease": 2.5, "interval": 0, "repetitions": 0, "lapses": 0}


def test_initial_state_is_a_fresh_dict_each_time():
    s = SM2()
    s.initial_state()["ease"] = 9
    assert s.initial_state()["ease"] == 2.5


@pytest.mark.parametrize("bad", [0, 5, -1, 3.0, "3", None, True])
def test_grade_validated(bad):
    with pytest.raises(ValueError, match="Grade"):
        validate_grade(bad)
    with pytest.raises(ValueError, match="Grade"):
        SM2().review(SM2().initial_state(), bad, NOW)


@pytest.mark.parametrize("g", [1, 2, 3, 4])
def test_valid_grades_accepted(g):
    assert validate_grade(g) == g


# ----------------------------------------------------------------------- SM-2 intervals


def test_good_sequence_1_6_then_ease_multiplied():
    r = run([3, 3, 3, 3])
    assert [x.state["interval"] for x in r] == [1, 6, 15, 38]  # 6*2.5, 15*2.5=37.5 -> 38
    assert [x.state["repetitions"] for x in r] == [1, 2, 3, 4]
    assert all(x.state["ease"] == 2.5 for x in r)  # Good keeps the ease


def test_due_is_now_plus_interval_days():
    r = SM2().review(SM2().initial_state(), 3, NOW)
    assert r.due == NOW + 1 * DAY
    r2 = SM2().review(r.state, 3, 5000.0)
    assert r2.due == 5000.0 + 6 * DAY


def test_easy_raises_ease_and_hard_lowers_it():
    assert run([4])[0].state["ease"] == 2.6
    assert run([2])[0].state["ease"] == pytest.approx(2.36)


def test_hard_still_counts_as_success():
    r = run([2, 2, 2])
    assert [x.state["repetitions"] for x in r] == [1, 2, 3]
    assert [x.state["interval"] for x in r][:2] == [1, 6]
    assert r[2].state["interval"] == round(6 * r[1].state["ease"])


def test_again_resets_and_counts_a_lapse_without_touching_ease():
    r = run([3, 3, 3, 1])
    last = r[-1].state
    assert (last["repetitions"], last["interval"], last["lapses"]) == (0, 1, 1)
    assert last["ease"] == r[2].state["ease"]
    assert r[-1].due == r[2].due + 1 * DAY


def test_again_on_a_new_card():
    s = run([1])[0].state
    assert s == {"ease": 2.5, "interval": 1, "repetitions": 0, "lapses": 1}


def test_recovery_after_a_lapse_restarts_at_1_then_6():
    r = run([3, 3, 3, 1, 3, 3])
    assert [x.state["interval"] for x in r[3:]] == [1, 1, 6]


def test_ease_never_below_minimum():
    r = run([3, 3] + [2] * 30)
    assert min(x.state["ease"] for x in r) == scheduler.SM2_MIN_EASE


def test_interval_always_grows_on_success_even_at_min_ease():
    r = run([3, 3] + [2] * 20)
    intervals = [x.state["interval"] for x in r]
    assert all(b > a for a, b in zip(intervals[1:], intervals[2:], strict=False))


# -------------------------------------------------------------------------------- purity


def test_state_is_not_mutated():
    s = SM2()
    state = {"ease": 2.5, "interval": 6, "repetitions": 2, "lapses": 0}
    before = copy.deepcopy(state)
    r = s.review(state, 3, NOW)
    assert state == before
    assert r.state is not state


def test_same_inputs_same_output():
    s, state = SM2(), {"ease": 2.1, "interval": 10, "repetitions": 4, "lapses": 1}
    assert s.review(state, 4, NOW) == s.review(state, 4, NOW)


def test_state_is_json_serializable():
    import json

    r = run([3, 4, 2, 1, 3])[-1]
    assert json.loads(json.dumps(r.state)) == r.state


def test_module_imports_nothing_from_db_or_web():
    tree = ast.parse(Path(scheduler.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"math", "dataclasses", "typing"}, imported


# ------------------------------------------------------------------------ bad state


@pytest.mark.parametrize(
    "state",
    [
        {},
        None,
        [],
        {"ease": 2.5, "interval": 1},  # repetitions missing
        {"ease": 1.0, "interval": 1, "repetitions": 1},  # ease below minimum
        {"ease": "x", "interval": 1, "repetitions": 1},
        {"ease": float("nan"), "interval": 1, "repetitions": 1},
        {"ease": 2.5, "interval": -1, "repetitions": 1},
        {"ease": 2.5, "interval": 1, "repetitions": -1},
        {"ease": 2.5, "interval": 1, "repetitions": True},
    ],
)
def test_malformed_state_rejected(state):
    with pytest.raises(ValueError, match="sm2 state"):
        SM2().review(state, 3, NOW)


def test_missing_lapses_is_tolerated():
    r = SM2().review({"ease": 2.5, "interval": 6, "repetitions": 2}, 1, NOW)
    assert r.state["lapses"] == 1


# ------------------------------------------------------------------------- registry


def test_registry():
    s = get_scheduler("sm2")
    assert isinstance(s, SM2) and s.name == "sm2"
    assert scheduler.DEFAULT_SCHEDULER in scheduler.SCHEDULERS
    with pytest.raises(ValueError, match="Unknown scheduler"):
        get_scheduler("fsrs")


def test_review_result_shape():
    r = SM2().review(SM2().initial_state(), 3, NOW)
    assert isinstance(r, Review) and isinstance(r.state, dict) and isinstance(r.due, float)
