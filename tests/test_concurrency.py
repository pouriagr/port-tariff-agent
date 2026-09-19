"""Bounded parallelism: order, isolation and the worker cap."""

from __future__ import annotations

import threading
import time

from port_tariff_agent.concurrency import map_bounded


def test_results_follow_input_order_not_completion_order() -> None:
    def work(item: int) -> int:
        time.sleep(0.02 if item == 0 else 0.0)
        return item * 10

    successes, failures = map_bounded([0, 1, 2], work, max_workers=3)
    assert [item for item, _ in successes] == [0, 1, 2]
    assert [result for _, result in successes] == [0, 10, 20]
    assert failures == []


def test_failures_are_returned_and_the_rest_still_run() -> None:
    def work(item: int) -> int:
        if item == 1:
            raise ValueError("bad item")
        return item

    successes, failures = map_bounded([0, 1, 2], work, max_workers=2)
    assert [item for item, _ in successes] == [0, 2]
    assert [item for item, _ in failures] == [1]
    assert isinstance(failures[0][1], ValueError)


def test_workers_never_exceed_the_cap() -> None:
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def work(item: int) -> int:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.01)
        with lock:
            state["now"] -= 1
        return item

    map_bounded(list(range(12)), work, max_workers=3)
    assert state["peak"] <= 3


def test_callbacks_fire_for_each_outcome() -> None:
    seen: list[str] = []

    def work(item: int) -> int:
        if item == 1:
            raise ValueError("bad")
        return item

    map_bounded(
        [0, 1],
        work,
        max_workers=1,
        on_success=lambda item, _: seen.append(f"ok:{item}"),
        on_failure=lambda item, _: seen.append(f"fail:{item}"),
    )
    assert seen == ["ok:0", "fail:1"]


def test_empty_input_does_no_work() -> None:
    assert map_bounded([], lambda item: item, max_workers=4) == ([], [])
