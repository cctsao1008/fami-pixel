"""Stable authority-side intake for asynchronous planner responses.

Historical planner versions read each worker response file directly inside their
objective selectors.  The filesystem/IPC read itself is control-plane
orchestration, not COLLECT/PROGRESS policy.  This module provides one small,
behavior-neutral seam that preserves response-path order and ignores unavailable
or malformed payloads exactly as the historical selectors did.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Mapping


ResponseReader = Callable[[Path], Mapping | None]


def read_available_responses(
    response_paths: Iterable[Path],
    *,
    reader: ResponseReader,
) -> list[dict]:
    """Read currently available worker payloads in configured path order.

    ``None`` means that worker has not published a readable response yet.  The
    returned dictionaries are shallow copies so downstream cohort caches/policy
    may annotate or retain them without mutating the reader's object.
    """

    responses: list[dict] = []
    for raw_path in response_paths:
        path = Path(raw_path)
        payload = reader(path)
        if payload is None:
            continue
        if not isinstance(payload, Mapping):
            continue
        responses.append(dict(payload))
    return responses
