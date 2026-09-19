"""Bounded parallel work.

Returns failures instead of raising, so a caller can record every failed item and report
them all at once rather than stopping at the first.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor


def map_bounded[T, R](
    items: Sequence[T],
    fn: Callable[[T], R],
    *,
    max_workers: int,
    on_success: Callable[[T, R], None] | None = None,
    on_failure: Callable[[T, BaseException], None] | None = None,
) -> tuple[list[tuple[T, R]], list[tuple[T, BaseException]]]:
    if not items:
        return [], []

    successes: list[tuple[T, R]] = []
    failures: list[tuple[T, BaseException]] = []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        futures = [pool.submit(fn, item) for item in items]
        for item, future in zip(items, futures, strict=True):
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - every failure is reported, not raised
                failures.append((item, exc))
                if on_failure is not None:
                    on_failure(item, exc)
            else:
                successes.append((item, result))
                if on_success is not None:
                    on_success(item, result)

    return successes, failures
