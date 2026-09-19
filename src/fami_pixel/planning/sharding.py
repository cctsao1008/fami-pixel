"""Pure deterministic worker sharding helpers."""

from __future__ import annotations

from typing import Iterable, TypeVar


T = TypeVar("T")


def shard_unique_items(
    items: Iterable[T],
    *,
    worker_index: int,
    worker_count: int,
) -> tuple[T, ...]:
    """Shard ordered items without duplicating work on surplus workers.

    Preserve V30's modulo contract exactly. A non-positive worker count is
    normalized to one, while out-of-range worker indices naturally receive an
    empty shard.
    """

    index = int(worker_index)
    count = max(1, int(worker_count))
    return tuple(
        item
        for position, item in enumerate(items)
        if position % count == index
    )
